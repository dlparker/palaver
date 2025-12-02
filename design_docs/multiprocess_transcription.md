# Multiprocess Transcription Architecture

**File**: `recorder/vad_recorder_v2.py`
**Date**: December 2, 2025

## Overview

The multiprocess transcription system decouples audio recording from speech-to-text processing, enabling concurrent transcription of multiple segments while recording continues.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      MAIN PROCESS                            │
│                                                              │
│  ┌──────────────┐        ┌──────────────┐                  │
│  │ Audio Input  │───────▶│     VAD      │                  │
│  │  (48kHz)     │        │  Detection   │                  │
│  └──────────────┘        └──────┬───────┘                  │
│                                  │                           │
│                         Speech Segment Complete             │
│                                  │                           │
│                                  ▼                           │
│                         ┌────────────────┐                  │
│                         │  Save WAV      │                  │
│                         │  seg_NNNN.wav  │                  │
│                         └────────┬───────┘                  │
│                                  │                           │
│                                  ▼                           │
│                   ┌──────────────────────────┐              │
│                   │   Job Queue (bounded)    │              │
│                   │  TranscriptionJob objs   │              │
│                   └──────────┬───────────────┘              │
└──────────────────────────────┼──────────────────────────────┘
                                │
                    ┌───────────┼───────────┐
                    │           │           │
                    ▼           ▼           ▼
         ┌─────────────┐ ┌─────────────┐ ┌─────────────┐
         │  Worker 0   │ │  Worker 1   │ │  Worker N   │
         │  (Process)  │ │  (Process)  │ │  (Process)  │
         │             │ │             │ │             │
         │  Whisper    │ │  Whisper    │ │  Whisper    │
         │  CLI        │ │  CLI        │ │  CLI        │
         └──────┬──────┘ └──────┬──────┘ └──────┬──────┘
                │                │                │
                └────────────────┼────────────────┘
                                 │
                                 ▼
                   ┌──────────────────────────┐
                   │   Result Queue           │
                   │  TranscriptionResult     │
                   └──────────┬───────────────┘
                                 │
         ┌───────────────────────┘
         │
         ▼
┌────────────────────────────────────────────────┐
│  Result Collector Thread (in main process)     │
│                                                 │
│  • Receives completed transcriptions           │
│  • Writes incremental updates                  │
│  • Assembles final transcript                  │
└─────────────────────────────────────────────────┘
```

## Key Components

### 1. TranscriptionJob (Dataclass)

Represents a segment ready for transcription:

```python
@dataclass
class TranscriptionJob:
    segment_index: int          # Segment number (for ordering)
    wav_path: Path              # Path to audio file
    session_dir: Path           # Session directory
    samplerate: int             # Audio sample rate
    duration_sec: float         # Segment duration
    timestamp: str              # UTC timestamp
```

### 2. TranscriptionResult (Dataclass)

Contains transcription output:

```python
@dataclass
class TranscriptionResult:
    segment_index: int           # Matches job
    text: str                    # Transcribed text or error message
    success: bool                # True if transcription succeeded
    error_msg: Optional[str]     # Error details if failed
    processing_time_sec: float   # Time taken to transcribe
    wav_path: Optional[str]      # Original audio file
```

### 3. Worker Process

Each worker:
- Runs in separate process (true parallelism)
- Consumes jobs from bounded queue
- Executes Whisper CLI
- Handles errors gracefully
- Puts results back to result queue

**Current implementation**: Local Whisper CLI
**Future**: Can be extended to call network LLM services

### 4. Result Collector Thread

Runs in main process:
- Monitors result queue
- Writes incremental transcript updates
- Maintains ordered results map
- Generates final transcript on completion

## Configuration

```python
NUM_WORKERS = 2              # Concurrent transcription processes
JOB_QUEUE_SIZE = 10          # Bounded queue (prevents memory overflow)
WHISPER_TIMEOUT = 60         # Timeout per segment (seconds)
```

## File Outputs

### During Recording

**`transcript_incremental.txt`**:
- Written as segments complete
- Shows real-time progress
- Includes success/failure markers
- Useful for monitoring and voice command parsing

Example:
```
# Incremental Transcript

✓ Segment 1: Clerk, this is the first segment.
✓ Segment 2: This is the second segment.
✗ Segment 3: /path/to/seg_0002.wav processing failure: timeout
   Error: timeout
