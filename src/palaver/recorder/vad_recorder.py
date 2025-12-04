#!/usr/bin/env python3
"""
palaver/recorder/vad_recorder.py
Voice Activity Detection (VAD) recorder with dynamic silence thresholds

Features:
- Detects "start new note" command in transcription
- Switches to long silence mode (5 seconds) for extended notes
- Returns to normal mode after long note completes
- Supports both live microphone input and WAV file input (for testing)
"""

import sys
import argparse
import numpy as np
import torch
import time
import wave
import threading
from datetime import datetime, timezone
from pathlib import Path
from scipy.signal import resample_poly
from typing import Optional

# Import audio source abstraction
from palaver.recorder.audio_sources import create_audio_source, FileAudioSource

# Import new modular components
from palaver.recorder.transcription import WhisperTranscriber, SimulatedTranscriber, TranscriptionJob
from palaver.recorder.text_processor import TextProcessor
from palaver.recorder.session import Session
from palaver.recorder.audio_sources import SimulatedAudioSource

# ================== CONFIG ==================
RECORD_SR = 48000
VAD_SR = 16000
DEVICE = "hw:1,0"
CHUNK_SEC = 0.03
CHUNK_SIZE = int(CHUNK_SEC * RECORD_SR)

VAD_THRESHOLD = 0.5
MIN_SILENCE_MS = 800        # Normal mode: 0.8 seconds
MIN_SILENCE_MS_LONG = 5000  # Long note mode: 5 seconds
SPEECH_PAD_MS = 1300        # First pass value
MIN_SEG_SEC = 1.2           # ~3-4 syllables at dictation pace (100-130 WPM)

# Transcription settings
NUM_WORKERS = 2             # Number of concurrent transcription workers
WHISPER_MODEL = "models/multilang_whisper_large3_turbo.ggml"
WHISPER_TIMEOUT = 60


# ================== VAD RECORDING ==================

print("Loading Silero VAD...")
model, utils = torch.hub.load('snakers4/silero-vad', 'silero_vad', trust_repo=True, verbose=False)
(_, _, _, VADIterator, _) = utils

# Global VAD state
vad = None
vad_mode = "normal"  # "normal" or "long_note"
vad_mode_requested = None  # Requested mode change (applied at segment boundary)
vad_lock = threading.Lock()

def create_vad(mode="normal"):
    """Create VAD with specified silence duration"""
    silence_ms = MIN_SILENCE_MS_LONG if mode == "long_note" else MIN_SILENCE_MS
    return VADIterator(
        model,
        threshold=VAD_THRESHOLD,
        sampling_rate=VAD_SR,
        min_silence_duration_ms=silence_ms,
        speech_pad_ms=SPEECH_PAD_MS
    )

def switch_vad_mode(new_mode):
    """Request VAD mode change (will be applied at next segment boundary)"""
    global vad_mode_requested
    if new_mode != vad_mode:
        vad_mode_requested = new_mode
        print(f"\n[VAD] Mode change queued: {new_mode} (will apply after current segment)")

def apply_vad_mode_change():
    """Apply queued VAD mode change (call at segment boundaries only)"""
    global vad, vad_mode, vad_mode_requested
    if vad_mode_requested and vad_mode_requested != vad_mode:
        vad_mode = vad_mode_requested
        vad_mode_requested = None
        vad = create_vad(vad_mode)
        print(f"\n[VAD] Mode changed to: {vad_mode}")

vad = create_vad("normal")
print("VAD ready.")

segments = []
kept_segment_indices = []  # Track which segments were actually saved (not discarded)
session_dir = None
in_speech = False
transcriber = None
text_processor = None

def downsample_to_512(chunk):
    """Downsample to exactly 512 samples @ 16 kHz."""
    down = resample_poly(chunk, VAD_SR, RECORD_SR)
    if down.shape[0] > 512:
        down = down[:512]
    elif down.shape[0] < 512:
        down = np.pad(down, (0, 512 - down.shape[0]))
    return down.astype(np.float32)

