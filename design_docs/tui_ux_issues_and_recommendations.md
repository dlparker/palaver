# TUI UX Issues and Recommendations

**Date:** 2025-12-04
**Status:** Draft for Review
**Related Files:**
- `src/palaver/tui/recorder_tui.py`
- `src/palaver/recorder/async_vad_recorder.py`
- `src/palaver/recorder/text_processor.py`

---

## Executive Summary

The current TUI implementation has several UX issues that make it confusing to use:

1. **Transcript Monitor shows processing messages instead of results** - Users see "[Processing... 2.3s]" but never see the actual transcribed text
2. **Missing transcription completion feedback** - No indication when transcription finishes
3. **Missing note workflow notifications** - No notifications for note start or title capture
4. **Delayed long note mode notification** - Long note mode notification only appears after the note ends

**Root Cause:** The `TextProcessor` class performs command detection and transcription processing but doesn't emit events to the TUI. The TUI only receives events from the audio callback layer, missing all text-processing events.

---

## Current Architecture Analysis

### Event Flow (Current State)

```
Audio Callback (sync)
    ↓
asyncio.Queue
    ↓
Event Processor (async)  →  TUI receives:
    ↓                        - RecordingStateChanged
    ↓                        - VADModeChanged
    ↓                        - SpeechStarted/SpeechEnded
WAV Save + Queue              - TranscriptionQueued
    ↓
Transcription (multiprocess)
    ↓
TextProcessor (thread)       TUI DOES NOT receive:
    - Command detection      ❌ TranscriptionComplete
    - Title capture          ❌ NoteCommandDetected
    - Mode change request    ❌ NoteTitleCaptured
```

### Missing Event Emissions

The following event types are **defined** but **never emitted**:

1. **`TranscriptionComplete`** - When transcription finishes successfully or fails
2. **`NoteCommandDetected`** - When "start new note" command is detected
3. **`NoteTitleCaptured`** - When note title is captured

These events are crucial for TUI feedback but are never sent to the TUI's event callback.

---

## Specific UX Issues

### Issue 1: Transcript Monitor Confusion

**Current Behavior:**
```
Transcript Panel:
⏳ 1. [Processing... 2.3s]
⏳ 2. [Processing... 1.8s]
⏳ 3. [Processing... 3.1s]
```

**User Expectation:**
```
Transcript Panel:
⏳ 1. [Processing... 2.3s]
✓ 1. This is the transcribed text from segment one
⏳ 2. [Processing... 1.8s]
✓ 2. This is the transcribed text from segment two
```

**Problem:**
- The `TranscriptMonitor.add_line()` is called when `SpeechEnded` occurs (showing "Processing...")
- `TranscriptMonitor.update_line()` should be called when `TranscriptionComplete` occurs
- But `TranscriptionComplete` events are never emitted by the backend

**Code Location:** `recorder_tui.py:326-332`
```python
elif isinstance(event, TranscriptionComplete):
    if event.success:
        self.transcript_monitor.update_line(
            event.segment_index,
            event.text[:100],  # Truncate for display
            "✓"
        )
```
This handler exists but is never triggered!

---

### Issue 2: No Transcription Completion Feedback

**Current Behavior:**
- User sees "Processing..." indefinitely
- No indication that transcription completed
- No error notification if transcription fails

**User Expectation:**
- See processing indicator
- See completion indicator (✓)
- See actual transcribed text
- See error indicator (✗) if transcription fails

**Problem:**
- `TranscriptionComplete` event is never emitted
- TUI has handler for this event but it's never called

**Impact:**
- Users don't know if transcription is working
- Users can't verify transcription accuracy in real-time
- No feedback on transcription errors

---

### Issue 3: Missing Note Workflow Notifications

**Current Behavior:**
```
Notifications Panel:
🎙️  Recording started
[user says "start a new note"]
[no notification appears]
[user speaks title]
[no notification appears]
[after ~5 seconds of silence]
🎙️  LONG NOTE MODE (5000ms silence)
```

**User Expectation:**
```
Notifications Panel:
🎙️  Recording started
[user says "start a new note"]
📝 NEW NOTE DETECTED - Speak title next...
[user speaks title]
📌 TITLE: My Important Topic
🎙️  Long note mode activated (5s silence)
[user dictates note body]
[5 seconds silence]
✓ Note completed, normal mode restored
```

**Problem:**
- `NoteCommandDetected` event is never emitted when command is detected
- `NoteTitleCaptured` event is never emitted when title is captured
- These events should be emitted by `TextProcessor._check_commands()` but aren't

