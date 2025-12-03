# Recorder Backend

This directory contains the UI-agnostic recorder backend (`recorder_backend.py`), which can be used by any UI (TUI, web, CLI, etc.).

## Files in this directory

- `vad_recorder.py` - Original Phase 1 recorder (CLI, synchronous transcription)
- `vad_recorder_v2.py` - Phase 1 with multiprocess transcription
- `vad_recorder_v2_long_note.py` - Experimental long note mode
- **`recorder_backend.py`** - UI-agnostic backend (NEW)

## Backend Architecture

`recorder_backend.py` provides:
- `RecorderBackend` class - Main recording engine
- Event system - 12 event types for state changes
- VAD processing - Speech detection with mode switching
- Multiprocess transcription - Concurrent Whisper processing
- Command detection - "start new note" and title capture

## Usage

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / "recorder"))

from recorder_backend import RecorderBackend

def my_handler(event):
    print(f"Event: {event.__class__.__name__}")

backend = RecorderBackend(event_callback=my_handler)
backend.start_recording()
# ... user speaks ...
backend.stop_recording()

print(f"Session: {backend.get_session_path()}")
```

## Event Types

All events inherit from `RecorderEvent` and include a `timestamp` field.

- **RecordingStateChanged** - Recording started/stopped
- **VADModeChanged** - Mode changed (normal/long_note)
- **SpeechDetected** - Speech started (segment index)
- **SpeechEnded** - Speech ended (duration, kept flag)
- **TranscriptionQueued** - Segment queued for transcription
- **TranscriptionComplete** - Transcription finished (text, success)
- **NoteCommandDetected** - "start new note" command heard
- **NoteTitleCaptured** - Note title captured
- **QueueStatus** - Processing queue status (queued, completed)

## Thread Safety

Events are emitted from multiple threads:
- **Audio thread** - SpeechDetected, SpeechEnded
- **Worker processes** - TranscriptionComplete
- **Collector thread** - Command detection, queue status

UI implementations must handle cross-thread event delivery safely.

## Configuration

Backend uses the same config as v2:
- `RECORD_SR = 48000` - Recording sample rate
- `VAD_SR = 16000` - VAD sample rate
- `MIN_SILENCE_MS = 800` - Normal mode silence
- `MIN_SILENCE_MS_LONG = 5000` - Long note mode silence
- `NUM_WORKERS = 2` - Transcription workers
- `WHISPER_MODEL = "models/multilang_whisper_large3_turbo.ggml"`

## File Outputs

Backend creates standard session structure:
- `sessions/YYYYMMDD_HHMMSS/` - Session directory
- `seg_NNNN.wav` - Audio segments
- `transcript_raw.txt` - Final transcript
- `manifest.json` - Session metadata

## UI Implementations

Current UIs using this backend:
- `tui/recorder_tui.py` - Textual-based terminal UI

Future UIs can import the same backend with zero modifications.