def audio_callback(indata, frames, time_info, status):
    global in_speech, vad_mode
    chunk = indata[:, 0].copy()  # mono

    vad_chunk = downsample_to_512(chunk)

    # This call returns a dict when speech starts/ends, None otherwise
    with vad_lock:
        window = vad(vad_chunk, return_seconds=False)

    if window:
        if window.get("start") is not None:
            # Apply any queued mode change BEFORE starting new segment
            apply_vad_mode_change()

            in_speech = True
            segments.append([])  # new segment starts
            mode_indicator = " [LONG NOTE]" if vad_mode == "long_note" else ""
            print(f"\n[Speech start{mode_indicator}]", end=" ", flush=True)
        if window.get("end") is not None:
            in_speech = False
            if segments and segments[-1]:
                seg = np.concatenate(segments[-1])
                dur = len(seg) / RECORD_SR
                num_chunks = len(segments[-1])
                print(f"\n[Speech end: {num_chunks} chunks, {dur:.2f}s]", end=" ", flush=True)
                if dur >= MIN_SEG_SEC:
                    seg_index = len(segments) - 1
                    print(f"✓ Segment #{len(segments)} KEPT", flush=True)
                    # Trigger save and transcription
                    save_and_queue_segment(seg_index, seg)
                    kept_segment_indices.append(seg_index)

                    # If we just finished a long note, queue switch back to normal mode
                    if vad_mode == "long_note":
                        switch_vad_mode("normal")
                        print("\n" + "="*70)
                        print("🎙️  WILL RESTORE NORMAL MODE after this segment")
                        print("Silence threshold: 0.8 seconds")
                        print("="*70 + "\n")
                else:
                    print(f"✗ DISCARDED (< {MIN_SEG_SEC}s)", flush=True)
                    segments.pop()  # discard tiny fragment

    # Accumulate while we are in speech state
    if in_speech:
        if not segments:
            segments.append([])
        segments[-1].append(chunk)

    mode_char = "L" if vad_mode == "long_note" else "S"
    print(mode_char if in_speech else ".", end="", flush=True)

