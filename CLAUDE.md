# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## System Context

**Palaver is the voice input component of a larger Assistant system.**

The complete system architecture:
- **Palaver** (this project): Voice → Draft text (VAD + Whisper → SQLite)
- **Gibbon** (separate project): Intent detection & action routing (Draft → Intent → Action)
- **Assistant**: The overall system (Palaver + Gibbon + future components)

**Palaver's scope and boundaries:**
- ✅ Palaver handles: Audio capture, VAD, transcription, draft storage
- ❌ Palaver does NOT handle: Intent detection, action routing, or managing todos/lists
- 🔄 Interface: Drafts stored in SQLite database at `vtt_results/`

**Downstream consumer:**
Gibbon reads completed drafts from Palaver and handles all intent detection and action routing. Gibbon uses "intent trees" (context-aware command structures) to determine user intent and route to appropriate actions (todo lists, shopping lists, research questions, etc.).

**When working on Palaver:**
Focus on the voice→text pipeline quality. Intent detection and action routing are out of scope - they happen in Gibbon. Palaver's job is to produce high-quality draft transcriptions.

## Project Overview

**Palaver** is an experimental voice-controlled toolset for LLM interaction. This is a personal exploration project focused on trying techniques rather than building a stable product. Code is expected to undergo extensive modification or replacement as experiments evolve.

The current implementation provides:
- VAD-based audio segment recording
- Whisper.cpp file-based transcription
- Voice-activated note-taking with command detection
- Basic TUI interface using Textual

## Development Setup

This project uses `uv` for dependency management. All commands should be run with `uv run`:

```bash
# Run tests
uv run pytest

# Run tests with coverage
uv run pytest --cov

# Run a specific test
uv run pytest tests/test_scribe_basic.py::test_specific_function

# Run scripts
uv run scripts/mic_to_text.py
uv run scripts/file_to_text.py
```

### Testing with ipdb

Tests are configured to use `ipdb` instead of `pdb`:
- `breakpoint()` in tests automatically uses ipdb
- `pytest --pdb` drops into ipdb on failures
- Configured in `tests/conftest.py`

## Core Architecture

### Event-Driven Audio Processing Pipeline

The system uses an event-driven architecture where audio flows through a chain of processors:

```
Audio Source → Downsampler → VAD Filter → Transcriber → Command Detector
    ↓              ↓             ↓            ↓              ↓
AudioEvents   AudioEvents   AudioEvents  TextEvents   CommandMatch
```

#### Event Types

**Audio Events** (`src/palaver/scribe/audio_events.py`):
- `AudioStartEvent` - Audio stream initialization (sample rate, channels, etc.)
- `AudioChunkEvent` - Raw audio data chunks (numpy arrays)
- `AudioSpeechStartEvent` - VAD detected speech start
- `AudioSpeechStopEvent` - VAD detected speech end
- `AudioStopEvent` - Audio stream termination
- `AudioErrorEvent` - Error conditions

**Text Events** (`src/palaver/scribe/text_events.py`):
- `TextEvent` - Transcribed text with timing information (VTT segments)

#### Listener Pattern

All components implement the `AudioEventListener` or `TextEventListener` protocols:

```python
class AudioEventListener(Protocol):
    async def on_audio_event(self, AudioEvent) -> None: ...

class TextEventListener(Protocol):
    async def on_text_event(self, TextEvent) -> None: ...
```

Components are chained using `add_audio_event_listener()`:

```python
listener = MicListener()
downsampler = DownSampler(target_samplerate=16000, target_channels=1)
listener.add_audio_event_listener(downsampler)
vadfilter = VADFilter(listener)
downsampler.add_audio_event_listener(vadfilter)
whisper = WhisperThread(model_path, error_callback)
vadfilter.add_audio_event_listener(whisper)
```

### Core Components

#### Audio Sources (`src/palaver/scribe/listener/`)

**ListenerCCSMixin** (`listen_api.py`):
- Base mixin providing event emission and error handling
- All listeners inherit from this
- Accepts optional `error_callback: Callable[[dict], None]` for background task errors
- Provides `_handle_background_error()` helper for consistent error handling

**MicListener** (`mic_listener.py`):
- Captures audio from default microphone using sounddevice
- Runs `_reader()` as background asyncio task
- Uses asyncio.Queue for thread-safe audio delivery from callback

**FileListener** (`file_listener.py`):
- Reads audio from WAV files using soundfile
- Supports multiple files played sequentially
- Optional timing simulation for testing

Both listeners:
- Support async context manager (`async with listener:`)
- Pass `error_callback` to parent mixin
- Use `_handle_background_error()` for exception handling

#### Audio Processors (`src/palaver/scribe/listener/`)

**DownSampler** (`downsampler.py`):
- Resamples audio to target sample rate (typically 16kHz for Whisper)
- Converts channel count (e.g., stereo to mono)
- Uses resampy with configurable quality

**VADFilter** (`vad_filter.py`):
- Voice Activity Detection using Silero VAD
- Emits `AudioSpeechStartEvent` and `AudioSpeechStopEvent`
- Marks `AudioChunkEvent.in_speech` for downstream consumers
- Configurable silence threshold and speech padding

#### Transcription (`src/palaver/scribe/scriven/`)

