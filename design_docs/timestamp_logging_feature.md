# Event Timestamp Logging Feature

**Date:** 2025-12-04
**Feature:** Unix timestamps and offset display for events
**Status:** ✅ COMPLETE

---

## Overview

Added unix timestamps to all events and enhanced `direct_recorder.py` to display events with time offsets from recording start. This makes it easy to understand event timing and save session traces for debugging.

---

## Changes Made

### 1. Unix Timestamps Already Present

The `AudioEvent` base class already had a `timestamp: float` field that stores Unix time (seconds since epoch). All events were already capturing timestamps when created using `time.time()`.

**No changes needed** - timestamps were already there!

### 2. Enhanced direct_recorder.py

Added event logging with timestamp offsets to help understand timing.

**New Features:**

#### Recording Start Time Capture
```python
RECORDING_START_TIME = time.time()
print(f"📍 Recording start time: {RECORDING_START_TIME:.3f} (unix timestamp)")
```

#### Timestamp Offset Formatter
```python
def format_timestamp_offset(event_timestamp: float) -> str:
    """Format event timestamp as offset from recording start."""
    offset = event_timestamp - RECORDING_START_TIME
    return f"+{offset:06.3f}"  # e.g., "+00.123" or "+12.456"
```

#### Event Logger Callback
```python
async def event_logger(event: AudioEvent):
    """Log events with timestamp offsets."""
    offset = format_timestamp_offset(event.timestamp)

    if isinstance(event, RecordingStateChanged):
        print(f"[{offset}] RecordingStateChanged: {'STARTED' if event.is_recording else 'STOPPED'}")

    elif isinstance(event, SpeechStarted):
        print(f"[{offset}] SpeechStarted: segment={event.segment_index}, mode={event.vad_mode}")

    # ... etc for all event types
```

#### Wire Up Event Logger
```python
recorder = AsyncVADRecorder(event_callback=event_logger)
```

#### New --auto Flag
```python
parser.add_argument("--auto", action="store_true",
                   help="Start recording immediately without waiting for Enter")
```

Useful for automated testing and non-interactive use.

---

## Example Output

### With File Input
```bash
PYTHONPATH=src uv run python scripts/direct_recorder.py --input tests_slow/audio_samples/note1.wav --auto
```

**Output:**
```
📍 Recording start time: 1764883130.422 (unix timestamp)
   Event timestamps will show as offsets: [+SS.sss]

[+00.027] RecordingStateChanged: STARTED
[+00.200] SpeechStarted: segment=0, mode=normal
[+02.473] SpeechEnded: segment=0, duration=2.13s, KEPT
[+02.476] TranscriptionQueued: segment=0, duration=2.13s
[+02.793] SpeechStarted: segment=1, mode=normal
[+05.026] TranscriptionComplete: segment=0, SUCCESS, text="lurk, start a new note."
[+05.026] NoteCommandDetected: segment=0
[+05.191] SpeechEnded: segment=1, duration=2.19s, KEPT
[+05.192] TranscriptionQueued: segment=1, duration=2.19s
[+05.638] SpeechStarted: segment=2, mode=normal
[+07.714] TranscriptionComplete: segment=1, SUCCESS, text="Clerk, this is the title."
[+07.714] NoteTitleCaptured: segment=1, title="Clerk, this is the title."
[+07.717] VADModeChanged: mode=long_note, silence=5000ms
[+07.768] SpeechStarted: segment=3, mode=long_note
[+14.785] VADModeChanged: mode=normal, silence=800ms
[+14.785] SpeechEnded: segment=3, duration=6.57s, KEPT
[+14.788] TranscriptionQueued: segment=3, duration=6.57s
```

### Timing Analysis From Above

- **0-0.2s**: System initialization, recording starts
- **0.2-2.5s**: First segment (command) captured and queued
- **2.5-5.0s**: Transcription happening in background, second segment starts
- **5.0s**: Command detected! "start a new note"
- **5.2-7.7s**: Title segment captured and transcribed
- **7.7s**: Title captured → **immediate mode change to long_note**
- **7.8-14.8s**: Note body (7 seconds of speaking)
- **14.8s**: Mode changes back to normal, note complete

This makes it crystal clear what's happening and when!

---

## Event Types Logged

All event types with formatted output:

1. **RecordingStateChanged** - Shows STARTED/STOPPED
2. **VADModeChanged** - Shows mode and silence threshold
3. **SpeechStarted** - Shows segment index and mode
4. **SpeechEnded** - Shows segment, duration, KEPT/DISCARDED status
5. **TranscriptionQueued** - Shows segment and duration
6. **TranscriptionComplete** - Shows segment, SUCCESS/FAILED, text preview (60 chars)
7. **NoteCommandDetected** - Shows segment where command found
8. **NoteTitleCaptured** - Shows segment and title text

---

## Benefits

### For Debugging
✅ See exact timing of all events
✅ Understand processing delays (e.g., transcription takes 2-3 seconds)
✅ Verify mode changes happen at correct times
✅ Track event ordering

### For Session Traces
✅ Easy to copy/paste output to save session traces
✅ Unix timestamp allows correlation with external logs
✅ Offset format is readable and precise

### For Performance Analysis
✅ Measure end-to-end latency
✅ Identify bottlenecks (e.g., transcription time)
✅ Verify VAD responsiveness

---

## Usage

### Standard Microphone Recording
```bash
./scripts/direct_recorder.py
# Press Enter to start, press Enter to stop
```

### File Input (Auto-start)
```bash
./scripts/direct_recorder.py --input audio.wav --auto
```

### Specific Device
```bash
./scripts/direct_recorder.py --input hw:1,0
```

### Save Session Trace
```bash
./scripts/direct_recorder.py --input audio.wav --auto > session_trace.log 2>&1
```

---

## Implementation Details

### Thread Safety

The event logger is called from the async event loop, not directly from the audio thread or text processor thread. This is safe because:

1. Events are pushed to an asyncio.Queue
2. Event processor (async) pulls from queue
3. Event processor calls `_emit_event()`
4. `_emit_event()` calls the event_callback (our logger)
5. Logger just formats and prints - no shared state modifications

### Timestamp Format

Format: `[+SS.sss]`
- Always shows positive offset from recording start
- Fixed width: 6 digits before decimal, 3 after
- Examples: `[+00.027]`, `[+05.026]`, `[+123.456]`

### Global State

Uses a global `RECORDING_START_TIME` variable:
- Set once when recording starts
- Used by all event log calls
- Simple and effective for CLI tool
- Not thread-safe but doesn't matter (set once, read many)

---

## Testing

### Unit Tests
```bash
uv run pytest tests/ -v
```
**Result:** ✅ All 62 tests pass

### Manual Testing
```bash
PYTHONPATH=src uv run python scripts/direct_recorder.py --input tests_slow/audio_samples/note1.wav --auto
```
**Result:** ✅ All events show with correct offsets

---

## Future Enhancements

### Possible Additions
1. Add event type filtering (--events speech,transcription)
2. Add JSON output format for machine parsing
3. Add CSV format for spreadsheet analysis
4. Add event statistics summary at end
5. Add color coding by event type
6. Add event timeline visualization

### Not Needed
- Custom timestamp formats (current format is ideal)
- Absolute timestamps in output (use Unix timestamp at start if needed)
- High-precision timestamps (milliseconds are sufficient)

---

## Files Modified

1. **scripts/direct_recorder.py**
   - Added `RECORDING_START_TIME` global
   - Added `format_timestamp_offset()` helper
   - Added `event_logger()` callback
   - Added `--auto` flag for non-interactive mode
   - Updated `run_interactive_recording()` to use event logger

**No changes needed** to event classes - timestamps already present!

---

## Summary

**Problem:** Need to understand event timing for debugging and save session traces.

**Solution:**
- Unix timestamps already present in all events ✓
- Added event logger to direct_recorder.py that displays offsets ✓
- Added --auto flag for non-interactive testing ✓

**Result:** Clear, readable timing information that makes debugging easy.

**Example Use Case:**
```bash
# Save a session trace for analysis
./scripts/direct_recorder.py --input my_recording.wav --auto > trace.log 2>&1

# Later, analyze timing:
grep "TranscriptionComplete" trace.log
[+05.026] TranscriptionComplete: segment=0, SUCCESS, text="..."
[+07.714] TranscriptionComplete: segment=1, SUCCESS, text="..."

# Conclusion: Transcription takes ~2.5 seconds per segment
```

**Status:** Ready for use. All tests pass. Feature complete.
