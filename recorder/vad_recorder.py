#!/usr/bin/env python3
"""
palaver/recorder/vad_recorder.py
Phase 1 – FINAL WORKING VERSION (December 2025)
Tested on your laptop (ALC295, 48 kHz, PipeWire)
"""

import sounddevice as sd
import numpy as np
import torch
import time
import wave
import json
from datetime import datetime, timezone
from pathlib import Path
import subprocess
from scipy.signal import resample_poly

# ================== CONFIG ==================
RECORD_SR = 48000
VAD_SR = 16000
DEVICE = 3
CHUNK_SEC = 0.03
CHUNK_SIZE = int(CHUNK_SEC * RECORD_SR)

VAD_THRESHOLD = 0.5
MIN_SILENCE_MS = 800
#SPEECH_PAD_MS = 2000       # Increased padding to capture speech onset
SPEECH_PAD_MS = 1300        # First pass value
MIN_SEG_SEC = 1.2           # ~3-4 syllables at dictation pace (100-130 WPM)

BASE_DIR = Path("sessions")
BASE_DIR.mkdir(exist_ok=True)

print("Loading Silero VAD...")
model, utils = torch.hub.load('snakers4/silero-vad', 'silero_vad', trust_repo=True, verbose=False)
(_, _, _, VADIterator, _) = utils

vad = VADIterator(
    model,
    threshold=VAD_THRESHOLD,
    sampling_rate=VAD_SR,
    min_silence_duration_ms=MIN_SILENCE_MS,
    speech_pad_ms=SPEECH_PAD_MS
)
print("VAD ready.")

segments = []
session_dir = None
in_speech = False  # Track current speech state

def downsample_to_512(chunk):
    """Downsample to exactly 512 samples @ 16 kHz."""
    down = resample_poly(chunk, VAD_SR, RECORD_SR)
    if down.shape[0] > 512:
        down = down[:512]
    elif down.shape[0] < 512:
        down = np.pad(down, (0, 512 - down.shape[0]))
    return down.astype(np.float32)

def audio_callback(indata, frames, time_info, status):
    global in_speech
    chunk = indata[:, 0].copy()  # mono

    vad_chunk = downsample_to_512(chunk)

    # This call returns a dict when speech starts/ends, None otherwise
    window = vad(vad_chunk, return_seconds=False)

    if window:
        if window.get("start") is not None:
            in_speech = True
            segments.append([])  # new segment starts
            print("\n[Speech start]", end=" ", flush=True)
        if window.get("end") is not None:
            in_speech = False
            if segments and segments[-1]:
                seg = np.concatenate(segments[-1])
                dur = len(seg) / RECORD_SR
                num_chunks = len(segments[-1])
                print(f"\n[Speech end: {num_chunks} chunks, {dur:.2f}s]", end=" ", flush=True)
                if dur >= MIN_SEG_SEC:
                    print(f"✓ Segment #{len(segments)} KEPT", flush=True)
                else:
                    print(f"✗ DISCARDED (< {MIN_SEG_SEC}s)", flush=True)
                    segments.pop()  # discard tiny fragment

    # Accumulate while we are in speech state
    if in_speech:
        if not segments:
            segments.append([])
        segments[-1].append(chunk)

    print("S" if in_speech else ".", end="", flush=True)

def main():
    global session_dir, in_speech
    sid = datetime.now().strftime("%Y%m%d_%H%M%S")
    session_dir = BASE_DIR / sid
    session_dir.mkdir(exist_ok=True)

    print(f"\nSession → {session_dir}")
    input("Press Enter to start...")

    segments.clear()
    in_speech = False
    vad.reset_states()

    with sd.InputStream(samplerate=RECORD_SR,
                       device=DEVICE,
                       channels=1,
                       dtype='float32',
                       blocksize=CHUNK_SIZE,
                       callback=audio_callback,
                       latency="low"):
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

    print(f"\nFinal segment count: {len(segments)}")

    # Save all segments
    lines = ["# Raw Transcript\n"]
    for i, seg_chunks in enumerate(segments):
        audio = np.concatenate(seg_chunks)
        wav_path = session_dir / f"seg_{i:04d}.wav"
        audio_i16 = np.int16(audio * 32767)
        with wave.open(str(wav_path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(RECORD_SR)
            wf.writeframes(audio_i16.tobytes())

        # Whisper
        try:
            r = subprocess.run([
                "whisper-cli", "-m", "../multilang_whisper_large3_turbo.ggml",
                "-f", str(wav_path), "--language", "en", "--output-txt", "--no-timestamps"
            ], capture_output=True, text=True, timeout=60, check=True)
            text = r.stdout.strip() or "[empty]"
        except Exception:
            text = "[failed]"
        lines.append(f"{i+1}. {text}")

    (session_dir / "transcript_raw.txt").write_text("\n".join(lines))

    manifest = {
        "session_start_utc": datetime.now(timezone.utc).isoformat(),
        "samplerate": RECORD_SR,
        "total_segments": len(segments),
        "segments": [{"index": i, "file": f"seg_{i:04d}.wav", "duration_sec": round(len(np.concatenate(segments[i]))/RECORD_SR, 3)}
                     for i in range(len(segments))]
    }
    (session_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))

    print(f"\nFinished! → {session_dir}")
    print(f"   • {len(segments)} speech segments created")
    print(f"   • transcript_raw.txt ready for Phase 2")

if __name__ == "__main__":
    main()

    
