#!/usr/bin/env python3
"""
palaver/editors/rerecorder.py
Phase 3 – Targeted Re-Recording Tool

Goal: Fix transcription errors by re-recording only marked segments.

Workflow for each marked segment:
1. Show context (±2 lines)
2. Play original audio
3. Press Enter to re-record
4. Record new audio (VAD-based)
5. Play back recorded audio
6. Choose: [k]eep  [r]etry  [s]kip  [q]uit
7. On keep → overwrite WAV, re-transcribe, update transcript

Usage:
    python editors/rerecorder.py sessions/20251202_194521

Requirements:
    - blocks_to_fix.json must exist (created by marker.py)
    - aplay/paplay for audio playback
    - whisper-cli for transcription
"""

import sys
import time
import json
import wave
import subprocess
from pathlib import Path
from datetime import datetime, timezone

import numpy as np
import sounddevice as sd
import torch
from scipy.signal import resample_poly

# ================== CONFIG ==================
RECORD_SR = 48000
VAD_SR = 16000
DEVICE = 3  # Your audio device
CHUNK_SEC = 0.03
CHUNK_SIZE = int(CHUNK_SEC * RECORD_SR)

VAD_THRESHOLD = 0.5
MIN_SILENCE_MS = 800
SPEECH_PAD_MS = 1300
MIN_SEG_SEC = 0.5  # Lower threshold for re-recording short phrases

WHISPER_MODEL = "models/multilang_whisper_large3_turbo.ggml"
WHISPER_TIMEOUT = 60

# Try to detect which audio player is available
AUDIO_PLAYER = None
for player in ["aplay", "paplay", "ffplay"]:
    try:
        subprocess.run([player, "--version"], capture_output=True, timeout=1)
        AUDIO_PLAYER = player
        break
    except:
        continue

if not AUDIO_PLAYER:
    print("Warning: No audio player found (aplay/paplay/ffplay). Playback disabled.")

# ================== VAD SETUP ==================
print("Loading Silero VAD...")
model, utils = torch.hub.load('snakers4/silero-vad', 'silero_vad', trust_repo=True, verbose=False)
(_, _, _, VADIterator, _) = utils
print("VAD ready.")

# ================== HELPER FUNCTIONS ==================

def downsample_to_512(chunk):
    """Downsample to exactly 512 samples @ 16 kHz."""
    down = resample_poly(chunk, VAD_SR, RECORD_SR)
    if down.shape[0] > 512:
        down = down[:512]
    elif down.shape[0] < 512:
        down = np.pad(down, (0, 512 - down.shape[0]))
    return down.astype(np.float32)


def play_audio(wav_path: Path):
    """Play audio file using system player"""
    if not AUDIO_PLAYER:
        print(f"  [Audio playback disabled - no player available]")
        return

    if not wav_path.exists():
        print(f"  [Audio file not found: {wav_path}]")
        return

    try:
        if AUDIO_PLAYER == "ffplay":
            subprocess.run([AUDIO_PLAYER, "-nodisp", "-autoexit", "-loglevel", "quiet",
                          str(wav_path)], check=True, timeout=30)
        else:
            subprocess.run([AUDIO_PLAYER, "-q", str(wav_path)], check=True, timeout=30)
    except subprocess.TimeoutExpired:
        print("  [Playback timeout]")
    except Exception as e:
        print(f"  [Playback failed: {e}]")


