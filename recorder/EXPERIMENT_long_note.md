# Long Note Mode Experiment

## Overview

`vad_recorder_v2_long_note.py` is an experimental variant that dynamically adjusts VAD silence detection based on voice commands.

## How It Works

### Normal Mode (Default)
- Silence threshold: **0.8 seconds**
- Good for short dictation segments
- Visual indicator: `S` during speech

### Long Note Mode (Triggered)
- Silence threshold: **5 seconds**
- Activated when you say "start new note"
- Automatically returns to normal after segment completes
- Visual indicator: `L` during speech

## Usage

```bash
uv run recorder/vad_recorder_v2_long_note.py
```

### Triggering Long Note Mode

Say one of these phrases:
- "start new note"
- "clerk start new note" (recommended - "clerk" helps VAD detect start)
- "clark start new note" (misspelling is handled)

When detected, you'll see:
```
======================================================================
🎙️  LONG NOTE MODE ACTIVATED
Silence threshold: 5 seconds (continue speaking...)
======================================================================
```

### Visual Feedback

**Normal mode:**
```
..........SSSSSSSSSS...........
```

**Long note mode:**
```
..........LLLLLLLLLLLLLLLLLL...........
```

**Mode restored:**
```
======================================================================
🎙️  NORMAL MODE RESTORED
Silence threshold: 0.8 seconds
======================================================================
```

## Example Session

```
1. Press Enter to start recording
2. Say: "clerk, this is a short test"
   → Transcribes normally, 0.8s silence ends segment

3. Say: "clerk start new note"
   → Mode switches to 5s silence

4. Say: "This is a much longer note where I want to pause
        and think between sentences without the recorder
        stopping after just 0.8 seconds of silence..."
   → Can pause up to 5 seconds without ending segment

5. Wait 5+ seconds
   → Segment completes, mode returns to normal
```

## Technical Details

### Command Detection
- Runs in the transcription result collector thread
- Checks each transcribed segment for "start new note"
- Case-insensitive matching
- Handles optional prefix: "clerk" or "clark"

### VAD Switching
- Creates new VADIterator with different `min_silence_duration_ms`
- Thread-safe using `threading.Lock`
- Switches back automatically after long note completes

### Configuration

```python
MIN_SILENCE_MS = 800        # Normal: 0.8 seconds
MIN_SILENCE_MS_LONG = 5000  # Long note: 5 seconds
```

## Limitations

1. **Transcription lag**: Mode switches after segment is transcribed (~1-3 seconds delay)
2. **One-way trigger**: Can't manually exit long note mode (completes on 5s silence)
3. **Command segment**: The trigger phrase itself becomes a short segment

## Future Enhancements

Ideas for next experiments:
- Multiple modes (short/medium/long)
- "End note" command to manually exit long mode
- Custom silence durations via voice ("use 3 second silence")
- State persistence across segments
- Visual timer showing remaining silence time
