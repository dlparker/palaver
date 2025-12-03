# Long Note Mode Experiment

## Overview

`vad_recorder_v2_long_note.py` is an experimental variant that dynamically adjusts VAD silence detection based on voice commands.

## How It Works

### State Machine

**Normal Mode (Default)**
- Silence threshold: **0.8 seconds**
- Good for short dictation segments
- Visual indicator: `S` during speech

**Title Waiting State (After Command)**
- Triggered by saying "start new note"
- Next segment is captured as the note title
- Stays in normal mode (0.8s silence)
- Prompts you to speak the title

**Long Note Mode (After Title)**
- Silence threshold: **5 seconds**
- Activated after title is captured
- Allows long pauses while thinking/speaking
- Visual indicator: `L` during speech
- Automatically returns to normal after note completes

## Usage

```bash
uv run recorder/vad_recorder_v2_long_note.py
```

### Workflow for Creating a Long Note

**Step 1: Trigger the command**
Say one of these phrases:
- "start new note"
- "clerk start new note" (recommended - "clerk" helps VAD detect start)
- "clark start new note" (misspelling is handled)

You'll see:
```
======================================================================
📝 NEW NOTE DETECTED
Please speak the title for this note...
======================================================================
```

**Step 2: Speak the title**
The next segment you speak will be captured as the title:
```
[Speech start] SSSSSSSS...
[Speech end: 25 chunks, 0.75s] ✓ Segment #X KEPT
```

You'll see:
```
======================================================================
📌 TITLE: My Important Research Ideas
🎙️  LONG NOTE MODE ACTIVATED
Silence threshold: 5 seconds (continue speaking...)
======================================================================
```

**Step 3: Speak the note content**
Now you can speak with long pauses:
```
[Speech start [LONG NOTE]] LLLLLLLLLLLLL...
```

Wait 5+ seconds of silence to complete the note.

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
   → System prompts: "Please speak the title for this note..."

4. Say: "Ideas for the weekend project"
   → System shows: "📌 TITLE: Ideas for the weekend project"
   → Switches to long note mode (5s silence)

5. Say: "I want to build a voice-controlled system...
        [pause 2-3 seconds while thinking]
        ...that can handle multiple microphones
        [pause again]
        ...and process them in parallel..."
   → Can pause up to 5 seconds between thoughts

6. Wait 5+ seconds
   → Note segment completes, mode returns to normal
   → Ready for next command or short dictation
```

## Technical Details

### Command Detection
- Runs in the transcription result collector thread
- Checks each transcribed segment for "start new note"
- Case-insensitive matching
- Handles optional prefix: "clerk" or "clark"

### State Machine
Three states:
1. **Normal**: Default recording mode
2. **Waiting for Title**: After "start new note" detected, next segment is title
3. **Long Note**: After title captured, extended silence threshold active

Transitions:
```
Normal --["start new note"]--> Waiting for Title
Waiting for Title --[next segment]--> Long Note
Long Note --[silence > 5s]--> Normal
```

### VAD Switching (Safe Mode Change)
- Creates new VADIterator with different `min_silence_duration_ms`
- **Mode changes queued, not immediate** - applied at segment boundaries
- Prevents VAD state corruption mid-segment
- Title segment uses normal VAD (0.8s) for clean title capture
- Switches to long mode at START of next segment after title
- Switches back to normal at START of next segment after long note
- Thread-safe using `threading.Lock`

**Why queued?** VADIterator maintains internal state that adapts during recording. Recreating it mid-stream causes:
- Poor speech detection
- More discarded segments
- Loss of calibration

By queueing mode changes and applying them at segment boundaries (when no audio is being processed), we maintain VAD reliability.

### Configuration

```python
MIN_SILENCE_MS = 800        # Normal: 0.8 seconds
MIN_SILENCE_MS_LONG = 5000  # Long note: 5 seconds
```

## Limitations

1. **Transcription lag**: State changes after segment is transcribed (~1-3 seconds delay)
2. **Title must be short**: Title uses 0.8s silence, so keep it brief
3. **One-way trigger**: Can't manually exit long note mode (completes on 5s silence)
4. **Three segments minimum**: Command segment, title segment, note content segment

## Future Enhancements

Ideas for next experiments:
- Multiple modes (short/medium/long)
- "End note" command to manually exit long mode
- Custom silence durations via voice ("use 3 second silence")
- State persistence across segments
- Visual timer showing remaining silence time
