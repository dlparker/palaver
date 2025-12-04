# TUI Evaluation and Refactoring Plan

## Current State Analysis

### What Exists

**File**: `src/palaver/tui/recorder_tui.py` (387 lines)

**Status**:
- ❌ Never tested
- ❌ Imports from deleted `recorder_backend_async.py`
- ❌ Cannot currently run
- ✅ Well-designed UI components
- ✅ Good reactive architecture

### Code Quality Assessment

#### ✅ Strengths (Worth Keeping)

1. **Clean Component Design**
   - `RecordButton` - Modal record/stop button
   - `ModeDisplay` - Shows VAD mode (normal/long_note) with visual indicators
   - `StatusDisplay` - Session info, segment count, queue status
   - `TranscriptMonitor` - Real-time transcript with 20-line rolling buffer
   - `NotificationDisplay` - Event notifications with 5-item rolling buffer

2. **Good UX Patterns**
   - Keyboard bindings (SPACE to toggle, Q to quit, C to clear)
   - Visual feedback (colors, emoji indicators)
   - Modal state management (recording/stopped)
   - Reactive UI updates

3. **Well-Structured CSS**
   - Proper layout with containers
   - Responsive sizing
   - Clear visual hierarchy

4. **Event-Driven Architecture**
   - Async event handling (`handle_recorder_event`)
   - Clean separation between backend and UI
   - Non-blocking design

#### ❌ Problems (Must Fix)

1. **Broken Imports** (Lines 24-36)
   ```python
   from palaver.recorder.recorder_backend_async import (
       AsyncRecorderBackend,  # This file was deleted
       RecorderEvent,
       RecordingStateChanged,
       # ... etc
   )
   ```
   **Impact**: Won't run at all

2. **Event Type Mismatch**
   - Old backend had detailed event types (`NoteCommandDetected`, `NoteTitleCaptured`, `QueueStatus`)
   - New `AsyncVADRecorder` has event types defined but **doesn't emit them**
   - Event callback mechanism exists but is unused

3. **No Tests**
   - Never been run
   - No integration with Textual's testing framework
   - Can't verify functionality

4. **No Simulated Mode Integration**
   - Can't test without real audio
   - No way to run fast tests

## Recommendation: **KEEP and REFACTOR** ✅

The UI design is **excellent** and worth preserving. However, it needs significant refactoring to work with the new async architecture.

### Why Keep It

1. **Good UX design** - Well thought out interface
2. **Solid architecture** - Component-based, reactive, event-driven
3. **Ready for async** - Already designed for async/await
4. **Most work is done** - UI components are complete
5. **Strategic value** - TUI enables hands-free operation (the whole point!)

### Why Not Just Discard

Starting from scratch would mean:
- Redesigning the entire UI layout (days of work)
- Rebuilding all reactive components
- Recreating CSS styling
- Redoing keyboard bindings
- Losing good UX decisions already made

**Estimated effort to rewrite from scratch**: 2-3 days
**Estimated effort to refactor**: 4-6 hours

## Refactoring Plan

### Phase 1: Update Event System (1-2 hours)

**Problem**: `AsyncVADRecorder` has events defined but doesn't emit them.

**Solution**: Add event callback support to `AsyncVADRecorder`.

#### Step 1.1: Add event callback parameter

```python
# async_vad_recorder.py

class AsyncVADRecorder:
    def __init__(self, event_callback: Optional[Callable] = None):
        self.event_callback = event_callback
        # ... rest of init
```

#### Step 1.2: Emit events throughout recorder

Add event emissions at key points:

```python
# In start_recording()
if self.event_callback:
    await self._emit_event(RecordingStateChanged(
        timestamp=time.time(),
        is_recording=True
    ))

# In _audio_callback() when speech starts
if self.event_callback:
    asyncio.run_coroutine_threadsafe(
        self._emit_event(SpeechDetected(...)),
        self.loop
    )

# In _process_events() when transcription completes
if self.event_callback:
    await self._emit_event(TranscriptionComplete(...))
```

#### Step 1.3: Helper method for emission

