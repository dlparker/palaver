# TUI Split Transcript Layout

**Date:** 2025-12-04
**Feature:** Split transcript display (current note vs completed note titles)
**Status:** ✅ COMPLETE

---

## Overview

Redesigned the TUI transcript display to show two panels side-by-side:
- **Left panel (2/3 width)**: Current note transcript (same behavior as before)
- **Right panel (1/3 width)**: List of completed note titles from this session

This provides better context during recording sessions with multiple notes.

---

## Requirements

1. ✅ Split transcript into left (current) and right (note titles)
2. ✅ Clear left side when new note starts
3. ✅ Show completed note titles on right side
4. ✅ Most recent note title visible at bottom
5. ✅ Make transcript area consume all available vertical space (dynamic sizing)

---

## Implementation

### New Widget Classes

**CurrentTranscriptMonitor** (renamed from TranscriptMonitor)
```python
class CurrentTranscriptMonitor(Static):
    """Display real-time transcript for current note"""

    def __init__(self):
        super().__init__()
        self.transcript_lines = []

    def add_line(self, segment_index: int, text: str, status: str = "✓"):
        """Add transcript line"""
        self.transcript_lines.append(f"{status} {segment_index + 1}. {text}")
        # Keep last 50 lines (will scroll)
        if len(self.transcript_lines) > 50:
            self.transcript_lines = self.transcript_lines[-50:]
        self.update_display()

    def clear(self):
        """Clear transcript"""
        self.transcript_lines = []
        self.update_display()
```

**NoteTitlesMonitor** (new widget)
```python
class NoteTitlesMonitor(Static):
    """Display titles of completed notes"""

    def __init__(self):
        super().__init__()
        self.note_titles = []

    def add_note(self, title: str):
        """Add a completed note title"""
        self.note_titles.append(title)
        self.update_display()

    def update_display(self):
        """Refresh display"""
        if not self.note_titles:
            content = "[No notes yet]"
        else:
            # Show all note titles, numbered
            lines = []
            for i, title in enumerate(self.note_titles, 1):
                # Truncate long titles
                display_title = title[:50] + "..." if len(title) > 50 else title
                lines.append(f"{i}. {display_title}")
            content = "\n".join(lines)

        self.update(Panel(content, title="Completed Notes", border_style="blue"))
```

### CSS Updates

```python
#transcript-section {
    height: 1fr;  # Consumes all available vertical space
    margin-bottom: 1;
}

#transcript-row {
    width: 100%;
    height: 100%;
}

#current-transcript {
    width: 2fr;  # 2/3 of width
    height: 100%;
    margin-right: 1;
}

#note-titles {
    width: 1fr;  # 1/3 of width
    height: 100%;
}
```

### Layout Structure

```python
def compose(self) -> ComposeResult:
    """Build UI"""
    # ... header and controls ...

    with Container(id="transcript-section"):
        with Horizontal(id="transcript-row"):
            with ScrollableContainer(id="current-transcript"):
                self.current_transcript = CurrentTranscriptMonitor()
                yield self.current_transcript

            with ScrollableContainer(id="note-titles"):
                self.note_titles = NoteTitlesMonitor()
                yield self.note_titles

    # ... notification section ...
```

### Note Tracking State

Added state variables to `RecorderApp.__init__()`:
```python
def __init__(self):
    super().__init__()
    self.backend = AsyncVADRecorder(event_callback=self.handle_recorder_event)
    self.current_segment = -1
    self.current_note_title = None  # Track current note title
    self.in_note_mode = False  # Track if we're currently in a note
```

### Event Handler Updates

**Recording Start** - Clear both panels:
```python
if event.is_recording:
    self.status_display.session_path = self.backend.session_dir
    self.current_transcript.clear()  # Clear current transcript
    self.note_titles.clear()  # Clear completed notes list
    self.in_note_mode = False
    self.current_note_title = None
```

**Recording Stop** - Save current note if incomplete:
```python
else:
    # If we have a current note that wasn't completed, add it
    if self.in_note_mode and self.current_note_title:
        self.note_titles.add_note(self.current_note_title)
```

**Note Command Detected** - Clear current transcript, save previous note:
```python
elif isinstance(event, NoteCommandDetected):
    # If we were already in a note, save it before starting new one
    if self.in_note_mode and self.current_note_title:
        self.note_titles.add_note(self.current_note_title)

    # Clear current transcript for new note
    self.current_transcript.clear()
    self.in_note_mode = True
    self.current_note_title = None

    self.notification_display.add_notification(
        "📝 NEW NOTE DETECTED - Speak title next...",
        "bold yellow"
    )
```

**Note Title Captured** - Save title for current note:
```python
elif isinstance(event, NoteTitleCaptured):
    # Save the title for this note
    self.current_note_title = event.title

    self.notification_display.add_notification(
        f"📌 TITLE: {event.title} - Long note mode active, continue speaking...",
        "bold cyan"
    )
```

---

## Visual Layout

