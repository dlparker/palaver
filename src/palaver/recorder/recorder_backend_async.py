"""
palaver/recorder/recorder_backend_async.py
Async/await recorder backend for clean integration with Textual

This version uses asyncio properly instead of threading hacks.
"""

import asyncio
import time
import wave
import json
import subprocess
from pathlib import Path
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Optional, Callable, List
from queue import Queue, Empty
from multiprocessing import Process, Event

import numpy as np
import sounddevice as sd
import torch
from scipy.signal import resample_poly

# ================== CONFIG ==================
RECORD_SR = 48000
VAD_SR = 16000
DEVICE = "hw:1,0"  # Framework Laptop 13 stereo mic
CHANNELS = 2  # Record stereo, save left channel as mono
CHUNK_SEC = 0.03
CHUNK_SIZE = int(CHUNK_SEC * RECORD_SR)

VAD_THRESHOLD = 0.5
MIN_SILENCE_MS = 800
MIN_SILENCE_MS_LONG = 5000
SPEECH_PAD_MS = 1300
MIN_SEG_SEC = 1.2

NUM_WORKERS = 2
JOB_QUEUE_SIZE = 10
WHISPER_MODEL = "models/multilang_whisper_large3_turbo.ggml"
WHISPER_TIMEOUT = 60

BASE_DIR = Path("sessions")
BASE_DIR.mkdir(exist_ok=True)

# ================== EVENTS ==================

@dataclass
class RecorderEvent:
    """Base class for all recorder events"""
    timestamp: float

@dataclass
class RecordingStateChanged(RecorderEvent):
    """Recording started or stopped"""
    is_recording: bool

@dataclass
class VADModeChanged(RecorderEvent):
    """VAD mode changed"""
    mode: str  # "normal" or "long_note"

@dataclass
class SpeechDetected(RecorderEvent):
    """Speech started"""
    segment_index: int

@dataclass
class SpeechEnded(RecorderEvent):
    """Speech ended"""
    segment_index: int
    duration_sec: float
    kept: bool

@dataclass
class TranscriptionQueued(RecorderEvent):
    """Segment queued for transcription"""
    segment_index: int
    wav_path: Path

@dataclass
class TranscriptionComplete(RecorderEvent):
    """Transcription finished"""
    segment_index: int
    text: str
    success: bool
    processing_time_sec: float

@dataclass
class NoteCommandDetected(RecorderEvent):
    """'start new note' command detected"""
    pass

@dataclass
class NoteTitleCaptured(RecorderEvent):
    """Note title captured"""
    title: str

@dataclass
class QueueStatus(RecorderEvent):
    """Processing queue status update"""
    queued_jobs: int
    completed_transcriptions: int

# ================== TRANSCRIPTION ==================

@dataclass
class TranscriptionJob:
    segment_index: int
    wav_path: Path
    session_dir: Path
    samplerate: int
    duration_sec: float
    timestamp: str

    def to_dict(self):
        d = self.__dict__.copy()
        d['wav_path'] = str(d['wav_path'])
        d['session_dir'] = str(d['session_dir'])
        return d

    @classmethod
    def from_dict(cls, d):
        d['wav_path'] = Path(d['wav_path'])
        d['session_dir'] = Path(d['session_dir'])
        return cls(**d)

@dataclass
class TranscriptionResult:
    segment_index: int
    text: str
    success: bool
    error_msg: Optional[str] = None
    processing_time_sec: float = 0.0
    wav_path: Optional[str] = None

