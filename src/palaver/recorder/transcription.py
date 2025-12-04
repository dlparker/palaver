"""
palaver/recorder/transcription.py
Transcription abstraction for audio segments

Provides:
- Transcriber protocol (abstract interface)
- WhisperTranscriber (real transcription with whisper-cli)
- SimulatedTranscriber (fake transcription for fast testing)
"""

import subprocess
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional, Dict
from multiprocessing import Process, Queue, Event
from queue import Empty


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


# ================== TRANSCRIBER PROTOCOL ==================

class Transcriber(ABC):
    """
    Abstract base class for transcription backends.

    Implementations:
    - WhisperTranscriber: Real transcription using whisper-cli
    - SimulatedTranscriber: Fake transcription for testing
    """

    @abstractmethod
    def start(self):
        """
        Initialize transcription backend.

        For WhisperTranscriber: Start worker processes
        For SimulatedTranscriber: No-op
        """
        pass

    @abstractmethod
    def stop(self):
        """
        Cleanup transcription backend.

        For WhisperTranscriber: Stop worker processes
        For SimulatedTranscriber: No-op
        """
        pass

    @abstractmethod
    def queue_job(self, job: TranscriptionJob):
        """
        Queue a transcription job.

        Args:
            job: TranscriptionJob with segment info and WAV path
        """
        pass

    @abstractmethod
    def get_result_queue(self) -> Queue:
        """
        Get the queue for reading transcription results.

        Returns:
            Queue that will receive TranscriptionResult dicts
        """
        pass


# ================== WHISPER TRANSCRIBER ==================

def _transcription_worker(worker_id: int, job_queue: Queue, result_queue: Queue,
                         shutdown_event: Event, model_path: str, timeout: int):
    """
    Worker process that transcribes audio segments using whisper-cli.

    Args:
        worker_id: Identifier for this worker (for logging)
        job_queue: Queue to receive TranscriptionJob objects
        result_queue: Queue to send TranscriptionResult objects
        shutdown_event: Event to signal graceful shutdown
        model_path: Path to whisper model
        timeout: Timeout in seconds for whisper-cli
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
                    "whisper-cli", "-m", model_path,
                    "-f", str(job.wav_path), "--language", "en",
                    "--output-txt", "--no-timestamps"
                ], capture_output=True, text=True, timeout=timeout, check=True)

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


class WhisperTranscriber(Transcriber):
    """
    Real transcription using whisper-cli with multiprocess workers.

    Uses a pool of worker processes to transcribe audio segments in parallel.
    """

    def __init__(self,
                 num_workers: int = 2,
                 model_path: str = "models/multilang_whisper_large3_turbo.ggml",
                 timeout: int = 60,
                 queue_size: int = 10):
        """
        Initialize WhisperTranscriber.

        Args:
            num_workers: Number of worker processes
            model_path: Path to whisper model file
            timeout: Timeout in seconds for whisper-cli
            queue_size: Maximum size of job queue
        """
        self.num_workers = num_workers
        self.model_path = model_path
        self.timeout = timeout
        self.queue_size = queue_size

        self.job_queue = None
        self.result_queue = None
        self.workers = []
        self.shutdown_event = None

    def start(self):
        """Start worker processes."""
        self.job_queue = Queue(maxsize=self.queue_size)
        self.result_queue = Queue()
        self.shutdown_event = Event()

        for i in range(self.num_workers):
            worker = Process(
                target=_transcription_worker,
                args=(i, self.job_queue, self.result_queue, self.shutdown_event,
                      self.model_path, self.timeout),
                daemon=True
            )
            worker.start()
            self.workers.append(worker)

        print(f"Started {self.num_workers} transcription workers")

    def stop(self):
        """Stop worker processes."""
        if self.shutdown_event:
            self.shutdown_event.set()

        # Send poison pills
        if self.job_queue:
            for _ in range(self.num_workers):
                try:
                    self.job_queue.put(None, timeout=1.0)
                except:
                    pass

        # Wait for workers
        for worker in self.workers:
            worker.join(timeout=2.0)
            if worker.is_alive():
                worker.terminate()

        self.workers.clear()

    def queue_job(self, job: TranscriptionJob):
        """Queue a transcription job."""
        if not self.job_queue:
            raise RuntimeError("Transcriber not started. Call start() first.")

        self.job_queue.put(job.to_dict())
        print(f"  → Queued for transcription")

    def get_result_queue(self) -> Queue:
        """Get the result queue."""
        if not self.result_queue:
            raise RuntimeError("Transcriber not started. Call start() first.")

        return self.result_queue


# ================== SIMULATED TRANSCRIBER ==================

class SimulatedTranscriber(Transcriber):
    """
    Simulated transcription that returns pre-defined text immediately.

    For testing downstream text processing without actual transcription overhead.
    """

    def __init__(self, transcripts: Dict[int, str]):
        """
        Initialize SimulatedTranscriber.

        Args:
            transcripts: Map of segment_index -> text
                         e.g., {0: "start new note", 1: "My Title", 2: "Body text"}
        """
        self.transcripts = transcripts
        self.result_queue_internal = None

    def start(self):
        """Initialize result queue."""
        self.result_queue_internal = Queue()

    def stop(self):
        """No-op for simulated mode."""
        pass

    def queue_job(self, job: TranscriptionJob):
        """
        Return pre-defined text immediately (no actual transcription).

        Args:
            job: TranscriptionJob (audio_path ignored)
        """
        if not self.result_queue_internal:
            raise RuntimeError("Transcriber not started. Call start() first.")

        text = self.transcripts.get(job.segment_index, "[no transcript defined]")

        result = TranscriptionResult(
            segment_index=job.segment_index,
            text=text,
            success=True,
            processing_time_sec=0.0,  # Instant
            wav_path=str(job.wav_path) if job.wav_path else None
        )

        # Put result immediately (simulates instant transcription)
        self.result_queue_internal.put(asdict(result))
        print(f"  → Simulated transcription: \"{text[:50]}...\"")

    def get_result_queue(self) -> Queue:
        """Get the result queue."""
        if not self.result_queue_internal:
            raise RuntimeError("Transcriber not started. Call start() first.")

        return self.result_queue_internal
