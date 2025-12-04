#!/usr/bin/env python3
"""
palaver/recorder/async_vad_recorder.py
Async/await VAD recorder for non-blocking UI integration

Architecture:
  Sync audio thread (sounddevice callback)
    → asyncio.Queue (thread-safe)
    → Async event processor
    → Async transcription & text processing
"""

import asyncio
import sys
import time
import wave
import numpy as np
import torch
from datetime import datetime, timezone
from pathlib import Path
from dataclasses import dataclass
from typing import Optional, Callable
from scipy.signal import resample_poly

# Import modular components
from palaver.recorder.audio_sources import AudioSource, create_audio_source
from palaver.recorder.session import Session

# ================== CONFIG ==================
RECORD_SR = 48000
VAD_SR = 16000
DEVICE = "hw:1,0"
CHUNK_SEC = 0.03
CHUNK_SIZE = int(CHUNK_SEC * RECORD_SR)

VAD_THRESHOLD = 0.5          # Normal mode threshold
VAD_THRESHOLD_LONG = 0.7     # Long note mode: higher threshold to ignore ambient noise
MIN_SILENCE_MS = 800         # Normal mode: 0.8 seconds
MIN_SILENCE_MS_LONG = 5000   # Long note mode: 5 seconds
SPEECH_PAD_MS = 1300
MIN_SEG_SEC = 1.2

# Transcription settings
NUM_WORKERS = 2
WHISPER_MODEL = "models/multilang_whisper_large3_turbo.ggml"
WHISPER_TIMEOUT = 60


# ================== EVENT TYPES ==================

@dataclass
class AudioEvent:
    """Base class for events from audio callback"""
    timestamp: float


@dataclass
class SpeechStarted(AudioEvent):
    """Speech segment started"""
    segment_index: int
    vad_mode: str  # "normal" or "long_note"


@dataclass
class SpeechEnded(AudioEvent):
    """Speech segment ended"""
    segment_index: int
    audio_data: np.ndarray
    duration_sec: float
    kept: bool  # True if segment met minimum duration


@dataclass
class ModeChangeRequested(AudioEvent):
    """VAD mode change requested (will apply at next segment boundary)"""
    requested_mode: str


@dataclass
class AudioChunk(AudioEvent):
    """Raw audio chunk (used for real-time monitoring if needed)"""
    data: np.ndarray
    in_speech: bool


@dataclass
class RecordingStateChanged(AudioEvent):
    """Recording started or stopped"""
    is_recording: bool


@dataclass
class VADModeChanged(AudioEvent):
    """VAD mode changed (normal/long_note)"""
    mode: str
    min_silence_ms: int


@dataclass
class TranscriptionQueued(AudioEvent):
    """Segment queued for transcription"""
    segment_index: int
    wav_path: Path
    duration_sec: float


@dataclass
class TranscriptionComplete(AudioEvent):
    """Transcription finished"""
    segment_index: int
    text: str
    success: bool
    processing_time_sec: float
    error_msg: Optional[str] = None


@dataclass
class NoteCommandDetected(AudioEvent):
    """'start new note' command detected in transcription"""
    segment_index: int


@dataclass
class NoteTitleCaptured(AudioEvent):
    """Note title captured (segment after 'start new note')"""
    segment_index: int
    title: str


@dataclass
class QueueStatus(AudioEvent):
    """Processing queue status update"""
    queued_jobs: int
    completed_transcriptions: int
    total_segments: int


# ================== VAD MANAGEMENT ==================

print("Loading Silero VAD...")
_vad_model, _vad_utils = torch.hub.load(
    'snakers4/silero-vad',
    'silero_vad',
    trust_repo=True,
    verbose=False
)
_VADIterator = _vad_utils[3]
print("VAD ready.")