**Code Location:** `recorder_tui.py:340-350`
```python
elif isinstance(event, NoteCommandDetected):
    self.notification_display.add_notification(
        "📝 NEW NOTE DETECTED - Speak title next...",
        "bold yellow"
    )

elif isinstance(event, NoteTitleCaptured):
    self.notification_display.add_notification(
        f"📌 TITLE: {event.title}",
        "bold cyan"
    )
```
These handlers exist but are never triggered!

---

### Issue 4: Delayed Long Note Mode Notification

**Current Behavior:**
- User says "start a new note" at t=0s
- User speaks title at t=2s
- Mode switch to long_note is **queued** at t=2s
- User speaks note body from t=4s to t=30s
- Note body ends with 5s silence at t=35s
- `VADModeChanged` event fires at t=35s (when next segment starts)
- Notification appears: "🎙️  LONG NOTE MODE (5000ms silence)"
- User is confused: "I already finished the note!"

**User Expectation:**
- See notification when entering long note mode (immediately after title)
- See notification when note is complete and returning to normal mode

**Problem:**
- `VADModeChanged` events only fire at segment boundaries
- By the time the event fires, the user has already completed the note
- The notification timing doesn't match the user's mental model

**Root Cause:**
- Mode changes are queued and applied at segment boundaries (correct for audio processing)
- But notifications should be sent immediately when mode change is requested (for user feedback)

**Code Location:** `text_processor.py:174`
```python
# Switch to long note mode
self.mode_change_callback("long_note")
print("\n" + "="*70)
print(f"📌 TITLE: {result.text}")
print("🎙️  LONG NOTE MODE ACTIVATED")
print("Silence threshold: 5 seconds (continue speaking...)")
print("="*70 + "\n")
```
This console output works correctly, but TUI doesn't see it!

---

### Issue 5: Status Display Field Mismatch

**Current Behavior:**
```python
# recorder_tui.py:354
self.status_display.completed_transcriptions = event.completed_transcriptions
```

**Problem:**
- `StatusDisplay` has attribute `completed` (line 86)
- Code tries to set `completed_transcriptions` (line 354)
- These don't match, causing silent failure

**Impact:**
- "Completed" counter never updates

---

## Recommended Solutions

### Solution 1: Add Event Emission to TextProcessor

**Approach:** Pass an event callback to `TextProcessor` similar to the recorder's event callback.

**Changes Required:**

1. **Modify `TextProcessor.__init__`** to accept event callback:
```python
def __init__(self,
             session_dir: Path,
             result_queue: Queue,
             mode_change_callback: Optional[Callable[[str], None]] = None,
             event_callback: Optional[Callable] = None):  # NEW
    self.event_callback = event_callback
    # ... rest of init
```

2. **Add event emission helper:**
```python
def _emit_event(self, event):
    """Emit event to callback if provided (thread-safe)."""
    if self.event_callback:
        try:
            self.event_callback(event)
        except Exception as e:
            print(f"[TextProcessor] Event callback error: {e}")
```

3. **Emit `TranscriptionComplete` in `process_result()`:**
```python
def process_result(self, result: TranscriptionResult):
    self.results[result.segment_index] = result
    self._write_incremental(result)

    # Emit TranscriptionComplete event
    if self.event_callback:
        from palaver.recorder.async_vad_recorder import TranscriptionComplete
        import time
        self._emit_event(TranscriptionComplete(
            timestamp=time.time(),
            segment_index=result.segment_index,
            text=result.text,
            success=result.success,
            processing_time_sec=result.processing_time_sec,
            error_msg=result.error_msg
        ))

    self._check_commands(result)
```

4. **Emit `NoteCommandDetected` in `_check_commands()`:**
```python
if not self.waiting_for_title and match_score > 0:
    self.waiting_for_title = True
    print("\n" + "="*70)
    print("📝 NEW NOTE DETECTED")
    # ... existing prints

    # Emit event
    if self.event_callback:
        from palaver.recorder.async_vad_recorder import NoteCommandDetected
        import time
        self._emit_event(NoteCommandDetected(
            timestamp=time.time(),
            segment_index=result.segment_index
        ))
```

5. **Emit `NoteTitleCaptured` in `_check_commands()`:**
```python
elif self.waiting_for_title:
    self.waiting_for_title = False
    self.current_note_title = result.text

    # Emit event BEFORE mode change
    if self.event_callback:
        from palaver.recorder.async_vad_recorder import NoteTitleCaptured
        import time
        self._emit_event(NoteTitleCaptured(
            timestamp=time.time(),
            segment_index=result.segment_index,
            title=result.text
        ))

    # Switch to long note mode
    self.mode_change_callback("long_note")
    # ... existing prints
```