```python
async def _emit_event(self, event: AudioEvent):
    """Emit event to callback if provided"""
    if self.event_callback:
        if asyncio.iscoroutinefunction(self.event_callback):
            await self.event_callback(event)
        else:
            self.event_callback(event)
```

### Phase 2: Update TUI Imports (30 minutes)

**File**: `src/palaver/tui/recorder_tui.py`

#### Step 2.1: Fix imports

```python
# OLD (broken)
from palaver.recorder.recorder_backend_async import (
    AsyncRecorderBackend,
    RecorderEvent,
    # ...
)

# NEW (working)
from palaver.recorder.async_vad_recorder import (
    AsyncVADRecorder,
    AudioEvent,
    SpeechStarted,
    SpeechEnded,
    # ... add any missing event types
)
```

#### Step 2.2: Map old event types to new ones

Some events from old backend may not exist. Need to either:
1. Add them to `async_vad_recorder.py`, or
2. Adapt TUI to work without them

**Missing events to add**:
- `RecordingStateChanged` - Add to async_vad_recorder
- `VADModeChanged` - Add to async_vad_recorder
- `TranscriptionComplete` - Already exists (use from transcription.py)
- `NoteCommandDetected` - Add to async_vad_recorder or text_processor
- `NoteTitleCaptured` - Add to async_vad_recorder or text_processor
- `QueueStatus` - Add to async_vad_recorder

#### Step 2.3: Update RecorderApp init

```python
# OLD
self.backend = AsyncRecorderBackend(event_callback=self.handle_recorder_event)

# NEW
self.backend = AsyncVADRecorder(event_callback=self.handle_recorder_event)
```

### Phase 3: Add Textual Tests (1-2 hours)

**New file**: `tests/test_recorder_tui.py`

Textual provides `App.run_test()` for testing:

```python
"""
tests/test_recorder_tui.py
Tests for TUI using Textual's testing framework
"""

import pytest
from pathlib import Path
from textual.widgets import Button

from palaver.tui.recorder_tui import RecorderApp


class TestRecorderTUI:
    """Test TUI with simulated recorder"""

    async def test_app_starts(self):
        """Test that app starts and renders"""
        app = RecorderApp()

        async with app.run_test() as pilot:
            # Verify main widgets exist
            assert app.query_one("#record-button")
            assert app.query_one("#mode-display")
            assert app.query_one("#status-display")

    async def test_record_button_toggle(self):
        """Test record button toggles state"""
        app = RecorderApp()

        async with app.run_test() as pilot:
            # Initially not recording
            button = app.query_one("#record-button", Button)
            assert "START" in button.label

            # Click to start recording
            await pilot.click("#record-button")
            await pilot.pause(0.5)

            # Should now show STOP
            assert "STOP" in button.label

            # Click to stop
            await pilot.click("#record-button")
            await pilot.pause(0.5)

            # Should show START again
            assert "START" in button.label

    async def test_keyboard_shortcuts(self):
        """Test keyboard bindings"""
        app = RecorderApp()

        async with app.run_test() as pilot:
            # Press SPACE to start recording
            await pilot.press("space")
            await pilot.pause(0.5)

            button = app.query_one("#record-button", Button)
            assert "STOP" in button.label

            # Press SPACE again to stop
            await pilot.press("space")
            await pilot.pause(0.5)

            assert "START" in button.label

    async def test_mode_display_updates(self):
        """Test that mode display shows correct mode"""
        app = RecorderApp()

        async with app.run_test() as pilot:
            mode_display = app.mode_display

            # Initially normal mode
            assert mode_display.mode == "normal"

            # Simulate mode change event
            from palaver.recorder.async_vad_recorder import VADModeChanged
            event = VADModeChanged(
                timestamp=0.0,
                mode="long_note"
            )
            app._handle_event_on_ui_thread(event)

            # Should update to long_note
            assert mode_display.mode == "long_note"

    async def test_transcript_updates(self):
        """Test transcript monitor receives updates"""
        app = RecorderApp()

        async with app.run_test() as pilot:
            transcript = app.transcript_monitor

            # Initially empty
            assert len(transcript.transcript_lines) == 0

            # Add some lines
            transcript.add_line(0, "First segment text")
            transcript.add_line(1, "Second segment text")

            # Verify lines added
            assert len(transcript.transcript_lines) == 2
            assert "First segment" in transcript.transcript_lines[0]
            assert "Second segment" in transcript.transcript_lines[1]

    async def test_notifications(self):
        """Test notification system"""
        app = RecorderApp()

        async with app.run_test() as pilot:
            notif = app.notification_display

            # Initially has welcome message
            assert len(notif.notifications) > 0

            # Add notification
            notif.add_notification("Test notification", "bold red")

            # Verify added
            assert any("Test notification" in str(n) for n in notif.notifications)

            # Clear notifications
            notif.clear()
            assert len(notif.notifications) == 0
```

