# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Philosophy

**READ AGENT_README.org FIRST** - It contains critical constraints on how to work with this codebase.

Key principles:
- This is a **single-user lab tool**, not production software
- Goal: **fast iteration cycles** - fail fast, try something else, refine what works
- **Do not** add unrequested features or "best practices" appropriate for products
- **Do not** create documentation unless explicitly requested
- **No** retry loops, extensive error handling for non-recoverable errors, or flexibility that wasn't requested
- Testing focuses on **verifying functionality**, not implementation details - minimal mocking

## Development Commands

### Testing
```bash
# Run all fast tests (simulated mode, no real audio/transcription)
uv run pytest -m "not slow"

# Run specific test file
uv run pytest tests/test_text_processor.py

# Run specific test function
uv run pytest tests/test_text_processor.py::test_note_command_detection

# Run with coverage
uv run pytest --cov=palaver --cov-report=html

# Run slow tests (real audio/transcription - takes time)
uv run pytest -m slow
uv run pytest tests_slow/
```

### Running the Recorder

```bash
# Interactive TUI (recommended for development)
uv run python -m palaver.tui.recorder_tui

# Direct CLI recorder (stdin/stdout interface)
uv run python scripts/direct_recorder.py

# From specific microphone device
uv run python scripts/direct_recorder.py --input hw:1,0

# Process WAV file for testing
uv run python scripts/direct_recorder.py --input tests/audio_samples/note1.wav
```

### Package Management
```bash
# This project uses uv for dependency management
# Install package in editable mode (required for development)
uv pip install -e .

# This enables imports from src/palaver/ without setting PYTHONPATH
# Verify installation: uv run python -c "import palaver; print(palaver.__file__)"
```

## Architecture Overview

### Event-Driven Pipeline
```
Audio Input → VAD Segmentation → Transcription → Text Processing → Command Detection → Document Rendering
```

All stages emit **typed events** (dataclasses in `async_vad_recorder.py`) that propagate to the TUI and other listeners.

### Key Components

**AsyncVADRecorder** (`recorder/async_vad_recorder.py`):
- Core async/await event loop
- Two VAD modes: normal (0.8s silence) and long_note (5s silence)
- Modes switch dynamically based on command detection
- Uses Silero VAD (PyTorch) for speech detection
- Processes 30ms audio chunks at 48kHz, downsampled to 16kHz for VAD

**Transcription** (`recorder/transcription.py`):
- `WhisperTranscriber`: Multiprocess worker pool calling whisper-cli subprocess
- `SimulatedTranscriber`: Instant fake transcription for fast testing
- Workers run in separate processes (CPU-bound whisper-cli)

**TextProcessor** (`recorder/text_processor.py`):
- Consumes transcription results from queue (threaded loop)
- Detects commands via `LooseActionPhrase` fuzzy matching
- State machine: idle → waiting_for_title → collecting_body → idle
- Callbacks trigger VAD mode changes back to recorder

**Command System** (`commands/`):
- `CommandDoc`: Abstract base - defines command phrase, speech buckets, render()
- `SpeechBucket`: Timing specification with **relative multipliers** of global base values
- `SimpleNote`: Current implementation - title + body → markdown file
- Fuzzy matching handles transcription variations ("start a new note" matches "start new note")

**Session Management** (`recorder/session.py`):
- Timestamped directories: `sessions/YYYYMMDD_HHMMSS/`
- Contains: WAV segments, transcripts (raw + incremental), manifest.json, rendered documents

### Threading/Concurrency Model

- **Audio callback**: Synchronous sounddevice callback (separate thread)
- **Event processor**: Async/await in AsyncVADRecorder
- **Transcription workers**: Multiprocess pool (2 workers default)
- **Text processor**: Dedicated thread consuming result queue
- **TUI**: Async/await Textual app, receives events via thread-safe callback

Audio callback → asyncio.Queue (thread-safe) → async event processor → transcription queue → worker processes → result queue → text processor thread → events → TUI

## Configuration

**RecorderConfig** (`config/recorder_config.py`):
- Loads from YAML or uses defaults
- **Base timing values** (SpeechBucket multiplies these):
  - `base_segment_size`: 5.0s (target chunk duration)
  - `base_start_window`: 2.0s (timeout for bucket to start)
  - `base_termination_silence`: 0.8s (silence to end bucket)
- VAD thresholds: normal=0.5, long_note=0.7
- Command matching threshold: 80.0% (rapidfuzz similarity)

## Testing Strategy

**Fast tests** (default): Use `SimulatedTranscriber` and mock audio
- Command detection
- State machine transitions
- Text processing logic

**Slow tests** (`tests_slow/`, `-m slow`): Real audio and transcription
- End-to-end recording with WAV files
- Actual whisper-cli transcription
- FileAudioSource playback

Use markers:
```python
@pytest.mark.slow
@pytest.mark.fast
@pytest.mark.integration
@pytest.mark.unit
```

## Adding New Commands

1. Create subclass of `CommandDoc` in `commands/`
2. Define `command_phrase` property (trigger phrase)
3. Define `speech_buckets` property (list of `SpeechBucket` instances)
4. Implement `render()` method (generate output files)
5. Register in command registry (future: currently only SimpleNote)

SpeechBucket timing is **relative**:
```python
SpeechBucket(
    name="note_title",
    display_name="Note Title",
    segment_size=0.4,         # 0.4 × 5.0s = 2.0s chunks
    start_window=3.0,         # 3.0 × 2.0s = 6.0s timeout
    termination_silence=1.0   # 1.0 × 0.8s = 0.8s silence
)
```

## Common Patterns

**Action phrase matching** for transcription variations:
```python
phrase = LooseActionPhrase(
    pattern="start new note",
    threshold=0.66,  # 2 of 3 words must match
    ignore_prefix=r'^(clerk|lurk|clark),?\s*'  # Strip artifacts
)
score = phrase.match("start a new note")  # Returns 1.0 (ignores "a")
```

**Event emission** throughout pipeline:
```python
@dataclass
class SpeechEnded(AudioEvent):
    segment_index: int
    audio_data: np.ndarray
    duration_sec: float
    kept: bool
```

All events inherit from `AudioEvent` with timestamp field.

## File Structure

- `src/palaver/recorder/`: Core recording, VAD, transcription, text processing
- `src/palaver/commands/`: Command system (CommandDoc, SpeechBucket, implementations)
- `src/palaver/config/`: Configuration management
- `src/palaver/tui/`: Textual-based terminal UI
- `scripts/`: CLI entry points (direct_recorder.py)
- `tests/`: Fast pytest tests
- `tests_slow/`: Slow tests with real audio/transcription
- `sessions/`: Runtime output (created automatically)
- `models/`: Whisper model files (not in repo)
- `design_docs/`: Architecture documentation