```

### After Recording

**`transcript_raw.txt`**:
- Final ordered transcript
- All segments in sequence
- Summary statistics
- Ready for Phase 2 processing

**`manifest.json`**:
- Session metadata
- Segment list with durations
- Configuration snapshot

## Extension Points for Network LLM

### Current Local Backend

```python
def transcription_worker(worker_id, job_queue, result_queue, shutdown_event):
    # ...
    subprocess.run(["whisper-cli", ...])  # Local Whisper
```

### Future: Network LLM Backend

To support remote LLM transcription (e.g., on your 4060 machine):

```python
# New module: recorder/backends.py

class TranscriptionBackend(ABC):
    @abstractmethod
    def transcribe(self, wav_path: Path, language: str) -> str:
        pass

class LocalWhisperBackend(TranscriptionBackend):
    def transcribe(self, wav_path, language="en"):
        r = subprocess.run(["whisper-cli", ...])
        return r.stdout.strip()

class NetworkLLMBackend(TranscriptionBackend):
    def __init__(self, base_url: str, model: str):
        self.base_url = base_url
        self.model = model

    def transcribe(self, wav_path, language="en"):
        # Read audio file
        with open(wav_path, 'rb') as f:
            audio_data = f.read()

        # HTTP request to your 4060 machine
        response = requests.post(
            f"{self.base_url}/v1/audio/transcriptions",
            files={"file": audio_data},
            data={"model": self.model, "language": language}
        )
        return response.json()["text"]

# Then modify worker:
def transcription_worker(worker_id, job_queue, result_queue,
                        shutdown_event, backend: TranscriptionBackend):
    # ...
    text = backend.transcribe(job.wav_path)
    # ...
```

### Multiple Backend Configuration

```python
# Config for mixed backends (local + remote)
BACKENDS = [
    LocalWhisperBackend(),
    NetworkLLMBackend("http://gpu-machine:8000", "whisper-large-v3"),
    NetworkLLMBackend("http://gpu-machine:8001", "whisper-large-v3"),
]

# Round-robin or load-balance across backends
```

## Benefits

1. **Non-blocking**: Continue recording while segments transcribe
2. **Parallel processing**: Multiple segments transcribe concurrently
3. **Memory safe**: Bounded queue prevents runaway memory usage
4. **Incremental output**: See results as they arrive (enables voice commands)
5. **Fault tolerant**: Worker failures don't crash recording
6. **Extensible**: Easy to add network backends, multiple models, post-processing

## Performance Characteristics

**With 2 local workers on current setup:**
- Segment duration: ~2-4 seconds
- Transcription time: ~1-3 seconds per segment (estimated)
- **Result**: Transcription keeps up with real-time speech

**With network LLM (4060 GPU):**
- Network latency: ~50-100ms
- GPU transcription: <1 second per segment
- **Result**: Could run 4-8 concurrent workers with fast turnaround

## Future Enhancements

### Phase 2: Voice Command Interface

```python
# In result collector
def _write_incremental(self, result: TranscriptionResult):
    # ... existing code ...

    # Parse for voice commands
    if self._is_command(result.text):
        command_queue.put(Command.parse(result.text))
```

### Phase 3: Multi-Model Pipeline

```python
# Different models for different purposes
BACKENDS = [
    ("fast", NetworkLLMBackend("http://gpu:8000", "whisper-tiny")),
    ("accurate", NetworkLLMBackend("http://gpu:8001", "whisper-large-v3")),
    ("punctuation", PunctuationModel()),
]

# Run in parallel, merge results
```

### Phase 4: Streaming / Real-time Partial Results

- Workers send partial results during long segments
- Update UI with "in-progress" transcription
- Useful for voice command responsiveness

## Testing

Test with:
```bash
uv run recorder/vad_recorder_v2.py
```

Monitor incremental updates:
```bash
tail -f sessions/YYYYMMDD_HHMMSS/transcript_incremental.txt
```

## Migration from v1

The original `vad_recorder.py` is preserved. V2 is a drop-in replacement with additional features but same usage pattern.

Key differences:
- V1: Synchronous transcription after recording stops
- V2: Concurrent transcription during recording
- V2: Incremental output file
- V2: Bounded queue, configurable workers
