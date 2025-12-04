# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Palaver is a voice-controlled toolset for LLM interaction. The core feature is a VAD (Voice Activity Detection) based recorder that supports dynamic silence thresholds for different interaction types, particularly "note-taking" workflows.

## Package Manager: uv

This project uses `uv` for package management. Python scripts require PYTHONPATH, but pytest handles this via pytest.ini:
```bash
# Running Python scripts directly
PYTHONPATH=src uv run python <script>

# Running tests (pytest.ini sets pythonpath automatically)
uv run pytest <test>
```

## Command Reference

### Running Tests

The project has three test tiers:
- **tests/**: Fast/medium tests using simulated mode and mocked components (seconds)
- **tests_slow/**: Integration tests with real audio/transcription (minutes)

```bash
# Run fast/medium tests (recommended for development)
uv run pytest tests/ -v

# Run slow/integration tests
uv run pytest tests_slow/ -v

# Run all tests (both fast and slow)
uv run pytest

# Run specific test file
uv run pytest tests/test_simulated_recorder.py -v -s

# Run tests by marker
uv run pytest -m fast          # Only fast tests
uv run pytest -m "not slow"    # Skip slow tests

# With coverage
uv run pytest --cov=palaver --cov-report=html
```

### Running the Recorder

The recorder uses an async architecture with a CLI wrapper:

```bash
# With microphone (default)
./run_vad_recorder.sh

# With specific device
./run_vad_recorder.sh --input hw:1,0

# With pre-recorded audio file (for testing)
./run_vad_recorder.sh --input tests/audio_samples/note1.wav

# Direct invocation
PYTHONPATH=src uv run python scripts/direct_recorder.py
PYTHONPATH=src uv run python scripts/direct_recorder.py --input hw:1,0
```

**Note**: `vad_recorder.py` is now a compatibility wrapper. Use `scripts/direct_recorder.py` or the shell wrapper for CLI usage.

### Generating Test Audio

Test audio generation uses a two-stage process (Piper TTS + WAV manipulation):

```bash
# Simple method (generates note workflow test)
./tools/generate_note_test.sh

# Append silence to existing file
python tools/wav_utils.py append input.wav output.wav --silence 6.0

# Concatenate files with precise silence control
python tools/wav_utils.py concat seg1.wav seg2.wav seg3.wav \
    -o output.wav --silence 1.0 1.0 6.0
```

See `tools/README.md` for comprehensive audio generation patterns.

## Architecture

### Recorder Architecture (Async/Await with VAD)

The recorder uses an **async/await architecture** with Voice Activity Detection (VAD) and **dynamic silence thresholds**:

- **Normal mode**: 0.8 second silence threshold (typical speech pauses)
- **Long note mode**: 5 second silence threshold (extended dictation)

**Key Architectural Pattern**: Mode changes are **queued** and applied at segment boundaries, never mid-segment. This prevents race conditions between the audio callback (sync) and transcription processing (async).

**Async Architecture Flow**:
```
Sync Audio Thread                    Async Event Loop
     (fast)                           (main thread)
       │                                    │
   Audio Callback                           │
   • VAD processing          ──────────────>│
   • Segment detection         asyncio.Queue│
   • Push events                            │
                                    Event Processor
                                    • Save WAV files (executor)
                                    • Queue transcription
                                    • Process results
                                    • Command detection
```

**Critical Files**:
- `src/palaver/recorder/async_vad_recorder.py` - Core async recorder implementation
- `src/palaver/recorder/vad_recorder.py` - Sync wrapper for test compatibility
- `scripts/direct_recorder.py` - CLI entry point
- `src/palaver/recorder/audio_sources.py` - Input abstraction (device vs file vs simulated)

### Note Detection State Machine

The "start new note" workflow is a **transcription-triggered state machine** with VAD-based termination:

1. **Normal mode (0.8s)**: User says "start a new note"
2. **Transcription detects command**: Queue switch to long_note mode
3. **Next segment**: Capture as title, apply mode switch
4. **Long note mode (5s)**: Record note body
5. **5+ seconds silence detected**: Segment ends, **automatically** queue switch back to normal
6. **Next segment**: Normal mode restored

**Critical Insight**: Note end is detected by **silence duration**, NOT transcription content. The system automatically exits long note mode after ANY segment completes in that mode.

**Location**: See `design_docs/note_body_detection_explanation.md` for detailed workflow.

### Audio Input Abstraction

The recorder supports three input modes via the `AudioSource` protocol:

- `DeviceAudioSource`: Live microphone via sounddevice
- `FileAudioSource`: Pre-recorded WAV files (for integration testing)
- `SimulatedAudioSource`: Bypasses VAD entirely (for fast unit testing)

The first two sources call the same `audio_callback()` with identical data format, enabling deterministic testing with perfect digital silence (avoiding ambient noise issues). Simulated mode bypasses audio processing entirely for maximum speed.

**Implementation**: `src/palaver/recorder/audio_sources.py`

### Transcription Pipeline (Multiprocess + Async)

The recorder uses a **multiprocess architecture** for parallel transcription, integrated with the async event loop:

1. Async event processor → Save WAV files (via executor)
2. Segments queued → Job queue (multiprocessing.Queue)
3. Worker processes (N=2) → whisper-cli transcription (subprocess)
4. Results collected (thread) → Transcript files + command detection

**Rationale**: Transcription is CPU-intensive and blocks; multiprocessing allows recording to continue uninterrupted while the async event loop remains responsive.

**Critical Pattern**: The `TextProcessor` runs in a separate thread (legacy design), watching the result queue and triggering mode changes via callback when commands are detected in transcriptions. The callback is thread-safe and simply sets a flag that the audio callback checks.

### Modular Architecture (Refactored)

The recorder has been refactored into modular components for testability and maintainability:

**Core Modules:**
- **`transcription.py`**: Transcription abstraction layer
  - `Transcriber` protocol (abstract interface)
  - `WhisperTranscriber` - Real transcription using whisper-cli with multiprocess workers
  - `SimulatedTranscriber` - Instant fake transcription for fast testing

- **`text_processor.py`**: Text processing and command detection
  - `TextProcessor` class - Processes transcription results
  - Command detection using `LooseActionPhrase` matching
  - State machine for note-taking workflow
  - Can be tested independently without audio/transcription

- **`session.py`**: Session management
  - Creates timestamped session directories
  - Writes manifest.json with metadata
  - Tracks session state

- **`action_phrases.py`**: Flexible command matching
  - `ActionPhrase` base class
  - `LooseActionPhrase` - Fuzzy matching with filler word filtering
  - Scoring system for partial matches
  - Configurable thresholds and prefix filtering

**Benefits:**
- Fast testing: Simulated mode runs 100x faster than real transcription
- Isolated testing: Test command detection without audio overhead
- Easy extension: Add new commands by creating ActionPhrase instances
- Clear separation: Audio → VAD → Transcription → Text Processing → Output

## Test Audio Generation System

**Problem**: Piper TTS applies uniform silence between sentences, but VAD testing requires **mixed silence patterns** (1s for normal speech, 6s to trigger mode changes).

**Solution**: Two-stage generation
1. Generate speech segments with Piper (uniform 1s or 0s silence)
2. Concatenate with `tools/wav_utils.py`, specifying exact silence after each segment

**Tools**:
- `tools/wav_utils.py` - Core WAV manipulation (append, concatenate, create silence)
- `tools/generate_note_test.sh` - Quick note workflow test generator
- `tools/generate_test_audio_example.py` - Advanced patterns and examples

**Pattern for New Test Scenarios**:
```python
from tools.wav_utils import concatenate_wavs

# Generate segments separately with Piper
segments = ["seg1.wav", "seg2.wav", "seg3.wav", "seg4.wav"]

# Concatenate with precise silence: 1s, 1s, 1s, 6s
concatenate_wavs(segments, "test.wav", silence_between=[1.0, 1.0, 1.0, 6.0])
```

**VAD Testing Guidelines**:
- Normal mode (0.8s threshold): Use 1.0-1.5s silence to trigger segment end
- Long note mode (5s threshold): Use 6.0-8.0s silence to trigger segment end
- Avoid testing at exact thresholds (flaky)

## Fast Testing with Simulated Mode

The project uses **simulated mode** for rapid testing of downstream text processing logic without audio/transcription overhead.

### Simulated Mode

Simulated mode bypasses VAD and transcription, directly feeding pre-defined text to the text processor:

```python
from palaver.recorder.vad_recorder import main

# Define test scenario
simulated_segments = [
    ("start a new note", 1.5),
    ("My Important Title", 2.0),
    ("Body text for the note", 3.0),
]

# Run in simulated mode (completes in milliseconds)
session_dir = main(mode="simulated", simulated_segments=simulated_segments)

# Verify results
transcript = (session_dir / "transcript_raw.txt").read_text()
assert "My Important Title" in transcript
```

### Test Organization

- **tests/**: Fast/medium tests using simulated mode (~9 seconds for all)
  - Command detection
  - State machine workflows
  - Multiple notes
  - Edge cases
  - Unit tests for modular components

- **tests_slow/**: Integration tests with real audio/transcription
  - VAD behavior with real audio files
  - End-to-end workflows
  - Whisper transcription accuracy

**Best Practice**: Write fast tests first to verify logic, then integration tests to verify audio behavior.

See `tests/README.md` for detailed testing patterns.

## Current Development State

**Status**: Async/await refactoring COMPLETE (see `design_docs/vad_recorder_async_refactoring_plan.md`)

**Recently Completed**:
- ✅ Async/await architecture implementation
- ✅ Event-driven design with asyncio.Queue
- ✅ Thread-safe audio callback → async event processor
- ✅ CLI wrapper for interactive usage (scripts/direct_recorder.py)
- ✅ Backward compatibility wrapper (vad_recorder.py)
- ✅ Test suite reorganization (tests/ for fast, tests_slow/ for integration)
- ✅ All tests passing with async architecture

**Architecture Improvements**:
- Non-blocking async/await throughout (ready for TUI integration)
- Clean separation: sync audio thread → asyncio.Queue → async processing
- Modular design enables isolated testing of text processing
- Simulated mode runs 100x faster than real transcription
- ActionPhrase system for flexible command matching
- Executor pattern for blocking I/O (file writes)

**Test Coverage**:
- Fast tests run in ~9 seconds (tests/)
- Integration tests in tests_slow/
- 100% backward compatibility maintained

**Known Issues**:
1. Microphone long note mode doesn't terminate after 5s silence (likely ambient noise)
2. Current mode switching logic prevents multiple body paragraphs with pauses

## Session State & Planning

**Always check these files before starting work**:
- `SESSION_STATE.md` - Current progress, immediate next steps, open questions
- `design_docs/recorder_refactoring_plan.md` - Complete 6-phase plan with progress report
- `design_docs/note_body_detection_explanation.md` - Note workflow details

## Important Conventions

### "Clerk," Prefix
Test audio uses "Clerk," prefix at start of sentences as a **workaround for VAD speech-start detection quirk**. This prefix should be filtered in production transcription processing.

### File vs Microphone Testing
- **File input**: Deterministic, perfect digital silence, reliable for VAD threshold testing
- **Microphone input**: Environment-dependent, ambient noise prevents true silence detection

Always test with file input first to isolate VAD logic from environment issues.

### Session Directories
Recording sessions are saved to `sessions/YYYYMMDD_HHMMSS/` with:
- `manifest.json` - Metadata including input source type
- `transcript_raw.txt` - Final ordered transcript
- `transcript_incremental.txt` - Real-time transcript updates
- `seg_NNNN.wav` - Individual audio segments

## Critical: Quarantined Files

**⚠️ ABSOLUTELY NEVER examine, read, or interact with any files in:**
- `ai_ignore_these_files/` (if exists)

**These files are STRICTLY QUARANTINED from all LLM interactions. Do NOT:**
- Read these files for any reason
- Suggest changes to these files
- Reference these files in code
- Include these files in any analysis

This is a hard requirement for this codebase.

## Textual UI (TUI)

The project includes a Textual-based UI (`src/palaver/tui/recorder_tui.py`) for the async recorder backend. With the async refactoring complete, the TUI can now integrate with `AsyncVADRecorder`.

**Architecture**: The TUI can receive events from the async backend and update UI components reactively. Event types to support: recording state changes, VAD mode changes, speech detection, transcription completion, note commands.

**Integration Path**: The TUI should instantiate `AsyncVADRecorder` and call its async methods from within the Textual app's event loop. The recorder's event queue can be monitored to update UI in real-time.

## Using the Async API

For programmatic usage or UI integration, use `AsyncVADRecorder` directly:

```python
import asyncio
from palaver.recorder.async_vad_recorder import AsyncVADRecorder

async def main():
    # Create recorder
    recorder = AsyncVADRecorder()

    # Start recording
    await recorder.start_recording(input_source="hw:1,0")

    # Do other async work while recording...
    await asyncio.sleep(10)

    # Stop and get session directory
    session_dir = await recorder.stop_recording()
    print(f"Session saved to: {session_dir}")

# Run from sync code
asyncio.run(main())
```

**For simulated/testing mode**:
```python
from palaver.recorder.async_vad_recorder import run_simulated

segments = [
    ("start a new note", 1.5),
    ("My Title", 2.0),
    ("Body content", 3.0),
]

# Returns Path to session directory
session_dir = asyncio.run(run_simulated(segments))
```

**For backward compatibility** (tests):
```python
from palaver.recorder.vad_recorder import main

# Synchronous wrapper (uses asyncio.run internally)
session_dir = main(mode="simulated", simulated_segments=segments)
```

## Dependencies

Key external dependencies:
- **torch/silero-vad**: Voice activity detection model
- **sounddevice**: Audio device I/O
- **piper-tts**: Text-to-speech for test generation
- **whisper-cli**: Speech-to-text transcription (external binary)
- **textual**: TUI framework
- **scipy**: Audio resampling

## Development Workflow

1. **Before making changes**: Read `SESSION_STATE.md` for current state
2. **When adding features**: Update `design_docs/recorder_refactoring_plan.md`
3. **When creating tests**: Use `tools/` utilities for test audio generation
4. **After changes**: Update `SESSION_STATE.md` if modifying project state
5. **For audio work**: Reference `tools/README.md` for patterns

## Common Pitfalls

1. **Don't modify audio callback to be async** - It runs in audio thread, must be fast and synchronous. Use `asyncio.run_coroutine_threadsafe()` to communicate with async code.
2. **Don't apply mode changes mid-segment** - Always queue and apply at boundaries
3. **Don't test VAD at exact thresholds** - Use clear margins (0.5s or 6s, not 0.8s or 5s)
4. **Don't forget PYTHONPATH=src for direct script execution** - Required for imports (pytest.ini handles this for tests)
5. **Don't assume microphone silence works like file silence** - Ambient noise is real
6. **Don't use blocking I/O in async code** - Use `loop.run_in_executor()` for file operations, subprocess calls, etc.
7. **Don't call `asyncio.run()` from within an async context** - Use `await` instead. Only use `asyncio.run()` from synchronous code.