def transcription_worker(worker_id: int, job_queue: Queue, result_queue: Queue, shutdown_event: Event):
    """Worker process for transcription (runs in separate process)"""
    while not shutdown_event.is_set():
        try:
            job_dict = job_queue.get(timeout=0.5)
            if job_dict is None:
                break

            job = TranscriptionJob.from_dict(job_dict)
            start_time = time.time()

            try:
                r = subprocess.run([
                    "whisper-cli", "-m", WHISPER_MODEL,
                    "-f", str(job.wav_path), "--language", "en",
                    "--output-txt", "--no-timestamps"
                ], capture_output=True, text=True, timeout=WHISPER_TIMEOUT, check=True)

                text = r.stdout.strip() or "[empty]"
                processing_time = time.time() - start_time

                result = TranscriptionResult(
                    segment_index=job.segment_index,
                    text=text,
                    success=True,
                    processing_time_sec=processing_time,
                    wav_path=str(job.wav_path)
                )

            except subprocess.TimeoutExpired:
                result = TranscriptionResult(
                    segment_index=job.segment_index,
                    text=f"{job.wav_path} processing failure: timeout",
                    success=False,
                    error_msg="timeout",
                    wav_path=str(job.wav_path)
                )
            except Exception as e:
                result = TranscriptionResult(
                    segment_index=job.segment_index,
                    text=f"{job.wav_path} processing failure: {str(e)}",
                    success=False,
                    error_msg=str(e),
                    wav_path=str(job.wav_path)
                )

            result_queue.put(result.__dict__)

        except Empty:
            continue
        except Exception:
            continue

# ================== ASYNC RECORDER BACKEND ==================