6. **Update `AsyncVADRecorder` to pass callback to TextProcessor:**
```python
# In start_recording()
self.text_processor = TextProcessor(
    session_dir=self.session_dir,
    result_queue=self.transcriber.get_result_queue(),
    mode_change_callback=self._handle_mode_change_request,
    event_callback=self._emit_event_from_thread  # NEW
)
```

7. **Add thread-safe event emission from TextProcessor thread:**
```python
def _emit_event_from_thread(self, event: AudioEvent):
    """
    Emit event from text processor thread (thread-safe).

    TextProcessor runs in a separate thread, so we need to
    schedule the event emission in the main event loop.
    """
    if self.loop:
        asyncio.run_coroutine_threadsafe(
            self._emit_event(event),
            self.loop
        )
```

**Benefits:**
- Minimal changes to existing architecture
- Reuses existing event types and handlers
- Thread-safe event emission
- No breaking changes to API

**Risks:**
- Adds complexity to TextProcessor
- Event callback must be thread-safe
- Requires careful testing of threading

---

### Solution 2: Fix Status Display Field Name

**Simple fix:** Change line 354 in `recorder_tui.py`:

```python
# BEFORE:
self.status_display.completed_transcriptions = event.completed_transcriptions

# AFTER:
self.status_display.completed = event.completed_transcriptions
```

---

### Solution 3: Improve VADModeChanged Notification Timing

**Current Issue:** Notification says "LONG NOTE MODE" after the note is already complete.

**Option A: Change notification text to be retrospective:**
```python
# recorder_tui.py:291-302
elif isinstance(event, VADModeChanged):
    self.mode_display.mode = event.mode
    if event.mode == "long_note":
        self.notification_display.add_notification(
            f"🎙️  Switched to long note mode ({event.min_silence_ms}ms silence)",
            "bold green"
        )
    else:
        self.notification_display.add_notification(
            f"✓ Note complete, normal mode restored ({event.min_silence_ms}ms)",
            "bold blue"
        )
```

**Option B: Add immediate mode change intent notifications:**

With Solution 1 implemented, `NoteTitleCaptured` will fire immediately when title is captured. The notification can say:
- "📌 TITLE: {title} - Long note mode active, continue speaking..."

Then when mode actually changes at segment boundary:
- Don't show notification (user already knows)

**Recommendation:** Use Option B with Solution 1 for better UX.

---

### Solution 4: Enhance Transcript Display

**Additional Improvements:**

1. **Show transcription in progress:**
```python
⏳ 1. [Transcribing... 2.3s audio]
```

2. **Show completion:**
```python
✓ 1. This is the transcribed text from the first segment
```

3. **Show errors clearly:**
```python
✗ 1. [Transcription failed: timeout]
```

4. **Add processing time:**
```python
✓ 1. This is the transcribed text (processed in 1.2s)
```

**Implementation:**
- Already have handlers for `TranscriptionComplete`
- Just need events to be emitted (Solution 1)
- Consider adding processing time to display

---

### Solution 5: Add Queue Status Updates

**Current Issue:** "Transcribing" counter never updates.

**Root Cause:** `QueueStatus` events are never emitted.

**Fix:** Emit `QueueStatus` events from transcriber at regular intervals:

```python
# In transcription.py WhisperTranscriber
def _periodic_status_update(self):
    """Periodically emit queue status (runs in thread)."""
    while self.running:
        time.sleep(1.0)  # Update every second
        if self.event_callback:
            self.event_callback(QueueStatus(
                timestamp=time.time(),
                queued_jobs=self.job_queue.qsize(),
                completed_transcriptions=self.completed_count,
                total_segments=self.total_segments
            ))
```

**Alternative:** Emit `QueueStatus` when jobs are queued/completed:
- In `queue_job()`: emit status after adding to queue
- In `_process_results()`: emit status after completing job

---

## Summary of Changes Required

### Critical (Must Fix):

1. ✅ **Add event callback to TextProcessor** - Enables all other fixes
2. ✅ **Emit TranscriptionComplete events** - Shows transcription results
3. ✅ **Emit NoteCommandDetected events** - Shows note start notification
4. ✅ **Emit NoteTitleCaptured events** - Shows title capture notification
5. ✅ **Fix status display field name** - Makes completed counter work

### Important (Should Fix):

6. ✅ **Improve VADModeChanged notification text** - Less confusing timing
7. ✅ **Add QueueStatus event emission** - Shows transcription queue status