def record_segment_vad():
    """
    Record a single segment using VAD.
    Returns numpy array of audio data, or None if recording failed.
    """
    vad = VADIterator(
        model,
        threshold=VAD_THRESHOLD,
        sampling_rate=VAD_SR,
        min_silence_duration_ms=MIN_SILENCE_MS,
        speech_pad_ms=SPEECH_PAD_MS
    )

    segments = []
    in_speech = False
    speech_detected = False

    def audio_callback(indata, frames, time_info, status):
        nonlocal in_speech, speech_detected
        chunk = indata[:, 0].copy()
        vad_chunk = downsample_to_512(chunk)
        window = vad(vad_chunk, return_seconds=False)

        if window:
            if window.get("start") is not None:
                in_speech = True
                speech_detected = True
                segments.append([])
                print("S", end="", flush=True)
            if window.get("end") is not None:
                in_speech = False
                print(".", end="", flush=True)

        if in_speech:
            if not segments:
                segments.append([])
            segments[-1].append(chunk)
            print("S", end="", flush=True)
        else:
            print(".", end="", flush=True)

    print("\n  Recording (speak now)...", end=" ", flush=True)

    try:
        with sd.InputStream(samplerate=RECORD_SR,
                          device=DEVICE,
                          channels=1,
                          dtype='float32',
                          blocksize=CHUNK_SIZE,
                          callback=audio_callback,
                          latency="low"):
            # Record for max 30 seconds or until 2 seconds of silence after speech
            start_time = time.time()
            last_speech_time = time.time()

            while time.time() - start_time < 30:
                time.sleep(0.1)

                if speech_detected:
                    if in_speech:
                        last_speech_time = time.time()
                    elif time.time() - last_speech_time > 2.0:
                        # 2 seconds of silence after speech detected
                        break

    except KeyboardInterrupt:
        print("\n  [Recording cancelled]")
        return None
    except Exception as e:
        print(f"\n  [Recording failed: {e}]")
        return None

    print()  # newline after dots/S

    # Check if we got any speech
    if not segments or not segments[-1]:
        print("  [No speech detected]")
        return None

    # Concatenate all chunks
    audio = np.concatenate(segments[-1])
    duration = len(audio) / RECORD_SR

    if duration < MIN_SEG_SEC:
        print(f"  [Recording too short: {duration:.1f}s < {MIN_SEG_SEC}s]")
        return None

    print(f"  Recorded {duration:.1f}s")
    return audio


