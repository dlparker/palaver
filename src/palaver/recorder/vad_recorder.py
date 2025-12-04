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
from palaver.recorder.transcription import WhisperTranscriber, TranscriptionJob
from palaver.recorder.text_processor import TextProcessor
from palaver.recorder.session import Session

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

def main(input_source: Optional[str] = None):
    """
    Run the VAD recorder.

    Args:
        input_source: Either device name (e.g., "hw:1,0") or path to WAV file.
                     If None, uses DEVICE constant.
    """
    global session_dir, in_speech, transcriber, text_processor

    # Create session
    session = Session()
    session_dir = session.create()

    # Determine audio source
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

    # Create transcriber
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
