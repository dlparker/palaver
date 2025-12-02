# recorder_final.py  ←  this is the one you keep forever
import sounddevice as sd
import numpy as np
import wave
import json
import time
import threading
from datetime import datetime, timezone
from pathlib import Path

class SegmentedAudioRecorder:
    def __init__(self, samplerate=48000, channels=1, segment_sec=30.0, out_dir="raw_sound"):
        self.samplerate = samplerate
        self.channels = channels
        self.segment_sec = segment_sec
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)

        self.stream = None
        self.buffer = np.empty((0, channels), dtype="float32")
        self.lock = threading.Lock()
        self.running = threading.Event()
        self.segments = []

    def _callback(self, indata, frames, time_info, status):
        with self.lock:
            self.buffer = np.append(self.buffer, indata.copy(), axis=0)

    def start_recording(self):
        if self.running.is_set():
            return

        # reset everything
        self.segments.clear()
        with self.lock:
            self.buffer = np.empty((0, self.channels), dtype="float32")

        # try mono first, fall back to stereo if device refuses
        for ch in [1, 2]:
            try:
                self.stream = sd.InputStream(
                    samplerate=self.samplerate,
                    device=3,                  # your internal mic
                    channels=ch,
                    dtype="float32",
                    blocksize=2048,
                    latency="low",
                    callback=self._callback,
                )
                self.stream.start()
                self.channels = ch             # remember what actually worked
                break
            except Exception as e:
                print(f"channels={ch} failed → {e}")
                if self.stream:
                    self.stream.close()
                continue
        else:
            raise RuntimeError("Could not open microphone")

        self.running.set()
        self.start_time = datetime.now(timezone.utc).isoformat()
        threading.Thread(target=self._writer, daemon=True).start()
        print(f"Recording started → {self.out_dir.resolve()} (channels={self.channels})")

    def _writer(self):
        need = int(self.segment_sec * self.samplerate)
        idx = 0

        while self.running.is_set() or len(self.buffer) >= need:
            if len(self.buffer) < need:
                time.sleep(0.02)
                continue

            with self.lock:
                seg = self.buffer[:need].copy()
                self.buffer = self.buffer[need:]

            self._save(seg, idx)
            idx += 1

        # final partial
        if len(self.buffer):
            with self.lock:
                seg = self.buffer.copy()
                self.buffer = np.empty((0, self.channels), dtype="float32")
            self._save(seg, idx, partial=True)

    def _save(self, audio_np, idx, partial=False):
        audio_i16 = np.int16(audio_np[:, 0] if audio_np.shape[1] == 2 else audio_np) * 32767

        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")[:-3]
        name = f"seg_{idx:04d}_{ts}.wav"
        wav_path = self.out_dir / name

        with wave.open(str(wav_path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(self.samplerate)
            wf.writeframes(audio_i16.tobytes())

        dur = len(audio_np) / self.samplerate
        meta = {
            "segment_index": idx,
            "file": name,
            "duration_sec": round(dur, 6),
            "samples": len(audio_np),
            "start_offset_sec": idx * self.segment_sec,
            "is_partial": partial,
            "session_start_utc": self.start_time,
        }
        self.segments.append(meta)
        (self.out_dir / f"{Path(name).stem}.json").write_text(json.dumps(meta, indent=2))

        p = " (partial)" if partial else ""
        print(f"→ {name}  ({dur:.2f}s){p}")

    def stop_recording(self):
        if not self.running.is_set():
            return
        self.running.clear()
        if self.stream:
            self.stream.stop()
            self.stream.close()
        time.sleep(0.4)

        manifest = {
            "session_start_utc": self.start_time,
            "samplerate": self.samplerate,
            "channels": 1,   # we always save mono files
            "total_segments": len(self.segments),
            "total_duration_sec": sum(s["duration_sec"] for s in self.segments),
            "segments": self.segments,
        }
        (self.out_dir / "session_manifest.json").write_text(json.dumps(manifest, indent=2))
        print(f"\nStopped — {len(self.segments)} clean segment(s) saved.\n")

if __name__ == "__main__":
    r = SegmentedAudioRecorder(segment_sec=15.0)   # change to 30.0 or whatever you want
    input("Press Enter to start …")
    r.start_recording()
    input("Recording — press Enter again to stop …")
    r.stop_recording()