def create_vad(mode="normal"):
    """
    Create VAD iterator with specified silence duration and threshold.

    In long_note mode, we use a higher threshold (0.7 instead of 0.5) to ignore
    ambient noise and allow true silence detection. This is critical for microphone
    input where background noise can prevent the 5-second silence from being detected.

    Args:
        mode: "normal" or "long_note"

    Returns:
        VADIterator instance configured for the specified mode
    """
    if mode == "long_note":
        silence_ms = MIN_SILENCE_MS_LONG
        threshold = VAD_THRESHOLD_LONG
    else:
        silence_ms = MIN_SILENCE_MS
        threshold = VAD_THRESHOLD

    print(f"[DEBUG] Creating VAD: mode={mode}, silence_threshold={silence_ms}ms, vad_threshold={threshold}")
    return _VADIterator(
        _vad_model,
        threshold=threshold,
        sampling_rate=VAD_SR,
        min_silence_duration_ms=silence_ms,
        speech_pad_ms=SPEECH_PAD_MS
    )


def downsample_to_512(chunk: np.ndarray) -> np.ndarray:
    """Downsample to exactly 512 samples @ 16 kHz for VAD"""
    down = resample_poly(chunk, VAD_SR, RECORD_SR)
    if down.shape[0] > 512:
        down = down[:512]
    elif down.shape[0] < 512:
        down = np.pad(down, (0, 512 - down.shape[0]))
    return down.astype(np.float32)


# ================== ASYNC VAD RECORDER ==================