### Phase 4: Simulated Mode Integration (1-2 hours)

**Goal**: Allow TUI to work with simulated recorder for testing.

#### Option A: Mock Recorder for TUI Testing

Create a mock recorder that emits events without actual recording:

```python
# tests/mocks/mock_recorder.py

class MockAsyncVADRecorder:
    """Mock recorder for TUI testing"""

    def __init__(self, event_callback=None):
        self.event_callback = event_callback
        self.is_recording = False
        self.session_dir = Path("sessions/mock_session")

    async def start_recording(self, **kwargs):
        self.is_recording = True
        await self._emit_event(RecordingStateChanged(
            timestamp=time.time(),
            is_recording=True
        ))

    async def stop_recording(self):
        self.is_recording = False
        await self._emit_event(RecordingStateChanged(
            timestamp=time.time(),
            is_recording=False
        ))
        return self.session_dir

    async def simulate_speech_segment(self, text: str, duration: float):
        """Simulate a speech segment for testing"""
        # Emit speech start
        await self._emit_event(SpeechDetected(...))

        # Emit speech end
        await self._emit_event(SpeechEnded(...))

        # Emit transcription
        await self._emit_event(TranscriptionComplete(...))

    async def _emit_event(self, event):
        if self.event_callback:
            await self.event_callback(event)
```

#### Option B: Real Recorder with Simulated Mode

Modify `AsyncVADRecorder` to support a "simulated" parameter that skips audio:

```python
class AsyncVADRecorder:
    def __init__(self, event_callback=None, simulated=False):
        self.event_callback = event_callback
        self.simulated = simulated
        # ...

    async def start_recording(self, input_source=None, **kwargs):
        if self.simulated:
            # Don't actually start audio
            self.is_recording = True
            await self._emit_event(RecordingStateChanged(...))
            return

        # Real recording...
```

**Recommendation**: Use **Option A (Mock Recorder)** for pure TUI tests, keep real recorder for integration tests.

### Phase 5: Integration Test (1 hour)

**New file**: `tests/test_tui_integration.py`

Test TUI with real simulated recorder:

```python
"""
tests/test_tui_integration.py
Integration tests for TUI with simulated recorder
"""

import pytest
from palaver.tui.recorder_tui import RecorderApp
from tests.mocks.mock_recorder import MockAsyncVADRecorder


async def test_full_recording_workflow():
    """Test complete recording workflow with simulated segments"""

    # Create app with mock recorder
    app = RecorderApp()
    app.backend = MockAsyncVADRecorder(event_callback=app.handle_recorder_event)

    async with app.run_test() as pilot:
        # Start recording
        await pilot.press("space")
        await pilot.pause(0.1)

        # Simulate segments
        await app.backend.simulate_speech_segment("start a new note", 1.5)
        await pilot.pause(0.1)

        await app.backend.simulate_speech_segment("My Note Title", 2.0)
        await pilot.pause(0.1)

        await app.backend.simulate_speech_segment("Note body text", 3.0)
        await pilot.pause(0.1)

        # Verify UI updates
        assert app.status_display.total_segments == 3
        assert len(app.transcript_monitor.transcript_lines) == 3
        assert "My Note Title" in str(app.transcript_monitor.transcript_lines)

        # Stop recording
        await pilot.press("space")
        await pilot.pause(0.1)

        # Verify stopped
        assert not app.backend.is_recording
```