**WhisperThread** (`whisper_thread.py`):
- Wraps pywhispercpp for speech-to-text
- Runs transcription worker in thread or multiprocess
- Buffers audio chunks until speech stops or buffer fills
- Supports optional pre-buffer for capturing audio before speech detection
- Emits `TextEvent` with transcription results
- Uses background tasks: `_sender()` and `_error_watcher()`
- Call `gracefull_shutdown(timeout)` for clean shutdown

**DetectCommands** (`scriven/detect_commands.py`):
- Fuzzy matches transcribed text against command dictionary
- Uses rapidfuzz for Levenshtein distance matching
- Configurable match threshold (default 75%)
- Calls `on_command` callback with `CommandMatch` objects

### Error Handling Pattern

Background tasks in listeners and workers follow a consistent pattern:

```python
async def _background_task(self):
    try:
        # Main work
        pass
    except asyncio.CancelledError:
        logger.info("Task cancelled")
        raise
    except Exception as e:
        await self._handle_background_error(e, "ClassName._method_name")
    finally:
        self._task = None
        await self._cleanup()
```

The `ListenerCCSMixin._handle_background_error()` method:
1. Creates error_dict with exception, traceback, and source
2. Logs the error
3. Calls error_callback if provided
4. Falls back to emitting AudioErrorEvent

This pattern is used by:
- `MicListener._reader()`
- `FileListener._reader()`
- `WhisperThread._sender()` and `_error_watcher()`

## Key Scripts

- `scripts/mic_to_text.py` - Main demo: microphone → transcription → command detection
- `scripts/file_to_text.py` - Test transcription with WAV files

## Files to Ignore

See `ignore_for_now.md` for incomplete/experimental files that should be skipped:
- `src/palaver/scribe/listener/event_io.py` - Incomplete
- `src/palaver/scribe/listener/pre_buffered.py` - Incomplete sketch
- `scripts/qt_ui1.py` - Experimental UI prototype
- `scripts/grok_alter_model.py` - Untested segment recording functionality

## Important Patterns

### Shutdown Sequence

When handling Control-C or shutdown:

```python
async with listener:
    await listener.start_recording()
    try:
        while True:
            await asyncio.sleep(0.1)
    except (KeyboardInterrupt, asyncio.CancelledError):
        print("Shutting down...")
    finally:
        # Cleanup MUST happen inside context manager
        await whisper_thread.gracefull_shutdown(3.0)
```

The `finally` block must be inside the `async with` to ensure proper cleanup order.

### Audio Source IDs

Audio source IDs use a custom URI format:
```
ase://{local_ip}:{port}/palaver/audio_source/{source_type}/{timestamp}
```

Created via `create_source_id()` in `listen_api.py`. These IDs track audio through the pipeline.

### Command Dictionary

Commands are defined in `command_match.py` as pattern → command_str mappings:

```python
default_command_map = {
    'start a new note': 'start_note',
    'break break break': 'end_note',
    # ...
}
```

Fuzzy matching allows natural variations in speech.

## Dependencies

Key dependencies (see `pyproject.toml`):
- **pywhispercpp** - Whisper.cpp Python bindings for transcription
- **silero-vad** - Voice Activity Detection
- **sounddevice** - Audio I/O
- **soundfile** - WAV file handling
- **resampy** - Audio resampling
- **rapidfuzz** - Fast fuzzy string matching
- **python-eventemitter** - Async event system
- **textual** - TUI framework
- **torch/torchaudio** - Required by Silero VAD

Development:
- **pytest** - Testing framework
- **pytest-asyncio** - Async test support
- **pytest-cov** - Coverage reporting
- **ipdb** - Enhanced debugger

## Development Process

This project follows the **LabsNFoundries** process (see `process_docs/labs_n_foundries_0.2.org`), which means:

### Code Maturity Levels

Code in this repository exists at different stages of development maturity. Components are marked with the `@stage` decorator from `src/palaver/stage_markers.py`:

- **Research/Study** - Throwaway exploration code (minimal quality investment)
- **POC** - Proof of concept code (component contracts only)
- **Prototype** - First realistic use (moderate quality, basic tests)
- **MVP** - Production-ready with good test coverage (current target for core pipeline)
- **Production** - Long-term sustainable (comprehensive tests, full docs)

**Example from codebase:**
```python
from palaver.stage_markers import Stage, stage

@stage(Stage.MVP, track_coverage=True)
class WhisperThread:
    """Core transcription component - production quality."""
    pass
```

When reading or modifying code, check its `.__stage__` attribute or decorator to understand expected quality level. Don't assume all code should be at production quality - this is intentional.

### Work Organization

- **Stories** define what to build and at what quality level (org-mode files in `process_docs/stories/`)
- **Tasks** define implementation steps (tracked in beads via `bd` commands)
- See `AGENTS.md` for complete process documentation

## Project Philosophy

This is an experimental project for personal use focused on voice-controlled LLM interaction. Expect:
- Rapid iteration and breaking changes
- Components at different maturity stages
- Code that may be replaced entirely as experiments evolve
- Mixed quality levels reflecting the learning → building spectrum

The current core pipeline (MicListener → VAD → Whisper → Commands) is at **MVP** stage with solid architecture and test coverage. New experimental features may start at Research/POC stages.