def save_wav(wav_path: Path, audio: np.ndarray):
    """Save audio as WAV file"""
    audio_i16 = np.int16(audio * 32767)
    with wave.open(str(wav_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(RECORD_SR)
        wf.writeframes(audio_i16.tobytes())


def transcribe_segment(wav_path: Path) -> str:
    """Transcribe audio file using Whisper"""
    try:
        result = subprocess.run([
            "whisper-cli", "-m", WHISPER_MODEL,
            "-f", str(wav_path), "--language", "en",
            "--output-txt", "--no-timestamps"
        ], capture_output=True, text=True, timeout=WHISPER_TIMEOUT, check=True)

        text = result.stdout.strip()
        return text if text else "[empty]"

    except subprocess.TimeoutExpired:
        return f"{wav_path} processing failure: timeout"
    except Exception as e:
        return f"{wav_path} processing failure: {str(e)}"


# ================== MAIN RERECORDER CLASS ==================

class ReRecorder:
    """Phase 3 - Targeted segment re-recording tool"""

    def __init__(self, session_path: Path):
        self.session_path = session_path
        self.blocks_to_fix = []
        self.transcript_lines = []
        self.corrections = {}  # index -> new text

        self.load_session()

    def load_session(self):
        """Load blocks_to_fix.json and transcript"""
        # Load marked segments
        blocks_path = self.session_path / "blocks_to_fix.json"
        if not blocks_path.exists():
            print(f"Error: {blocks_path} not found")
            print("Run marker.py first to mark segments for fixing.")
            sys.exit(1)

        blocks_data = json.loads(blocks_path.read_text())
        self.blocks_to_fix = blocks_data.get("marked_for_fix", [])

        if not self.blocks_to_fix:
            print("No segments marked for fixing. Nothing to do!")
            sys.exit(0)

        # Load transcript
        raw_path = self.session_path / "transcript_raw.txt"
        if not raw_path.exists():
            print(f"Error: {raw_path} not found")
            sys.exit(1)

        raw_lines = raw_path.read_text().splitlines()
        self.transcript_lines = []
        for line in raw_lines:
            if line.strip() and not line.startswith("#"):
                # Expected format: "1. some text here"
                parts = line.strip().split(". ", 1)
                if len(parts) == 2:
                    self.transcript_lines.append(parts[1])
                else:
                    self.transcript_lines.append(line.strip())

        print(f"\nSession: {self.session_path.name}")
        print(f"Segments to fix: {len(self.blocks_to_fix)}")
        print(f"Total segments: {len(self.transcript_lines)}\n")

    def show_context(self, index: int):
        """Show segment with ±2 lines context"""
        print("\n" + "=" * 70)
        print(f"Segment {index + 1} / {len(self.transcript_lines)}")
        print("=" * 70)

        # Show context
        for i in range(max(0, index - 2), min(len(self.transcript_lines), index + 3)):
            if i == index:
                print(f"\n>>> {i + 1}. {self.transcript_lines[i]} <<<\n")
            else:
                print(f"    {i + 1}. {self.transcript_lines[i]}")

        print("=" * 70)

    def process_segment(self, index: int) -> bool:
        """
        Process a single segment.
        Returns True to continue, False to quit.
        """
        self.show_context(index)

        wav_path = self.session_path / f"seg_{index:04d}.wav"

        # Play original
        print("\n[Playing original audio...]")
        play_audio(wav_path)

        while True:
            # Record new version
            input("\nPress Enter to re-record this segment (or Ctrl+C to skip)...")

            try:
                new_audio = record_segment_vad()
            except KeyboardInterrupt:
                print("\n[Skipped]")
                return True

            if new_audio is None:
                retry = input("Recording failed. Try again? (y/n): ").strip().lower()
                if retry != 'y':
                    return True
                continue

            # Save to temp file and play back
            temp_path = self.session_path / f"temp_rerecord_{index:04d}.wav"
            save_wav(temp_path, new_audio)

            print("\n[Playing recorded audio...]")
            play_audio(temp_path)

            # Menu
            print("\nChoose action:")
            print("  [k] Keep (save and move to next)")
            print("  [r] Retry (record again)")
            print("  [s] Skip (discard and move to next)")
            print("  [q] Quit")

            choice = input("\nYour choice: ").strip().lower()

            if choice == 'k':
                # Keep: overwrite original, transcribe, update
                print("\n[Transcribing...]")
                save_wav(wav_path, new_audio)
                new_text = transcribe_segment(wav_path)
                self.corrections[index] = new_text

                print(f"New transcription: {new_text}")

                # Clean up temp
                if temp_path.exists():
                    temp_path.unlink()

                return True

            elif choice == 'r':
                # Retry: loop back to record again
                if temp_path.exists():
                    temp_path.unlink()
                continue

            elif choice == 's':
                # Skip: discard and continue
                if temp_path.exists():
                    temp_path.unlink()
                return True

            elif choice == 'q':
                # Quit
                if temp_path.exists():
                    temp_path.unlink()
                return False

            else:
                print("Invalid choice. Please enter k, r, s, or q.")

    def save_corrected_transcript(self):
        """Write transcript_corrected.txt with all corrections applied"""
        lines = ["# Corrected Transcript – Palaver Phase 3\n"]

        for i, text in enumerate(self.transcript_lines):
            if i in self.corrections:
                lines.append(f"{i + 1}. {self.corrections[i]}")
            else:
                lines.append(f"{i + 1}. {text}")

        output_path = self.session_path / "transcript_corrected.txt"
        output_path.write_text("\n".join(lines))

        print(f"\n✓ Saved: {output_path}")

    def run(self):
        """Main loop - process all marked segments"""
        print("Starting Phase 3 re-recording...\n")

        processed = 0
        for idx in self.blocks_to_fix:
            if idx >= len(self.transcript_lines):
                print(f"Warning: Segment {idx} out of range, skipping")
                continue

            should_continue = self.process_segment(idx)
            processed += 1

            if not should_continue:
                print("\n[Quit requested]")
                break

        # Save results
        if self.corrections:
            self.save_corrected_transcript()

            # Save metadata
            meta = {
                "last_edited_utc": datetime.now(timezone.utc).isoformat(),
                "corrected_segments": list(self.corrections.keys()),
                "total_corrections": len(self.corrections),
            }
            meta_path = self.session_path / "rerecording_log.json"
            meta_path.write_text(json.dumps(meta, indent=2))

            print(f"\n✓ Corrected {len(self.corrections)} segment(s)")
        else:
            print("\n[No corrections made]")

        print("\nPhase 3 complete!")


# ================== ENTRY POINT ==================

def main():
    if len(sys.argv) != 2:
        print("Usage: rerecorder.py <session_directory>")
        print("Example: rerecorder.py sessions/20251202_194521")
        sys.exit(1)

    session_path = Path(sys.argv[1]).resolve()

    if not session_path.exists():
        print(f"Error: Session directory not found: {session_path}")
        sys.exit(1)

    if not session_path.is_dir():
        print(f"Error: Not a directory: {session_path}")
        sys.exit(1)

    rerecorder = ReRecorder(session_path)
    rerecorder.run()


if __name__ == "__main__":
    main()
