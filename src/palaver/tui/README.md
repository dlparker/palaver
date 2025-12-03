# Palaver TUI - Voice Recorder UI

This directory contains the Textual-based TUI and the UI-agnostic recorder backend.

## Architecture

### Clean Separation of Concerns

```
┌─────────────────────────────────────────────┐
│        tui/recorder_tui.py                   │
│     (Textual UI - can be replaced)          │
│                                              │
│  - RecordButton widget                       │
│  - ModeDisplay widget                        │
│  - TranscriptMonitor widget                  │
│  - NotificationDisplay widget                │
│  - RecorderApp (main app)                    │
└──────────────────┬──────────────────────────┘
                   │
                   │ Event callbacks
                   │ Control methods
                   │
┌──────────────────▼──────────────────────────┐
│     recorder/recorder_backend.py             │
│     (UI-agnostic recording engine)          │
│                                              │
│  - RecorderBackend class                     │
│  - Event system (12 event types)             │
│  - VAD processing                            │
│  - Audio recording                           │
│  - Transcription workers                     │
│  - Command detection                         │
└──────────────────────────────────────────────┘
```

**Note**: The backend lives in `recorder/` directory to be shared across all UIs (TUI, web, CLI, etc.).

### Why This Architecture?

**UI-Agnostic Backend**: `recorder_backend.py` has ZERO dependencies on Textual or any UI framework. It can be used with:
- Textual TUI (current)
- Web UI (Flask/FastAPI)
- GUI (Tkinter/PyQt)
- CLI (simple text interface)
- Programmatic API (for automation)

**Event-Driven**: Backend emits events, UI listens. Backend doesn't know or care who's listening.

## Backend API

### Usage

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "recorder"))

from recorder_backend import RecorderBackend

def my_event_handler(event):
    print(f"Event: {event}")

backend = RecorderBackend(event_callback=my_event_handler)

# Start recording
backend.start_recording()

# ... recording happens ...

# Stop recording
backend.stop_recording()

# Get session path
session = backend.get_session_path()
```

### Event Types

The backend emits 12 event types:

1. **RecordingStateChanged** - Recording started/stopped
2. **VADModeChanged** - Mode switched (normal/long_note)
3. **SpeechDetected** - Speech started
4. **SpeechEnded** - Speech ended (with duration, kept status)
5. **TranscriptionQueued** - Segment queued for transcription
6. **TranscriptionComplete** - Transcription finished (with text)
7. **NoteCommandDetected** - "start new note" detected
8. **NoteTitleCaptured** - Note title captured
9. **QueueStatus** - Processing queue status update

All events include a `timestamp` field.

### Thread Safety

Events are emitted from various threads:
- **Audio callback thread**: SpeechDetected, SpeechEnded
- **Worker processes**: TranscriptionComplete
- **Result collector thread**: All transcription-related events

UI must handle cross-thread event delivery. Textual provides `call_from_thread()` for this.

## TUI Features

### 1. Modal Record/Stop Button
- Large, prominent button
- Press SPACE or click to toggle
- Green "START RECORDING" / Red "STOP RECORDING"

### 2. Recording Mode Display
- Shows current VAD mode:
  - **NORMAL (0.8s silence)** - blue
  - **LONG NOTE (5s silence)** - green
- Shows 🎙️ indicator when speaking

### 3. Status Display
- Session directory name
- Total segments count
- Transcribing queue size
- Completed transcriptions

### 4. Transcript Monitor
- Real-time display of transcribed segments
- Shows last 20 segments (scrollable)
- Status indicators:
  - ⏳ Processing
  - ✓ Success
  - ✗ Failed

### 5. Notification Display
- Voice command alerts:
  - 📝 NEW NOTE DETECTED
  - 📌 TITLE: [title]
- Mode change notifications
- Session start/stop messages
- Last 5 notifications shown

## Usage

```bash
# From tui directory:
uv run python recorder_tui.py

# Or from project root:
uv run python tui/recorder_tui.py
```

### Keybindings

- `SPACE` - Start/Stop recording
- `q` - Quit (stops recording if active)
- `c` - Clear notifications

### Voice Commands

During recording, say:
- **"clerk start new note"** → Triggers title prompt
- **"[title text]"** → Captured as title, switches to long mode
- **[long note content]** → Can pause up to 5 seconds
- **[5s silence]** → Returns to normal mode

## Creating Alternative UIs

### Web UI Example

```python
from recorder_backend import RecorderBackend
from flask import Flask, jsonify
from flask_socketio import SocketIO, emit

app = Flask(__name__)
socketio = SocketIO(app)

backend = None

def handle_event(event):
    """Broadcast events to web clients"""
    socketio.emit('recorder_event', {
        'type': event.__class__.__name__,
        'data': event.__dict__
    })

@app.route('/start', methods=['POST'])
def start():
    global backend
    backend = RecorderBackend(event_callback=handle_event)
    backend.start_recording()
    return jsonify({'status': 'started'})

@app.route('/stop', methods=['POST'])
def stop():
    backend.stop_recording()
    return jsonify({'status': 'stopped'})

if __name__ == '__main__':
    socketio.run(app)
```

### CLI Example

```python
from recorder_backend import RecorderBackend

def simple_handler(event):
    print(f"[{event.__class__.__name__}] {event.__dict__}")

backend = RecorderBackend(event_callback=simple_handler)

print("Press Enter to start...")
input()
backend.start_recording()

print("Recording... Press Enter to stop")
input()
backend.stop_recording()

print(f"Session saved to: {backend.get_session_path()}")
```

## File Outputs

Backend creates:
- `sessions/YYYYMMDD_HHMMSS/` - Session directory
- `seg_NNNN.wav` - Audio segments
- `transcript_raw.txt` - Final transcript
- `manifest.json` - Session metadata

## Dependencies

**Backend only:**
- numpy
- sounddevice
- torch (Silero VAD)
- scipy

**TUI (additional):**
- textual
- rich

The backend has NO Textual dependencies!

## Future UI Options

With this architecture, you can easily add:
- **Voice-only UI**: Speak commands, audio feedback
- **Mobile app**: React Native, Flutter
- **VSCode extension**: Integrate into editor
- **Slack bot**: Recording via chat
- **REST API**: Microservice architecture
- **Desktop GUI**: Electron, PyQt

All use the same `RecorderBackend` class!
