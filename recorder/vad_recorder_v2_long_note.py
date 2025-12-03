#!/usr/bin/env python3
"""
palaver/recorder/vad_recorder_v2_long_note.py
Experimental: Dynamic VAD silence detection based on "start new note" command

Features:
- Detects "start new note" command in transcription
- Switches to long silence mode (5 seconds) for extended notes
- Returns to normal mode after long note completes
"""

import sounddevice as sd
import numpy as np
import torch
import time
import wave
import json
import threading
from datetime import datetime, timezone
from pathlib import Path
import subprocess
from scipy.signal import resample_poly
from multiprocessing import Process, Queue, Event
from dataclasses import dataclass, asdict
from typing import Optional, List
from queue import Empty

# ================== CONFIG ==================
RECORD_SR = 48000
VAD_SR = 16000
DEVICE = 3
CHUNK_SEC = 0.03
CHUNK_SIZE = int(CHUNK_SEC * RECORD_SR)

VAD_THRESHOLD = 0.5
MIN_SILENCE_MS = 800        # Normal mode: 0.8 seconds
MIN_SILENCE_MS_LONG = 5000  # Long note mode: 5 seconds
SPEECH_PAD_MS = 1300        # First pass value
MIN_SEG_SEC = 1.2           # ~3-4 syllables at dictation pace (100-130 WPM)

# Transcription settings
NUM_WORKERS = 2             # Number of concurrent transcription workers
JOB_QUEUE_SIZE = 10         # Bounded queue size
WHISPER_MODEL = "models/multilang_whisper_large3_turbo.ggml"
WHISPER_TIMEOUT = 60

BASE_DIR = Path("sessions")
BASE_DIR.mkdir(exist_ok=True)

# ================== DATACLASSES ==================

@dataclass
class TranscriptionJob:
    """Job sent to transcription worker"""
    segment_index: int
    wav_path: Path
    session_dir: Path
    samplerate: int
    duration_sec: float
    timestamp: str

    def to_dict(self):
        """Serialize for queue (Path objects need conversion)"""
        d = asdict(self)
        d['wav_path'] = str(d['wav_path'])
        d['session_dir'] = str(d['session_dir'])
        return d

    @classmethod
    def from_dict(cls, d):
        """Deserialize from queue"""
        d['wav_path'] = Path(d['wav_path'])
        d['session_dir'] = Path(d['session_dir'])
        return cls(**d)


@dataclass
class TranscriptionResult:
    """Result from transcription worker"""
    segment_index: int
    text: str
    success: bool
    error_msg: Optional[str] = None
    processing_time_sec: float = 0.0
    wav_path: Optional[str] = None


# ================== WORKER PROCESS ==================

def transcription_worker(worker_id: int, job_queue: Queue, result_queue: Queue, shutdown_event: Event):
    """
    Worker process that transcribes audio segments.

    Args:
        worker_id: Identifier for this worker (for logging)
        job_queue: Queue to receive TranscriptionJob objects
        result_queue: Queue to send TranscriptionResult objects
        shutdown_event: Event to signal graceful shutdown
    """
    print(f"[Worker {worker_id}] Starting transcription worker")

    while not shutdown_event.is_set():
        try:
            # Non-blocking get with timeout to check shutdown_event
            job_dict = job_queue.get(timeout=0.5)
            if job_dict is None:  # Poison pill
                break

            job = TranscriptionJob.from_dict(job_dict)
            print(f"[Worker {worker_id}] Transcribing segment {job.segment_index}...")

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

                print(f"[Worker {worker_id}] Segment {job.segment_index} done ({processing_time:.1f}s)")

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

            result_queue.put(asdict(result))

        except Empty:
            continue
        except Exception as e:
            print(f"[Worker {worker_id}] Unexpected error: {e}")
            continue

    print(f"[Worker {worker_id}] Shutting down")


# ================== RESULT COLLECTOR ==================