### Nice to Have:

8. ⬜ **Show processing time in transcript** - Better performance feedback
9. ⬜ **Add color coding for transcript status** - Visual clarity
10. ⬜ **Add auto-scroll to transcript** - Always show latest

---

## Implementation Plan

### Phase 1: Event Infrastructure (Critical)
1. Add event callback parameter to `TextProcessor`
2. Add thread-safe event emission helper to `TextProcessor`
3. Wire up event callback in `AsyncVADRecorder.start_recording()`
4. Add thread-safe event forwarder in `AsyncVADRecorder`

**Testing:**
- Verify events are emitted from text processor thread
- Verify events reach TUI callback
- Check for thread safety issues

### Phase 2: Transcription Events (Critical)
1. Emit `TranscriptionComplete` in `TextProcessor.process_result()`
2. Update TUI handler to show truncated text
3. Test with both successful and failed transcriptions

**Testing:**
- Run recorder with file input
- Verify transcript updates from "Processing..." to actual text
- Verify error display for failed transcriptions

### Phase 3: Note Workflow Events (Critical)
1. Emit `NoteCommandDetected` in `TextProcessor._check_commands()`
2. Emit `NoteTitleCaptured` in `TextProcessor._check_commands()`
3. Update notification text for `VADModeChanged` to be less confusing

**Testing:**
- Record "start a new note" → title → body
- Verify notification appears immediately after command
- Verify title notification appears immediately after title
- Verify mode change notification timing makes sense

### Phase 4: Status Display Fixes (Important)
1. Fix field name mismatch in `recorder_tui.py:354`
2. Add `QueueStatus` event emission to transcriber
3. Test queue counters update correctly

### Phase 5: Polish (Nice to Have)
1. Add processing time display
2. Add color coding
3. Add auto-scroll
4. User testing and refinement

---

## Open Questions

1. **Thread Safety:** Should `TextProcessor` event callback use `asyncio.run_coroutine_threadsafe()` or should it handle both sync/async like `AsyncVADRecorder._emit_event()`?
   - **Recommendation:** Use `run_coroutine_threadsafe()` - simpler, always safe

2. **Event Timing:** Should we emit events before or after writing to files?
   - **Recommendation:** Emit after writing - ensures file is ready if TUI wants to read it

3. **Error Handling:** Should failed event callbacks crash the text processor?
   - **Recommendation:** No - catch and log exceptions, continue processing

4. **Backward Compatibility:** Should we maintain compatibility with old code that doesn't provide event callback?
   - **Recommendation:** Yes - make event_callback optional, check if None before emitting

5. **Performance:** Will frequent event emissions impact performance?
   - **Recommendation:** No - events are lightweight, TUI already handles many events from audio callback

---

## Testing Strategy

### Unit Tests:
- `TextProcessor` with mock event callback
- Verify events are emitted at correct times
- Verify event data is correct

### Integration Tests:
- Run TUI with file input
- Verify all UI elements update correctly
- Test note workflow end-to-end
- Test error handling

### Manual Testing:
- Record live with microphone
- Create multiple notes
- Verify notifications are helpful
- Check for timing issues
- Get user feedback on UX improvements

---

## Success Criteria

### User can:
1. ✅ See actual transcribed text in transcript monitor
2. ✅ Know when transcription completes
3. ✅ See notification when "start new note" is detected
4. ✅ See notification when title is captured
5. ✅ Understand when long note mode is active
6. ✅ See queue status (transcribing/completed counts)

### System:
1. ✅ Events are thread-safe
2. ✅ No performance degradation
3. ✅ Backward compatible (event callback optional)
4. ✅ Error handling prevents crashes

---

## Future Enhancements (Out of Scope)

- Real-time partial transcription results (streaming)
- Waveform visualization
- Note editing in TUI
- Playback controls
- Keyboard shortcuts for note workflow
- Session management (load previous sessions)
- Export functionality

---

## Conclusion

The TUI has good foundation but is missing critical event emissions from the text processing layer. The main issue is architectural: `TextProcessor` performs important operations (transcription completion, command detection, title capture) but doesn't communicate these to the TUI.

**The solution is straightforward:** Add event callback support to `TextProcessor` and emit the already-defined event types. All the TUI handlers are already in place, they just need the events to be emitted.

**Estimated Effort:**
- Phase 1-3 (critical fixes): 4-6 hours
- Phase 4-5 (polish): 2-3 hours
- Testing: 2-3 hours
- **Total: 8-12 hours**

**Risk Level:** Low - changes are isolated to event emission, existing functionality unchanged.