## Implementation Timeline

| Phase | Task | Time | Dependencies |
|-------|------|------|--------------|
| 1 | Add event emission to AsyncVADRecorder | 1-2h | None |
| 2 | Update TUI imports and integration | 30m | Phase 1 |
| 3 | Add Textual tests | 1-2h | Phase 2 |
| 4 | Create mock recorder for testing | 1-2h | Phase 2 |
| 5 | Integration tests | 1h | Phases 3-4 |

**Total**: 4.5 - 7.5 hours

## Event Types Needed

The TUI expects these events (need to ensure they exist):

### Currently in async_vad_recorder.py
- ✅ `AudioEvent` (base class)
- ✅ `SpeechStarted`
- ✅ `SpeechEnded`
- ✅ `ModeChangeRequested`
- ✅ `AudioChunk`

### Need to Add
- ❌ `RecordingStateChanged(is_recording: bool)`
- ❌ `VADModeChanged(mode: str)`
- ❌ `TranscriptionQueued(segment_index, wav_path)`
- ❌ `TranscriptionComplete(segment_index, text, success, processing_time)`
- ❌ `NoteCommandDetected()`
- ❌ `NoteTitleCaptured(title: str)`
- ❌ `QueueStatus(queued_jobs, completed_transcriptions)`

These can be added to `async_vad_recorder.py` or kept in a separate `tui_events.py` module.

## Alternative: Discard and Rebuild Later

If we decide **NOT** to refactor now:

### Pros
- Focus on core functionality first
- Build TUI when we know exactly what we need
- Less technical debt from adapting old code

### Cons
- Lose good UX design decisions
- Waste existing work (~2 days of UI design)
- Delay hands-free operation capability
- Have to recreate the same components later

**Estimated cost of discarding**: 2-3 days to rebuild later

## Final Recommendation

**KEEP and REFACTOR** the TUI code. Here's why:

1. **ROI is positive**: 5-8 hours to refactor vs 2-3 days to rebuild
2. **Good foundation**: UI design is solid and thought-out
3. **Strategic value**: Hands-free operation is a core use case
4. **Learning opportunity**: Forces us to properly implement event system
5. **Testing infrastructure**: Will result in better tests for recorder

### Immediate Next Steps

1. **Phase 1**: Add event callback support to `AsyncVADRecorder` (2 hours)
2. **Phase 2**: Update TUI imports (30 minutes)
3. **Phase 3**: Add one basic test to verify it works (1 hour)

If that works well, continue with Phases 4-5.

If it's more trouble than expected, we can still discard with only ~3 hours invested.

## Decision Point

After Phase 3 (basic functionality working), evaluate:
- Did the refactoring go smoothly?
- Is the code maintainable?
- Are tests working well?

If **yes** → Continue with Phases 4-5
If **no** → Discard and note lessons for future rebuild

## Code to Keep vs Replace

### Keep (Good Quality)
- ✅ All UI components (RecordButton, ModeDisplay, etc.)
- ✅ CSS styling
- ✅ Keyboard bindings
- ✅ Layout structure
- ✅ Event handler pattern

### Replace/Update
- ❌ Import statements (broken)
- ❌ Backend initialization (needs AsyncVADRecorder)
- ❌ Event type mappings (needs new event types)
- 🔄 Event handler logic (may need tweaks)

## Risks and Mitigations

### Risk 1: Event system is complex
**Mitigation**: Start with minimal events, add more as needed

### Risk 2: Textual testing is hard
**Mitigation**: Start with simple component tests, not full integration

### Risk 3: Takes longer than expected
**Mitigation**: Set 8-hour budget; if exceeded, discard and rebuild later

### Risk 4: Bugs in existing UI code
**Mitigation**: Tests will reveal bugs; fix or note for rebuild

## Success Criteria

After refactoring, we should have:
- ✅ TUI runs without errors
- ✅ Record button starts/stops recording
- ✅ Mode display shows correct mode
- ✅ Transcript updates in real-time
- ✅ At least 5 passing tests
- ✅ Works with mock recorder

If we achieve these, the refactoring is successful.