class AsyncVADRecorder:
    """
    Async VAD-based voice recorder.

    Coordinates audio input, VAD processing, transcription, and text processing
    in a non-blocking async architecture.

    Usage:
        recorder = AsyncVADRecorder()
        await recorder.start_recording()
        # ... recording runs in background ...
        session_dir = await recorder.stop_recording()

    Event Callback:
        recorder = AsyncVADRecorder(event_callback=my_handler)

        The event_callback will be called with AudioEvent instances for:
        - RecordingStateChanged: Recording started/stopped
        - VADModeChanged: Mode switched (normal/long_note)
        - SpeechStarted/SpeechEnded: Speech segment detected
        - TranscriptionQueued/TranscriptionComplete: Transcription lifecycle
        - NoteCommandDetected/NoteTitleCaptured: Note workflow events
        - QueueStatus: Processing queue updates

        Callback can be sync or async function.
    """

    def __init__(self, event_callback: Optional[Callable] = None):
        # Event callback for consumers (TUI, monitoring, etc.)
        self.event_callback = event_callback

        # Event loop and queue
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.event_queue: Optional[asyncio.Queue] = None

        # Recording state
        self.is_recording = False
        self.session: Optional[Session] = None
        self.session_dir: Optional[Path] = None

        # Audio input
        self.audio_source: Optional[AudioSource] = None

        # VAD state (accessed from audio thread)
        self.vad = None
        self.vad_mode = "normal"
        self.vad_mode_requested = None
        self.in_speech = False
        self.segments = []  # List of audio chunks per segment
        self.kept_segment_indices = []

        # Async tasks
        self.event_processor_task: Optional[asyncio.Task] = None
        self.transcription_processor_task: Optional[asyncio.Task] = None

        # Transcriber and text processor (will be created in start_recording)
        self.transcriber = None
        self.text_processor = None

    async def _emit_event(self, event: AudioEvent):
        """
        Emit event to callback if provided.

        Handles both sync and async callbacks.
        Safe to call from any context.
        """
        if self.event_callback:
            try:
                if asyncio.iscoroutinefunction(self.event_callback):
                    await self.event_callback(event)
                else:
                    self.event_callback(event)
            except Exception as e:
                # Don't let callback errors crash the recorder
                print(f"[Recorder] Event callback error: {e}", file=sys.stderr)

    def _emit_event_threadsafe(self, event: AudioEvent):
        """
        Emit event from audio thread (thread-safe).

        Called from audio callback which runs in a different thread.
        Schedules emission in the main event loop.
        """
        if self.event_callback and self.loop:
            try:
                asyncio.run_coroutine_threadsafe(
                    self._emit_event(event),
                    self.loop
                )
            except Exception as e:
                print(f"[Recorder] Event emission error: {e}", file=sys.stderr)

    def _emit_event_from_text_processor(self, event: AudioEvent):
        """
        Emit event from text processor thread (thread-safe).

        Called from TextProcessor thread when transcription completes or
        commands are detected. Forwards events to the main event callback.

        Args:
            event: AudioEvent instance (TranscriptionComplete, NoteCommandDetected, etc.)
        """
        if self.event_callback and self.loop:
            try:
                # Schedule the event emission in the main event loop
                asyncio.run_coroutine_threadsafe(
                    self._emit_event(event),
                    self.loop
                )
            except Exception as e:
                print(f"[Recorder] Text processor event error: {e}", file=sys.stderr)

    async def start_recording(
        self,
        input_source: Optional[str] = None,
        session: Optional[Session] = None
    ) -> None:
        """
        Start recording session.

        Args:
            input_source: Device name or file path (None = use DEVICE constant)
            session: Optional pre-created Session (None = create new)
        """
        if self.is_recording:
            raise RuntimeError("Already recording")

        print("\n" + "="*70)
        print("Starting async recording session...")
        print("="*70)

        # Store event loop for audio callback
        self.loop = asyncio.get_running_loop()

        # Create event queue
        self.event_queue = asyncio.Queue()

        # Create or use provided session
        if session is None:
            self.session = Session()
            self.session_dir = self.session.create()
        else:
            self.session = session
            self.session_dir = self.session.get_path()

        # Create audio source
        if input_source is None:
            input_source = DEVICE

        self.audio_source = create_audio_source(
            input_spec=input_source,
            samplerate=RECORD_SR,
            blocksize=CHUNK_SIZE,
            channels=2
        )

        # Store metadata
        from palaver.recorder.audio_sources import FileAudioSource
        is_file_input = isinstance(self.audio_source, FileAudioSource)
        self.session.add_metadata("input_source", {
            "type": "file" if is_file_input else "device",
            "source": str(input_source)
        })
        self.session.add_metadata("num_workers", NUM_WORKERS)

        print(f"Input source: {'FILE' if is_file_input else 'DEVICE'} ({input_source})")

        # Initialize VAD
        self.vad = create_vad("normal")
        self.vad_mode = "normal"
        self.vad_mode_requested = None
        self.in_speech = False
        self.segments.clear()
        self.kept_segment_indices.clear()

        # Start transcriber
        from palaver.recorder.transcription import WhisperTranscriber
        self.transcriber = WhisperTranscriber(
            num_workers=NUM_WORKERS,
            model_path=WHISPER_MODEL,
            timeout=WHISPER_TIMEOUT
        )
        self.transcriber.start()

        # Start text processor with event callback
        from palaver.recorder.text_processor import TextProcessor
        self.text_processor = TextProcessor(
            session_dir=self.session_dir,
            result_queue=self.transcriber.get_result_queue(),
            mode_change_callback=self._handle_mode_change_request,
            event_callback=self._emit_event_from_text_processor
        )
        self.text_processor.start()

        # Start event processor task
        self.event_processor_task = asyncio.create_task(self._process_events())

        # Start audio stream with callback
        self.audio_source.start(self._audio_callback)

        self.is_recording = True
        print("Recording started")

        # Emit recording started event
        await self._emit_event(RecordingStateChanged(
            timestamp=time.time(),
            is_recording=True
        ))

    async def stop_recording(self) -> Path:
        """
        Stop recording session and finalize.

        Returns:
            Path to session directory
        """
        if not self.is_recording:
            raise RuntimeError("Not recording")

        print("\nStopping recording...")
        self.is_recording = False

        # Stop audio stream
        if self.audio_source:
            self.audio_source.stop()

        # Give audio callback time to finish
        await asyncio.sleep(0.5)

        # Push sentinel to event queue to stop processor
        await self.event_queue.put(None)

        # Wait for event processor to finish
        if self.event_processor_task:
            await self.event_processor_task

        # Check for unfinished segment
        if self.in_speech and self.segments and self.segments[-1]:
            seg = np.concatenate(self.segments[-1])
            dur = len(seg) / RECORD_SR
            print(f"\n[Warning: Unfinished segment: {dur:.2f}s]")
            if dur >= MIN_SEG_SEC:
                print(f"  → KEPT")
                await self._save_and_queue_segment(len(self.segments) - 1, seg)
                self.kept_segment_indices.append(len(self.segments) - 1)
            else:
                print(f"  → DISCARDED (< {MIN_SEG_SEC}s)")
                self.segments.pop()

        # Finalize transcription and text processing
        total_kept_segments = len([s for s in self.segments if s])
        print(f"\nFinal segment count: {total_kept_segments}")
        print("Waiting for transcriptions to complete...")

        self.transcriber.stop()
        self.text_processor.stop()
        self.text_processor.finalize(total_kept_segments)

        # Write manifest
        segment_info = [
            {
                "index": i,
                "file": f"seg_{i:04d}.wav",
                "duration_sec": round(len(np.concatenate(self.segments[i]))/RECORD_SR, 3)
            }
            for i in self.kept_segment_indices
        ]
        self.session.write_manifest(
            segments=segment_info,
            total_segments=total_kept_segments,
            samplerate=RECORD_SR
        )

        print(f"\nFinished! → {self.session_dir}")
        print(f"   • {total_kept_segments} speech segments created")
        print(f"   • Check transcript_incremental.txt for real-time results")
        print(f"   • transcript_raw.txt ready for Phase 2")

        # Emit recording stopped event
        await self._emit_event(RecordingStateChanged(
            timestamp=time.time(),
            is_recording=False
        ))

        return self.session_dir

    async def wait_for_completion(self):
        """
        Wait for recording to complete (for file input mode).

        This is a no-op for microphone mode (caller must call stop_recording).
        For file mode, waits until audio source finishes.
        """
        from palaver.recorder.audio_sources import FileAudioSource
        if isinstance(self.audio_source, FileAudioSource):
            # File mode - wait for completion
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None,
                self.audio_source.wait_for_completion
            )
            print("File processing complete")

    def _audio_callback(self, indata, frames, time_info, status):
        """
        Audio callback (runs in audio thread - MUST be fast and synchronous).

        Processes audio with VAD and pushes events to async queue.
        """
        # Extract mono channel
        chunk = indata[:, 0].copy()

        # Downsample for VAD
        vad_chunk = downsample_to_512(chunk)

        # Run VAD (fast, synchronous)
        window = self.vad(vad_chunk, return_seconds=False)

        # Handle VAD events
        if window:
            if window.get("start") is not None:
                # Apply queued mode change at segment boundary
                self._apply_vad_mode_change()

                # Start new segment
                self.in_speech = True
                self.segments.append([])
                segment_index = len(self.segments) - 1

                # Push event to async queue
                event = SpeechStarted(
                    timestamp=time.time(),
                    segment_index=segment_index,
                    vad_mode=self.vad_mode
                )
                self._push_event(event)

                mode_indicator = " [LONG NOTE]" if self.vad_mode == "long_note" else ""
                print(f"\n[Speech start{mode_indicator}]", end=" ", flush=True)

            if window.get("end") is not None:
                # End segment
                self.in_speech = False

                if self.segments and self.segments[-1]:
                    seg = np.concatenate(self.segments[-1])
                    dur = len(seg) / RECORD_SR
                    num_chunks = len(self.segments[-1])
                    segment_index = len(self.segments) - 1

                    print(f"\n[Speech end: {num_chunks} chunks, {dur:.2f}s]", end=" ", flush=True)

                    # Check minimum duration
                    kept = dur >= MIN_SEG_SEC

                    if kept:
                        print(f"✓ Segment #{len(self.segments)} KEPT", flush=True)
                        # Push event with audio data
                        event = SpeechEnded(
                            timestamp=time.time(),
                            segment_index=segment_index,
                            audio_data=seg,
                            duration_sec=dur,
                            kept=True
                        )
                        self._push_event(event)

                        # If in long note mode, queue switch back to normal
                        if self.vad_mode == "long_note":
                            self._switch_vad_mode("normal")
                            print("\n" + "="*70)
                            print("🎙️  WILL RESTORE NORMAL MODE after this segment")
                            print("Silence threshold: 0.8 seconds")
                            print("="*70 + "\n")
                    else:
                        print(f"✗ DISCARDED (< {MIN_SEG_SEC}s)", flush=True)
                        self.segments.pop()
                        event = SpeechEnded(
                            timestamp=time.time(),
                            segment_index=segment_index,
                            audio_data=seg,
                            duration_sec=dur,
                            kept=False
                        )
                        self._push_event(event)

        # Accumulate audio while in speech
        if self.in_speech:
            if not self.segments:
                self.segments.append([])
            self.segments[-1].append(chunk)

        # Visual indicator
        mode_char = "L" if self.vad_mode == "long_note" else "S"
        print(mode_char if self.in_speech else ".", end="", flush=True)

    def _push_event(self, event: AudioEvent):
        """
        Push event to async queue (thread-safe).

        Called from audio callback (different thread).
        """
        if self.loop and self.event_queue:
            # Thread-safe: schedule coroutine in event loop
            asyncio.run_coroutine_threadsafe(
                self.event_queue.put(event),
                self.loop
            )

    def _apply_vad_mode_change(self):
        """
        Apply queued VAD mode change (called at segment boundaries only).

        Called from audio callback (sync).
        """
        if self.vad_mode_requested and self.vad_mode_requested != self.vad_mode:
            old_mode = self.vad_mode
            self.vad_mode = self.vad_mode_requested
            self.vad_mode_requested = None
            self.vad = create_vad(self.vad_mode)
            print(f"\n[VAD] Mode changed to: {self.vad_mode}")

            # Emit mode changed event (thread-safe)
            silence_ms = MIN_SILENCE_MS_LONG if self.vad_mode == "long_note" else MIN_SILENCE_MS
            self._emit_event_threadsafe(VADModeChanged(
                timestamp=time.time(),
                mode=self.vad_mode,
                min_silence_ms=silence_ms
            ))

    def _switch_vad_mode(self, new_mode: str):
        """
        Request VAD mode change (will be applied at next segment boundary).

        Also emits VADModeChanged event immediately so TUI can show feedback
        even if user doesn't speak again (which would trigger the actual mode change).

        Called from audio callback (sync).
        """
        if new_mode != self.vad_mode:
            self.vad_mode_requested = new_mode
            print(f"\n[VAD] Mode change queued: {new_mode} (will apply after current segment)")

            # Emit event immediately so TUI shows "Note complete" notification
            # The actual mode change will happen at next segment boundary
            silence_ms = MIN_SILENCE_MS_LONG if new_mode == "long_note" else MIN_SILENCE_MS
            self._emit_event_threadsafe(VADModeChanged(
                timestamp=time.time(),
                mode=new_mode,
                min_silence_ms=silence_ms
            ))

    def _handle_mode_change_request(self, mode: str):
        """
        Handle mode change request from text processor.

        This is called from text processor thread when title is captured.
        We need to apply the mode change IMMEDIATELY so the current segment
        (the note body that's being spoken right now) uses the long_note threshold.

        This is thread-safe because we're just setting attributes that the
        audio callback will read on its next iteration.
        """
        if mode != self.vad_mode:
            self.vad_mode = mode
            self.vad = create_vad(mode)
            print(f"\n[VAD] Mode changed IMMEDIATELY to: {mode}")
            print(f"      (Applied mid-segment for current speech)")

            # Emit mode changed event
            silence_ms = MIN_SILENCE_MS_LONG if mode == "long_note" else MIN_SILENCE_MS
            if self.loop:
                asyncio.run_coroutine_threadsafe(
                    self._emit_event(VADModeChanged(
                        timestamp=time.time(),
                        mode=mode,
                        min_silence_ms=silence_ms
                    )),
                    self.loop
                )

    async def _process_events(self):
        """
        Main event processing loop (async coroutine).

        Processes events from audio callback queue.
        """
        print("[Event processor started]")

        while True:
            # Wait for event from queue
            event = await self.event_queue.get()

            # Sentinel for shutdown
            if event is None:
                print("[Event processor stopping]")
                break

            # Process event
            if isinstance(event, SpeechStarted):
                # Speech started - emit to callback
                await self._emit_event(event)

            elif isinstance(event, SpeechEnded):
                # Speech ended - emit to callback
                await self._emit_event(event)

                # Save and queue if kept
                if event.kept:
                    await self._save_and_queue_segment(
                        event.segment_index,
                        event.audio_data
                    )
                    self.kept_segment_indices.append(event.segment_index)

            elif isinstance(event, ModeChangeRequested):
                # Mode change requested - apply it
                self._switch_vad_mode(event.requested_mode)

        print("[Event processor stopped]")

    async def _save_and_queue_segment(self, index: int, audio: np.ndarray):
        """
        Save WAV file and queue transcription job (async).

        Args:
            index: Segment index
            audio: Audio data as float32 numpy array
        """
        wav_path = self.session_dir / f"seg_{index:04d}.wav"

        # Save WAV file using executor (blocking I/O)
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            self._save_wav_sync,
            wav_path,
            audio
        )

        # Create and queue transcription job
        from palaver.recorder.transcription import TranscriptionJob
        job = TranscriptionJob(
            segment_index=index,
            wav_path=wav_path,
            session_dir=self.session_dir,
            samplerate=RECORD_SR,
            duration_sec=len(audio) / RECORD_SR,
            timestamp=datetime.now(timezone.utc).isoformat()
        )

        # Queue job (transcriber.queue_job is thread-safe)
        self.transcriber.queue_job(job)

        # Emit transcription queued event
        await self._emit_event(TranscriptionQueued(
            timestamp=time.time(),
            segment_index=index,
            wav_path=wav_path,
            duration_sec=len(audio) / RECORD_SR
        ))

    def _save_wav_sync(self, wav_path: Path, audio: np.ndarray):
        """
        Save WAV file (synchronous, called via executor).

        Args:
            wav_path: Path to save WAV file
            audio: Audio data as float32 numpy array
        """
        audio_i16 = np.int16(audio * 32767)

        with wave.open(str(wav_path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(RECORD_SR)
            wf.writeframes(audio_i16.tobytes())


# ================== HELPER FUNCTIONS ==================

async def run_simulated(simulated_segments: list) -> Path:
    """
    Run simulated mode for fast testing (bypasses audio/VAD).

    Args:
        simulated_segments: List of (text, duration_sec) tuples

    Returns:
        Path to session directory
    """
    print(f"\n{'='*70}")
    print("🚀 SIMULATED MODE")
    print(f"   Segments: {len(simulated_segments)}")
    print(f"{'='*70}\n")

    # Create session
    session = Session()
    session_dir = session.create()

    # Store metadata
    session.add_metadata("input_source", {
        "type": "simulated",
        "source": "simulated_segments"
    })
    session.add_metadata("num_segments", len(simulated_segments))

    # Build transcript map
    transcripts = {i: text for i, (text, _) in enumerate(simulated_segments)}

    # Create simulated transcriber
    from palaver.recorder.transcription import SimulatedTranscriber, TranscriptionJob
    transcriber = SimulatedTranscriber(transcripts=transcripts)
    transcriber.start()

    # Create text processor (with no-op mode change callback)
    def simulated_mode_callback(mode: str):
        print(f"[Simulated] Mode change requested: {mode} (no-op in simulated mode)")

    from palaver.recorder.text_processor import TextProcessor
    text_processor = TextProcessor(
        session_dir=session_dir,
        result_queue=transcriber.get_result_queue(),
        mode_change_callback=simulated_mode_callback
    )
    text_processor.start()

    # Queue simulated transcription jobs
    for i, (text, duration_sec) in enumerate(simulated_segments):
        job = TranscriptionJob(
            segment_index=i,
            wav_path=None,
            session_dir=session_dir,
            samplerate=RECORD_SR,
            duration_sec=duration_sec,
            timestamp=datetime.now(timezone.utc).isoformat()
        )
        transcriber.queue_job(job)
        print(f"Segment {i}: \"{text[:60]}...\" ({duration_sec:.1f}s)")

    print(f"\nProcessing {len(simulated_segments)} simulated segments...")

    # Give text processor time to process
    await asyncio.sleep(0.5)

    # Stop transcriber and text processor
    transcriber.stop()
    text_processor.stop()
    text_processor.finalize(len(simulated_segments))

    # Write manifest
    segment_info = [
        {
            "index": i,
            "file": None,
            "duration_sec": round(duration_sec, 3)
        }
        for i, (_, duration_sec) in enumerate(simulated_segments)
    ]
    session.write_manifest(
        segments=segment_info,
        total_segments=len(simulated_segments),
        samplerate=RECORD_SR
    )

    print(f"\n{'='*70}")
    print(f"✅ Simulated mode complete → {session_dir}")
    print(f"   • {len(simulated_segments)} segments processed")
    print(f"   • Check transcript_incremental.txt for results")
    print(f"{'='*70}\n")

    return session_dir
