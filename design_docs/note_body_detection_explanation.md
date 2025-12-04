# Note Body End Detection - How It Works

## Overview

The detection of the end of a note body is **purely VAD-based** (silence detection), NOT content-based. The transcription is only used to trigger mode changes, not to detect the end of content.

## The Complete Workflow

### Phase 1: Normal Recording Mode
**VAD threshold:** 0.8 seconds of silence
**Location:** `vad_recorder.py:291, 297`

```
User speaks → VAD detects speech → Segment created → Transcription queued
```

### Phase 2: "Start New Note" Detection
**Location:** `vad_recorder.py:227-233` (ResultCollector._write_incremental)

1. **Transcription completes** for a segment
2. **ResultCollector checks** if text contains "start new note"
3. **If found:**
   - Set `waiting_for_title = True`
   - Print "📝 NEW NOTE DETECTED"
   - **Note:** Mode hasn't changed yet! Still in normal mode (0.8s)

### Phase 3: Title Capture
**Location:** `vad_recorder.py:236-246`

1. **Next segment transcription completes**
2. **ResultCollector sees** `waiting_for_title == True`
3. **Actions:**
   - Capture text as `current_note_title`
   - Call `mode_change_callback("long_note")` → calls `switch_vad_mode()`
   - Print "📌 TITLE: ..." and "🎙️ LONG NOTE MODE ACTIVATED"

### Phase 4: Mode Change Application
**Location:** `vad_recorder.py:306-319`

**Important:** Mode changes are **queued**, not immediate!

```python
switch_vad_mode("long_note"):
    vad_mode_requested = "long_note"  # Queue the change
    print("Mode change queued: long_note (will apply after current segment)")

apply_vad_mode_change():  # Called at segment boundaries
    if vad_mode_requested:
        vad_mode = vad_mode_requested
        vad = create_vad(vad_mode)  # Create new VAD with 5s threshold
```

**When applied:** At the **START** of the next speech segment (line 355)
- Just before `segments.append([])` to start a new segment
- Ensures entire segment uses the new threshold

### Phase 5: Long Note Recording
**VAD threshold:** 5 seconds of silence
**Location:** `vad_recorder.py:295-304`

```
User speaks body → VAD detects speech → Segments created → Transcription happens
                                     ↑
                            Using 5-second silence threshold
```

**Key difference:**
- Normal mode: 0.8s silence → segment ends
- Long note mode: **5s silence** → segment ends

### Phase 6: Note Body End Detection ⭐
**Location:** `vad_recorder.py:373-379`

**THIS IS THE CRITICAL PART:**

```python
if window.get("end") is not None:  # VAD detected silence threshold exceeded
    in_speech = False
    if segments and segments[-1]:
        seg = np.concatenate(segments[-1])
        dur = len(seg) / RECORD_SR
        if dur >= MIN_SEG_SEC:
            save_and_queue_segment(len(segments) - 1, seg)

            # ⭐ HERE IS WHERE NOTE BODY ENDS ⭐
            if vad_mode == "long_note":
                switch_vad_mode("normal")
                print("🎙️ WILL RESTORE NORMAL MODE after this segment")
```

**The detection logic:**
1. VAD detects **5+ seconds of silence** (because we're in long_note mode)
2. Speech segment ends (window.get("end"))
3. Segment is saved
4. **Automatic check:** "Are we in long_note mode?"
5. **If yes:** Queue switch back to normal mode
6. **Next segment** will use 0.8s threshold again

## Key Insights

### ✅ What Drives the Note End
- **Only VAD silence detection** (5 seconds in long_note mode)
- **NOT** any keyword or transcription content
- **NOT** manual user action

### ✅ When Mode Changes Happen
1. **Transcription triggers mode requests** (async, whenever transcription completes)
2. **Audio callback applies mode changes** (sync, at segment boundaries)
3. This prevents race conditions and ensures clean segment boundaries

### ✅ The Problem You Observed with Microphone
**Possible issues:**
1. **Ambient noise** preventing 5 seconds of true silence
2. **Mic sensitivity** picking up background sounds
3. **VAD threshold** (0.5) too sensitive for your environment
4. **Audio hardware** not properly muting between sentences

**Why file input might work better:**
- File has **perfect silence** (digital zeros) for 6 seconds
- No ambient noise, no mic artifacts
- Deterministic, repeatable

## Timeline Example (with piper.sh test file)

```
Time    Event                           VAD Mode    Threshold   Action
────────────────────────────────────────────────────────────────────────
0.0s    "Clerk, start a new note"       normal      0.8s
0.8s    Silence → segment end           normal      0.8s        Save seg 1
        Transcription: detect command   normal      -           Queue: long_note

2.0s    "Clerk, This is the title"      normal      0.8s
2.8s    Silence → segment end           normal      0.8s        Save seg 2
        Apply mode change               →long_note  5s          Create new VAD
        Transcription: capture title    long_note   -

8.8s    "Clerk, body, first sentence"   long_note   5s
14.8s   Silence → 5s NOT reached        long_note   5s          Still in speech

16.0s   "Stop"                          long_note   5s
22.0s   Silence → 6s > 5s threshold     long_note   5s          ⭐ END DETECTED
        Segment ends                    long_note   5s          Save seg 4
        Check: in long_note? YES!       long_note   -           Queue: normal
        Apply mode change               →normal     0.8s        Restore normal
```

## Testing Verification

To verify this works correctly in your test:
1. ✅ Check console output for "🎙️ WILL RESTORE NORMAL MODE" message
2. ✅ Verify it appears after the last segment ("Stop")
3. ✅ Check mode change was queued at the right time
4. ✅ Verify segment count matches expected (4 segments)

## Why This Design?

**Advantages:**
- Simple: no complex NLP to detect "end of note"
- Reliable: based on silence, not speech recognition accuracy
- Natural: user controls by pausing
- Flexible: works for any content

**Disadvantages:**
- Requires **actual silence** (problematic with noisy microphones)
- Fixed threshold (can't adapt to speaking pace)
- User must remember to pause for 5 seconds

## Recommendation

If microphone test still doesn't work after file test succeeds:
1. Test in **very quiet room**
2. Try **increasing MIN_SILENCE_MS_LONG** to 7000-8000ms
3. Consider **lowering VAD_THRESHOLD** (0.5 → 0.3) for less sensitivity
4. Check **DEVICE** setting matches your actual microphone