```
┌─────────────────────────────────────────────────────────────┐
│ Header                                                      │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  [⏺ START RECORDING]  (large button)                       │
│                                                             │
│  ┌───────────────────────┐  ┌──────────────────────────┐   │
│  │ Recording Mode        │  │ Status                   │   │
│  │ NORMAL (0.8s silence) │  │ Session: 20251204_143022 │   │
│  └───────────────────────┘  │ Segments: 5              │   │
│                              │ Transcribing: 1          │   │
│                              │ Completed: 4             │   │
│                              └──────────────────────────┘   │
├─────────────────────────────────────────────────────────────┤
│ ┌─────────────────────────┬─────────────────────────────┐  │
│ │ Current Note            │ Completed Notes             │  │
│ │ ─────────────────────   │ ─────────────────────────   │  │
│ │ ✓ 1. Clerk, start a ... │ 1. Meeting with Team Lead   │  │
│ │ ✓ 2. This is the title  │ 2. Project Requirements     │  │
│ │ ✓ 3. Here is the body   │ 3. Action Items for Sprint  │  │
│ │ ⏳ 4. [Processing...]    │ [Most recent at bottom]     │  │
│ │                         │                             │  │
│ │      (2/3 width)        │      (1/3 width)            │  │
│ │      Scrolls if needed  │      Scrolls if needed      │  │
│ └─────────────────────────┴─────────────────────────────┘  │
│                                                             │
│  (This section dynamically expands to fill vertical space)  │
├─────────────────────────────────────────────────────────────┤
│ Notifications                                               │
│ ─────────────────────────────────────────────               │
│ 📌 TITLE: This is the title - Long note mode active...      │
│                                                             │
└─────────────────────────────────────────────────────────────┘
│ Footer (keybindings)                                        │
└─────────────────────────────────────────────────────────────┘
```

---

## Behavior Summary

### When Recording Starts
1. Both panels are cleared
2. Left panel shows "[No segments yet]"
3. Right panel shows "[No notes yet]"

### During Normal Recording
- Left panel accumulates transcript lines for all segments
- Right panel remains empty (no notes yet)

### When "Start New Note" Detected
1. If there was a previous note in progress, its title is moved to right panel
2. Left panel is CLEARED for new note
3. Notification shows "📝 NEW NOTE DETECTED"

### When Note Title Captured
1. Title is saved in `self.current_note_title`
2. Notification shows "📌 TITLE: {title}"
3. Mode switches to long_note

### During Note Body
- Left panel shows transcript lines for note body
- Right panel shows previously completed note titles
- Current note title is NOT yet in right panel (still in progress)

### When Note Completes
- Notification shows "✓ Note complete, normal mode restored"
- Current note title stays in memory (will be added when next note starts OR recording stops)

### When Recording Stops
- If there's a current note in progress, its title is added to right panel
- Session directory is saved

---

## Benefits

### Better Context
- ✅ Users can see all completed note titles at a glance
- ✅ Current note transcript is isolated and clear
- ✅ Easy to track progress through multiple notes in one session

### Improved UX
- ✅ Left panel clears when new note starts (less clutter)
- ✅ Right panel provides session history
- ✅ Most recent note visible at bottom (natural scrolling)

### Dynamic Sizing
- ✅ Transcript area expands to fill available vertical space
- ✅ Responsive to terminal resizing
- ✅ Scrolling works independently in both panels

---

## Testing

### Import Test
```bash
PYTHONPATH=src uv run python -c "from palaver.tui.recorder_tui import RecorderApp; print('TUI imports successfully')"
```
**Result:** ✅ No errors

### Manual Testing Steps
```bash
PYTHONPATH=src uv run python src/palaver/tui/recorder_tui.py
```

**Test Scenario:**
1. Start recording
2. Say "start a new note"
3. Say title: "First Note Title"
4. Speak note body (multiple sentences)
5. Wait 5+ seconds for note to complete
6. Say "start a new note"
7. Say title: "Second Note Title"
8. Speak note body
9. Stop recording

**Expected Results:**
- ✅ Left panel shows current note transcript
- ✅ Left panel clears when new note starts
- ✅ Right panel accumulates note titles ("First Note Title", "Second Note Title")
- ✅ Most recent title visible at bottom
- ✅ Both panels scroll independently
- ✅ Transcript area fills vertical space

---

## Files Modified

**src/palaver/tui/recorder_tui.py**
- Created `CurrentTranscriptMonitor` class (renamed from `TranscriptMonitor`)
- Created `NoteTitlesMonitor` class (new)
- Updated CSS for split layout (2fr:1fr ratio)
- Modified `compose()` to use `Horizontal` layout with two `ScrollableContainer`s
- Added `current_note_title` and `in_note_mode` state tracking
- Updated event handlers for `RecordingStateChanged`, `NoteCommandDetected`, `NoteTitleCaptured`
- Updated all references from `transcript_monitor` to `current_transcript`

---

## Summary

**Problem:** Single transcript display became cluttered during multi-note sessions; no visibility into completed notes.

**Solution:**
- Split transcript into two panels (current note vs completed titles)
- Clear current panel when new note starts
- Track and display completed note titles
- Dynamic vertical sizing with `height: 1fr`

**Result:** Clear, organized UI that provides context during multi-note recording sessions.

**Status:** Complete and ready for testing.