class AsyncRecorderBackend:
    """
    Async/await recorder backend.

    Usage:
        backend = AsyncRecorderBackend(event_callback=my_callback)
        await backend.start_recording()
        # ... recording ...
        await backend.stop_recording()
    """

    def __init__(self, event_callback: Optional[Callable[[RecorderEvent], None]] = None):
        """
        Args:
            event_callback: Async or sync function called for each event
        """
        self.event_callback = event_callback

        # Recording state
        self.is_recording = False
        self.session_dir = None
        self.stream = None

        # VAD state
        self.vad = None
        self.vad_mode = "normal"
        self.vad_mode_requested = None
        self.in_speech = False
        self.segments = []

        # Transcription state
        self.job_queue = None
        self.result_queue = None
        self.workers = []
        self.shutdown_event = None
        self.result_collector_task = None
        self.results = {}

        # Note state machine
        self.waiting_for_title = False
        self.current_note_title = None

        # VAD (lazy-loaded)
        self.model = None
        self.VADIterator = None
        self.vad_loaded = False

        # Event loop (for scheduling from audio thread)
        self.loop = None

    async def _emit_event(self, event: RecorderEvent):
        """Emit event to callback (handles both async and sync callbacks)"""
        if self.event_callback:
            try:
                if asyncio.iscoroutinefunction(self.event_callback):
                    await self.event_callback(event)
                else:
                    self.event_callback(event)
            except Exception as e:
                print(f"[Backend] Event callback error: {e}")

    async def _ensure_vad_loaded(self):
        """Lazy-load VAD in executor to avoid blocking"""
        if not self.vad_loaded:
            loop = asyncio.get_event_loop()
            # Run blocking torch.hub.load in thread pool
            model, utils = await loop.run_in_executor(
                None,
                lambda: torch.hub.load('snakers4/silero-vad', 'silero_vad',
                                     trust_repo=True, verbose=False)
            )
            self.model = model
            self.VADIterator = utils[3]
            self.vad_loaded = True

    def _create_vad(self, mode="normal"):
        """Create VAD with specified mode (sync - VAD must be loaded first)"""
        silence_ms = MIN_SILENCE_MS_LONG if mode == "long_note" else MIN_SILENCE_MS
        self.vad = self.VADIterator(
            self.model,
            threshold=VAD_THRESHOLD,
            sampling_rate=VAD_SR,
            min_silence_duration_ms=silence_ms,
            speech_pad_ms=SPEECH_PAD_MS
        )

    def _switch_vad_mode(self, new_mode):
        """Queue VAD mode change"""
        if new_mode != self.vad_mode:
            self.vad_mode_requested = new_mode

    def _apply_vad_mode_change(self):
        """Apply queued VAD mode change (called from audio thread)"""
        if self.vad_mode_requested and self.vad_mode_requested != self.vad_mode:
            self.vad_mode = self.vad_mode_requested
            self.vad_mode_requested = None
            self._create_vad(self.vad_mode)
            # Schedule event emission from audio thread
            self._schedule_coro(self._emit_event(VADModeChanged(
                timestamp=time.time(),
                mode=self.vad_mode
            )))

    def _downsample_to_512(self, chunk):
        """Downsample to 512 samples @ 16kHz"""
        down = resample_poly(chunk, VAD_SR, RECORD_SR)
        if down.shape[0] > 512:
            down = down[:512]
        elif down.shape[0] < 512:
            down = np.pad(down, (0, 512 - down.shape[0]))
        return down.astype(np.float32)

    def _schedule_coro(self, coro):
        """Schedule coroutine from audio thread"""
        if self.loop:
            asyncio.run_coroutine_threadsafe(coro, self.loop)

    def _audio_callback(self, indata, frames, time_info, status):
        """Audio callback (runs in audio thread, must be sync)"""
        chunk = indata[:, 0].copy()
        vad_chunk = self._downsample_to_512(chunk)
        window = self.vad(vad_chunk, return_seconds=False)

        if window:
            if window.get("start") is not None:
                self._apply_vad_mode_change()
                self.in_speech = True
                self.segments.append([])
                self._schedule_coro(self._emit_event(SpeechDetected(
                    timestamp=time.time(),
                    segment_index=len(self.segments) - 1
                )))

            if window.get("end") is not None:
                self.in_speech = False
                if self.segments and self.segments[-1]:
                    seg = np.concatenate(self.segments[-1])
                    dur = len(seg) / RECORD_SR
                    kept = dur >= MIN_SEG_SEC

                    if kept:
                        idx = len(self.segments) - 1
                        self._schedule_coro(self._save_and_queue_segment(idx, seg))

                        if self.vad_mode == "long_note":
                            self._switch_vad_mode("normal")
                    else:
                        self.segments.pop()

                    self._schedule_coro(self._emit_event(SpeechEnded(
                        timestamp=time.time(),
                        segment_index=len(self.segments) - 1 if kept else -1,
                        duration_sec=dur,
                        kept=kept
                    )))

        if self.in_speech:
            if not self.segments:
                self.segments.append([])
            self.segments[-1].append(chunk)

    async def _save_and_queue_segment(self, index: int, audio: np.ndarray):
        """Save WAV and queue for transcription"""
        wav_path = self.session_dir / f"seg_{index:04d}.wav"

        # Run blocking I/O in executor
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._save_wav, wav_path, audio)

        job = TranscriptionJob(
            segment_index=index,
            wav_path=wav_path,
            session_dir=self.session_dir,
            samplerate=RECORD_SR,
            duration_sec=len(audio) / RECORD_SR,
            timestamp=datetime.now(timezone.utc).isoformat()
        )

        try:
            self.job_queue.put(job.to_dict(), block=False)
            await self._emit_event(TranscriptionQueued(
                timestamp=time.time(),
                segment_index=index,
                wav_path=wav_path
            ))
            await self._emit_queue_status()
        except:
            pass  # Queue full

    def _save_wav(self, wav_path: Path, audio: np.ndarray):
        """Save WAV file (sync, called via executor)"""
        audio_i16 = np.int16(audio * 32767)
        with wave.open(str(wav_path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(RECORD_SR)
            wf.writeframes(audio_i16.tobytes())

    async def _result_collector_loop(self):
        """Collect transcription results (async coroutine)"""
        loop = asyncio.get_event_loop()

        while self.is_recording or not self.result_queue.empty():
            try:
                # Poll queue in executor to avoid blocking
                result_dict = await loop.run_in_executor(
                    None,
                    lambda: self.result_queue.get(timeout=0.5)
                )

                if result_dict is None:
                    break

                result = TranscriptionResult(**result_dict)
                self.results[result.segment_index] = result

                await self._emit_event(TranscriptionComplete(
                    timestamp=time.time(),
                    segment_index=result.segment_index,
                    text=result.text,
                    success=result.success,
                    processing_time_sec=result.processing_time_sec
                ))

                await self._emit_queue_status()

                # Check for commands
                if result.success:
                    await self._check_for_commands(result.text)

            except Empty:
                await asyncio.sleep(0.1)
            except Exception as e:
                print(f"[Collector] Error: {e}")
                await asyncio.sleep(0.1)

    async def _check_for_commands(self, text: str):
        """Check transcribed text for voice commands"""
        text_lower = text.lower()

        if not self.waiting_for_title and "start new note" in text_lower:
            self.waiting_for_title = True
            await self._emit_event(NoteCommandDetected(timestamp=time.time()))

        elif self.waiting_for_title:
            self.waiting_for_title = False
            self.current_note_title = text
            self._switch_vad_mode("long_note")
            await self._emit_event(NoteTitleCaptured(
                timestamp=time.time(),
                title=text
            ))

    async def _emit_queue_status(self):
        """Emit queue status event"""
        queued = self.job_queue.qsize() if self.job_queue else 0
        completed = len(self.results)
        await self._emit_event(QueueStatus(
            timestamp=time.time(),
            queued_jobs=queued,
            completed_transcriptions=completed
        ))

    async def start_recording(self):
        """Start recording session (async)"""
        if self.is_recording:
            return

        # Store event loop for audio thread
        self.loop = asyncio.get_event_loop()

        # Create session
        sid = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.session_dir = BASE_DIR / sid
        self.session_dir.mkdir(exist_ok=True)

        # Load VAD (async, non-blocking)
        await self._ensure_vad_loaded()
        self._create_vad("normal")

        # Initialize queues and workers
        self.job_queue = Queue(maxsize=JOB_QUEUE_SIZE)
        self.result_queue = Queue()
        self.shutdown_event = Event()

        self.workers = []
        for i in range(NUM_WORKERS):
            p = Process(target=transcription_worker,
                       args=(i, self.job_queue, self.result_queue, self.shutdown_event))
            p.start()
            self.workers.append(p)

        # Start result collector as async task
        self.result_collector_task = asyncio.create_task(self._result_collector_loop())

        # Reset state
        self.segments.clear()
        self.in_speech = False
        self.results = {}
        self.waiting_for_title = False
        self.current_note_title = None
        self.vad.reset_states()

        # Start audio stream (stereo mic, we'll use left channel)
        self.stream = sd.InputStream(
            samplerate=RECORD_SR,
            device=DEVICE,
            channels=CHANNELS,
            dtype='float32',
            blocksize=CHUNK_SIZE,
            callback=self._audio_callback,
            latency="low"
        )
        self.stream.start()
        print("Microphone opened successfully (2 channels → using left channel)")

        self.is_recording = True
        await self._emit_event(RecordingStateChanged(
            timestamp=time.time(),
            is_recording=True
        ))

    async def stop_recording(self):
        """Stop recording session (async)"""
        if not self.is_recording:
            return

        self.is_recording = False

        # Stop audio stream
        if self.stream:
            self.stream.stop()
            self.stream.close()
            self.stream = None

        await asyncio.sleep(0.5)  # Let final segments process

        # Shutdown workers
        for _ in range(NUM_WORKERS):
            self.job_queue.put(None)

        # Wait for workers with timeout
        loop = asyncio.get_event_loop()
        for p in self.workers:
            await loop.run_in_executor(None, p.join, WHISPER_TIMEOUT * 2)
            if p.is_alive():
                p.terminate()

        # Stop result collector
        self.result_queue.put(None)
        if self.result_collector_task:
            await asyncio.wait_for(self.result_collector_task, timeout=2.0)

        # Save final transcript
        await self._save_final_transcript()

        await self._emit_event(RecordingStateChanged(
            timestamp=time.time(),
            is_recording=False
        ))

    async def _save_final_transcript(self):
        """Save final transcript files"""
        if not self.session_dir:
            return

        lines = ["# Raw Transcript\n"]
        for i in range(len(self.segments)):
            if i in self.results:
                lines.append(f"{i+1}. {self.results[i].text}")
            else:
                lines.append(f"{i+1}. [pending transcription]")

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            lambda: (self.session_dir / "transcript_raw.txt").write_text("\n".join(lines))
        )

        # Save manifest
        manifest = {
            "session_start_utc": datetime.now(timezone.utc).isoformat(),
            "samplerate": RECORD_SR,
            "total_segments": len([s for s in self.segments if s]),
            "num_workers": NUM_WORKERS,
        }
        await loop.run_in_executor(
            None,
            lambda: (self.session_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
        )

    def get_session_path(self) -> Optional[Path]:
        """Get current session directory"""
        return self.session_dir