def save_and_queue_segment(index: int, audio: np.ndarray):
    """Save WAV file and queue transcription job"""
    wav_path = session_dir / f"seg_{index:04d}.wav"
    audio_i16 = np.int16(audio * 32767)

    with wave.open(str(wav_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(RECORD_SR)
        wf.writeframes(audio_i16.tobytes())

    # Create transcription job
    job = TranscriptionJob(
        segment_index=index,
        wav_path=wav_path,
        session_dir=session_dir,
        samplerate=RECORD_SR,
        duration_sec=len(audio) / RECORD_SR,
        timestamp=datetime.now(timezone.utc).isoformat()
    )

    # Queue for transcription via transcriber
    transcriber.queue_job(job)

def _run_simulated_mode(session: Session, simulated_segments: list) -> Path:
    """
    Run in simulated mode - bypass VAD and use pre-defined transcriptions.

    This mode is for fast testing of downstream text processing without
    actual audio recording or transcription overhead.

    Args:
        session: Session object with created session directory
        simulated_segments: List of (text, duration_sec) tuples

    Returns:
        Path to session directory
    """
    global session_dir, transcriber, text_processor

    session_dir = session.get_path()

    print(f"\n{'='*70}")
    print("🚀 SIMULATED MODE")
    print(f"   Segments: {len(simulated_segments)}")
    print(f"{'='*70}\n")

    # Build transcript map for SimulatedTranscriber
    transcripts = {i: text for i, (text, _) in enumerate(simulated_segments)}

    # Store metadata
    session.add_metadata("input_source", {
        "type": "simulated",
        "source": "simulated_segments"
    })
    session.add_metadata("num_segments", len(simulated_segments))

    # Create simulated transcriber
    transcriber = SimulatedTranscriber(transcripts=transcripts)
    transcriber.start()

    # Create text processor (with no-op mode change callback)
    def simulated_mode_callback(mode: str):
        print(f"[Simulated] Mode change requested: {mode} (no-op in simulated mode)")

    text_processor = TextProcessor(
        session_dir=session_dir,
        result_queue=transcriber.get_result_queue(),
        mode_change_callback=simulated_mode_callback
    )
    text_processor.start()

    # Queue simulated transcription jobs (no actual WAV files)
    for i, (text, duration_sec) in enumerate(simulated_segments):
        job = TranscriptionJob(
            segment_index=i,
            wav_path=None,  # No actual WAV file in simulated mode
            session_dir=session_dir,
            samplerate=RECORD_SR,
            duration_sec=duration_sec,
            timestamp=datetime.now(timezone.utc).isoformat()
        )
        transcriber.queue_job(job)
        print(f"Segment {i}: \"{text[:60]}...\" ({duration_sec:.1f}s)")

    print(f"\nProcessing {len(simulated_segments)} simulated segments...")

    # Give text processor time to process all results
    import time
    time.sleep(0.5)  # Small delay to ensure all results are processed

    # Stop transcriber and text processor
    transcriber.stop()
    text_processor.stop()

    # Write final transcript
    text_processor.finalize(len(simulated_segments))

    # Write manifest (simulated segments have no actual WAV files)
    segment_info = [
        {
            "index": i,
            "file": None,  # No WAV file in simulated mode
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


def main(input_source: Optional[str] = None,
         mode: str = "auto",
         simulated_segments: Optional[list] = None):
    """
    Run the VAD recorder.

    Args:
        input_source: Either device name (e.g., "hw:1,0") or path to WAV file.
                     If None, uses DEVICE constant.
        mode: "auto" (detect from input), "microphone", "file", or "simulated"
        simulated_segments: For simulated mode: list of (text, duration_sec) tuples
    """
    global session_dir, in_speech, transcriber, text_processor

    # Validate simulated mode
    if mode == "simulated" and simulated_segments is None:
        raise ValueError("simulated_segments required when mode='simulated'")

    # Create session
    session = Session()
    session_dir = session.create()

    # Handle simulated mode
    if mode == "simulated":
        return _run_simulated_mode(session, simulated_segments)

    # Determine audio source (real modes)
    if input_source is None:
        input_source = DEVICE

    # Create audio source (device or file)
    audio_source = create_audio_source(
        input_spec=input_source,
        samplerate=RECORD_SR,
        blocksize=CHUNK_SIZE,
        channels=2
    )

    # Detect if interactive mode (device) or file mode
    is_file_input = isinstance(audio_source, FileAudioSource)

    # Store metadata for manifest
    session.add_metadata("input_source", {
        "type": "file" if is_file_input else "device",
        "source": str(input_source)
    })
    session.add_metadata("num_workers", NUM_WORKERS)

    print(f"Input source: {'FILE' if is_file_input else 'DEVICE'} ({input_source})")

    # Create transcriber (real transcription)
    transcriber = WhisperTranscriber(
        num_workers=NUM_WORKERS,
        model_path=WHISPER_MODEL,
        timeout=WHISPER_TIMEOUT
    )
    transcriber.start()

    # Create text processor with mode change callback
    text_processor = TextProcessor(
        session_dir=session_dir,
        result_queue=transcriber.get_result_queue(),
        mode_change_callback=switch_vad_mode
    )
    text_processor.start()

    input("Press Enter to start...")

    segments.clear()
    kept_segment_indices.clear()
    in_speech = False
    vad.reset_states()

    with audio_source:
        audio_source.start(audio_callback)

        if is_file_input:
            print(f"Processing audio file...")
            # Wait for file playback to complete
            audio_source.wait_for_completion()
            print("File processing complete")
        else:
            print("Recording… speak, pause, speak again… press Enter to stop")
            try:
                input()
            except KeyboardInterrupt:
                pass

    time.sleep(1.0)  # let final segment finish

    # Check if there's an unfinished segment
    if in_speech and segments and segments[-1]:
        seg = np.concatenate(segments[-1])
        dur = len(seg) / RECORD_SR
        print(f"\n[Warning: Unfinished segment detected: {len(segments[-1])} chunks, {dur:.2f}s]")
        if dur < MIN_SEG_SEC:
            print(f"  → DISCARDED (< {MIN_SEG_SEC}s)")
            segments.pop()
        else:
            print(f"  → KEPT")
            save_and_queue_segment(len(segments) - 1, seg)
            kept_segment_indices.append(len(segments) - 1)

    total_kept_segments = len([s for s in segments if s])  # Count non-empty segments
    print(f"\nFinal segment count: {total_kept_segments}")
    print(f"Waiting for transcriptions to complete...")

    # Stop transcriber (signals workers to finish)
    transcriber.stop()

    # Stop text processor
    text_processor.stop()

    # Write final transcript
    text_processor.finalize(total_kept_segments)

    # Write manifest using Session
    segment_info = [
        {
            "index": i,
            "file": f"seg_{i:04d}.wav",
            "duration_sec": round(len(np.concatenate(segments[i]))/RECORD_SR, 3)
        }
        for i in kept_segment_indices
    ]
    session.write_manifest(
        segments=segment_info,
        total_segments=total_kept_segments,
        samplerate=RECORD_SR
    )

    print(f"\nFinished! → {session_dir}")
    print(f"   • {total_kept_segments} speech segments created")
    print(f"   • Check transcript_incremental.txt for real-time results")
    print(f"   • transcript_raw.txt ready for Phase 2")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="VAD-based voice recorder with transcription",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Record from microphone (default device)
  python vad_recorder.py

  # Record from specific device
  python vad_recorder.py --input hw:1,0

  # Process a WAV file (for testing)
  python vad_recorder.py --input tests/audio_samples/note1.wav
"""
    )
    parser.add_argument(
        "--input",
        type=str,
        default=None,
        help="Input source: device name (e.g., hw:1,0) or path to WAV file. "
             "Default: uses DEVICE constant from config"
    )

    args = parser.parse_args()

    try:
        main(input_source=args.input)
    except KeyboardInterrupt:
        print("\n\nInterrupted by user")
        sys.exit(0)
    except Exception as e:
        print(f"\n\nError: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
