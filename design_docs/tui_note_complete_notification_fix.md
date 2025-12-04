# TUI Note Complete Notification Fix

**Date:** 2025-12-04
**Issue:** TUI doesn't show "Note complete" notification when note body ends
**Status:** ✅ FIXED

---

## Problem

After fixing the long note termination issue, the CLI recorder correctly detected the end of note body segments, but the TUI never showed the "✓ Note complete, normal mode restored" notification.

### CLI Output (Working)
```
[Speech end: 336 chunks, 10.08s] ✓ Segment #4 KEPT

[VAD] Mode change queued: normal (will apply after current segment)

======================================================================
🎙  WILL RESTORE NORMAL MODE after this segment
Silence threshold: 0.8 seconds
======================================================================
```

### TUI Behavior (Not Working)
- Mode Display updates to show "normal" mode ✓
- But Notifications panel never shows "✓ Note complete" ✗

---

## Root Cause

The mode change back to normal was being **queued** but the `VADModeChanged` event was only emitted when the next segment started (via `_apply_vad_mode_change()`).

### Event Flow Problem

1. Note body segment ends (5s silence detected)
2. `_switch_vad_mode("normal")` called → queues mode change
3. **No event emitted yet**
4. User doesn't speak again
5. **No next segment starts → no event ever emitted → TUI never notified**

The TUI event handler was correct (lines 291-302 in recorder_tui.py), but it never received the event!

---

## Solution

Emit the `VADModeChanged` event **immediately** when queuing the mode change, not waiting for the next segment to start.

### Code Change

**File:** `src/palaver/recorder/async_vad_recorder.py:624-644`

```python
# BEFORE:
def _switch_vad_mode(self, new_mode: str):
    """Request VAD mode change (will be applied at next segment boundary)."""
    if new_mode != self.vad_mode:
        self.vad_mode_requested = new_mode
        print(f"\n[VAD] Mode change queued: {new_mode} (will apply after current segment)")

# AFTER:
def _switch_vad_mode(self, new_mode: str):
    """
    Request VAD mode change (will be applied at next segment boundary).

    Also emits VADModeChanged event immediately so TUI can show feedback
    even if user doesn't speak again (which would trigger the actual mode change).
    """
    if new_mode != self.vad_mode:
        self.vad_mode_requested = new_mode
        print(f"\n[VAD] Mode change queued: {new_mode} (will apply after current segment)")

        # Emit event immediately so TUI shows "Note complete" notification
        # The actual mode change will happen at next segment boundary
        silence_ms = MIN_SILENCE_MS_LONG if new_mode == "long_note" else MIN_SILENCE_MS
        self._emit_event_threadsafe(VADModeChanged(
            timestamp=time.time(),
            mode=new_mode,
            min_silence_ms=silence_ms
        ))
```

---

## How It Works Now

### Event Flow After Fix

1. Note body segment ends (5s silence detected)
2. `_switch_vad_mode("normal")` called
3. **Immediately emits `VADModeChanged(mode="normal")` event** ✓
4. TUI receives event → shows "✓ Note complete" notification ✓
5. Mode change is still queued for next segment boundary (for safety)

### Note on "Actual" vs "Queued" Mode

The mode change happens in two stages:
- **Event emission:** Immediate (for UI feedback)
- **VAD object recreation:** At next segment boundary (for audio processing safety)

This is safe because:
- The current segment has already ended
- The event informs the TUI that the note is complete
- The actual VAD mode change happens when/if the next segment starts

If the user never speaks again, the queued mode change doesn't matter because there's no audio to process.

---

## Testing

### Test 1: Integration Test
```bash
PYTHONPATH=src uv run python scripts/test_tui_events.py
```

**Result:**
```
Event breakdown:
  VADModeChanged: 2  ✓ (one for long_note, one for normal)
  NoteCommandDetected: 1
  NoteTitleCaptured: 1
  TranscriptionComplete: 2
  ...
```

### Test 2: All Unit Tests
```bash
uv run pytest tests/ -q
```

**Result:** ✅ All 62 tests passed

### Test 3: TUI Manual Test

**Steps:**
1. Run TUI: `PYTHONPATH=src uv run python src/palaver/tui/recorder_tui.py`
2. Say: "start a new note"
3. Say title: "My Important Meeting"
4. Speak note body (multiple sentences)
5. Stop speaking for 5+ seconds
6. **Expected:**
   - Notification: "✓ Note complete, normal mode restored (800ms)"
   - Mode Display: Shows "NORMAL (0.8s silence)"

---

## Summary

**Problem:** TUI never showed "Note complete" notification because `VADModeChanged` event was only emitted when next segment started, but if user didn't speak again, no segment started.

**Solution:** Emit `VADModeChanged` event immediately when queuing mode change back to normal, so TUI gets notified even if user doesn't speak again.

**Impact:**
- TUI now shows "Note complete" notification ✓
- User gets clear feedback that note body has ended ✓
- No impact on audio processing logic ✓
- All tests pass ✓

**Status:** Ready for use.