class ResultCollector:
    """Collects transcription results and writes incremental updates"""

    def __init__(self, session_dir: Path, result_queue: Queue, mode_change_callback=None):
        self.session_dir = session_dir
        self.result_queue = result_queue
        self.results = {}
        self.running = True
        self.thread = None
        self.transcript_path = session_dir / "transcript_raw.txt"
        self.incremental_path = session_dir / "transcript_incremental.txt"
        self.mode_change_callback = mode_change_callback  # Callback to signal mode change

        # Initialize files
        self.transcript_path.write_text("# Raw Transcript\n")
        self.incremental_path.write_text("# Incremental Transcript (updates as segments complete)\n")

    def start(self):
        """Start collector thread"""
        self.thread = threading.Thread(target=self._collect_loop, daemon=True)
        self.thread.start()

    def _collect_loop(self):
        """Main loop for collecting results"""
        while self.running:
            try:
                result_dict = self.result_queue.get(timeout=0.5)
                if result_dict is None:  # Stop signal
                    break

                result = TranscriptionResult(**result_dict)
                self.results[result.segment_index] = result

                # Write incremental update
                self._write_incremental(result)

            except Empty:
                continue
            except Exception as e:
                print(f"[Collector] Error processing result: {e}")

    def _write_incremental(self, result: TranscriptionResult):
        """Write incremental update for this segment"""
        with open(self.incremental_path, 'a') as f:
            status = "✓" if result.success else "✗"
            f.write(f"\n{status} Segment {result.segment_index + 1}: {result.text}\n")
            if not result.success and result.error_msg:
                f.write(f"   Error: {result.error_msg}\n")

        print(f"[Collector] Segment {result.segment_index} transcribed: {result.text[:60]}...")

        # Check for "start new note" command
        if result.success and self.mode_change_callback:
            text_lower = result.text.lower()
            # Look for optional prefix (clerk/clark) followed by "start new note"
            if "start new note" in text_lower:
                # Check if preceded by clerk/clark (optional)
                has_prefix = any(word in text_lower for word in ["clerk", "clark"])
                if has_prefix or "start new note" in text_lower:
                    self.mode_change_callback("long_note")
                    print("\n" + "="*70)
                    print("🎙️  LONG NOTE MODE ACTIVATED")
                    print("Silence threshold: 5 seconds (continue speaking...)")
                    print("="*70 + "\n")

    def stop(self):
        """Stop collector and write final transcript"""
        self.running = False
        if self.thread:
            self.thread.join(timeout=2.0)

    def write_final_transcript(self, total_segments: int):
        """Write final ordered transcript"""
        lines = ["# Raw Transcript\n"]

        # Write in order, handling missing segments
        for i in range(total_segments):
            if i in self.results:
                result = self.results[i]
                lines.append(f"{i+1}. {result.text}")
            else:
                lines.append(f"{i+1}. [transcription pending or failed]")

        self.transcript_path.write_text("\n".join(lines))

        # Summary
        successful = sum(1 for r in self.results.values() if r.success)
        failed = total_segments - successful

        summary = [
            f"\n# Transcription Summary",
            f"Total segments: {total_segments}",
            f"Successful: {successful}",
            f"Failed: {failed}"
        ]

        with open(self.transcript_path, 'a') as f:
            f.write("\n".join(summary))


# ================== VAD RECORDING ==================

print("Loading Silero VAD...")
model, utils = torch.hub.load('snakers4/silero-vad', 'silero_vad', trust_repo=True, verbose=False)
(_, _, _, VADIterator, _) = utils

# Global VAD state
vad = None
vad_mode = "normal"  # "normal" or "long_note"
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
    """Switch VAD to new mode (normal or long_note)"""
    global vad, vad_mode
    with vad_lock:
        if new_mode != vad_mode:
            vad_mode = new_mode
            vad = create_vad(new_mode)
            print(f"\n[VAD] Switched to {new_mode} mode")

vad = create_vad("normal")
print("VAD ready.")

segments = []
session_dir = None
in_speech = False
job_queue = None
result_queue = None
collector = None

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
                    print(f"✓ Segment #{len(segments)} KEPT", flush=True)
                    # Trigger save and transcription
                    save_and_queue_segment(len(segments) - 1, seg)

                    # If we just finished a long note, switch back to normal mode
                    if vad_mode == "long_note":
                        switch_vad_mode("normal")
                        print("\n" + "="*70)
                        print("🎙️  NORMAL MODE RESTORED")
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

    # Queue for transcription (non-blocking)
    try:
        job_queue.put(job.to_dict(), block=False)
        print(f"  → Queued for transcription")
    except:
        print(f"  → Queue full, transcription delayed")

def main():
    global session_dir, in_speech, job_queue, result_queue, collector

    sid = datetime.now().strftime("%Y%m%d_%H%M%S")
    session_dir = BASE_DIR / sid
    session_dir.mkdir(exist_ok=True)

    print(f"\nSession → {session_dir}")

    # Initialize queues
    job_queue = Queue(maxsize=JOB_QUEUE_SIZE)
    result_queue = Queue()
    shutdown_event = Event()

    # Start worker processes
    workers = []
    for i in range(NUM_WORKERS):
        p = Process(target=transcription_worker,
                   args=(i, job_queue, result_queue, shutdown_event))
        p.start()
        workers.append(p)

    print(f"Started {NUM_WORKERS} transcription workers")

    # Start result collector with mode change callback
    collector = ResultCollector(session_dir, result_queue, mode_change_callback=switch_vad_mode)
    collector.start()

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
            save_and_queue_segment(len(segments) - 1, seg)

    total_kept_segments = len([s for s in segments if s])  # Count non-empty segments
    print(f"\nFinal segment count: {total_kept_segments}")
    print(f"Waiting for transcriptions to complete...")

    # Signal workers to finish
    for _ in range(NUM_WORKERS):
        job_queue.put(None)  # Poison pill

    # Wait for workers with timeout
    for p in workers:
        p.join(timeout=WHISPER_TIMEOUT * 2)
        if p.is_alive():
            print(f"Warning: Worker still running, terminating...")
            p.terminate()

    # Stop collector
    result_queue.put(None)
    collector.stop()

    # Write final transcript
    collector.write_final_transcript(total_kept_segments)

    # Write manifest
    manifest = {
        "session_start_utc": datetime.now(timezone.utc).isoformat(),
        "samplerate": RECORD_SR,
        "total_segments": total_kept_segments,
        "num_workers": NUM_WORKERS,
        "segments": [
            {
                "index": i,
                "file": f"seg_{i:04d}.wav",
                "duration_sec": round(len(np.concatenate(segments[i]))/RECORD_SR, 3)
            }
            for i in range(len(segments)) if segments[i]
        ]
    }
    (session_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))

    print(f"\nFinished! → {session_dir}")
    print(f"   • {total_kept_segments} speech segments created")
    print(f"   • Check transcript_incremental.txt for real-time results")
    print(f"   • transcript_raw.txt ready for Phase 2")

if __name__ == "__main__":
    main()
