#!/usr/bin/env python3
"""
palaver/editors/marker.py
Phase 2 – Simple segment marker for identifying transcription errors

Goal: Quickly mark which segments need re-recording without editing them.
Output: blocks_to_fix.json containing list of segment indices

Usage:
    python marker.py sessions/20251202_194521

Keybindings:
    j / ↓       : Next segment
    k / ↑       : Previous segment
    space / !   : Toggle mark for fixing
    a           : Mark all segments
    c           : Clear all marks
    s           : Save and quit
    q           : Quit without saving
    p           : Play current segment audio (if aplay available)
"""

from pathlib import Path
import json
import sys
import subprocess
from datetime import datetime, timezone
from textual.app import App, ComposeResult
from textual.containers import Container, Vertical, Horizontal
from textual.widgets import Header, Footer, Static, Label
from textual.binding import Binding
from rich.text import Text
from rich.panel import Panel
from rich.console import RenderableType

class SegmentDisplay(Static):
    """Widget to display a single segment with mark status"""

    def __init__(self, index: int, text: str, marked: bool = False, **kwargs):
        super().__init__(**kwargs)
        self.index = index
        self.text = text
        self.marked = marked
        self.is_current = False

    def render(self) -> RenderableType:
        # Build the display text
        mark = "[!]" if self.marked else "[ ]"
        num = f"{self.index + 1:3d}"

        # Color coding
        if self.is_current:
            mark_style = "bold yellow" if self.marked else "bold white"
            text_style = "bold white"
        else:
            mark_style = "red" if self.marked else "dim"
            text_style = "white" if self.marked else "dim"

        content = Text()
        content.append(mark, style=mark_style)
        content.append(f" {num}. ", style="cyan")
        content.append(self.text[:80], style=text_style)  # Truncate long lines

        if self.is_current:
            return Panel(content, border_style="yellow", padding=(0, 1))
        return content


class SegmentMarker(App):
    """Simple TUI for marking segments that need fixing"""

    CSS = """
    Screen {
        background: $background;
    }

    #main-container {
        height: 100%;
        padding: 1;
    }

    #segment-list {
        height: 1fr;
        overflow-y: auto;
    }

    #detail-panel {
        height: auto;
        border: solid $accent;
        padding: 1;
        margin-top: 1;
    }

    #stats {
        height: 3;
        background: $surface;
        padding: 1;
    }
    """

    BINDINGS = [
        Binding("j", "next", "Next", priority=True),
        Binding("k", "prev", "Previous", priority=True),
        Binding("down", "next", "Next", show=False),
        Binding("up", "prev", "Previous", show=False),
        Binding("space", "toggle", "Mark/Unmark", priority=True),
        Binding("!", "toggle", "Mark/Unmark", show=False),
        Binding("a", "mark_all", "Mark All"),
        Binding("c", "clear_all", "Clear All"),
        Binding("p", "play", "Play Audio"),
        Binding("s", "save", "Save & Quit"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, session_path: Path, **kwargs):
        super().__init__(**kwargs)
        self.session_path = session_path
        self.segments = []  # List of segment texts
        self.marked = set()  # Set of marked indices
        self.current_index = 0
        self.segment_displays = []
        self.load_session()

    def load_session(self):
        """Load transcript and any existing marks"""
        raw_path = self.session_path / "transcript_raw.txt"
        blocks_path = self.session_path / "blocks_to_fix.json"

        if not raw_path.exists():
            print(f"Error: {raw_path} not found")
            sys.exit(1)

        # Load transcript
        raw_lines = raw_path.read_text().splitlines()
        self.segments = []
        for line in raw_lines:
            if line.strip() and not line.startswith("#"):
                # Expected format: "1. some text here"
                parts = line.strip().split(". ", 1)
                if len(parts) == 2:
                    self.segments.append(parts[1])
                else:
                    self.segments.append(line.strip())

        # Load existing marks if any
        if blocks_path.exists():
            try:
                data = json.loads(blocks_path.read_text())
                self.marked = set(data.get("marked_for_fix", []))
            except:
                self.marked = set()

    def compose(self) -> ComposeResult:
        """Build the UI"""
        yield Header()

        with Vertical(id="main-container"):
            yield Static(f"Session: {self.session_path.name}", id="session-header")

            with Container(id="segment-list"):
                for i, text in enumerate(self.segments):
                    marked = i in self.marked
                    display = SegmentDisplay(i, text, marked, id=f"seg-{i}")
                    self.segment_displays.append(display)
                    yield display

            yield Static("", id="detail-panel")
            yield Static("", id="stats")

        yield Footer()

    def on_mount(self):
        """Initialize after mounting"""
        self.update_display()

    def update_display(self):
        """Update current segment highlight and details"""
        # Update all segment displays
        for i, display in enumerate(self.segment_displays):
            display.is_current = (i == self.current_index)
            display.marked = (i in self.marked)
            display.refresh()

        # Update detail panel with full text
        if self.segments:
            current_text = self.segments[self.current_index]
            detail = self.query_one("#detail-panel", Static)
            detail.update(f"[bold]Full text:[/bold]\n{current_text}")

        # Update stats
        stats = self.query_one("#stats", Static)
        total = len(self.segments)
        marked_count = len(self.marked)
        stats.update(
            f"Segment {self.current_index + 1}/{total}  |  "
            f"Marked for fixing: {marked_count}  |  "
            f"Clean: {total - marked_count}"
        )

        # Scroll to current
        if self.segment_displays:
            self.segment_displays[self.current_index].scroll_visible()

    def action_next(self):
        """Move to next segment"""
        if self.current_index < len(self.segments) - 1:
            self.current_index += 1
            self.update_display()

    def action_prev(self):
        """Move to previous segment"""
        if self.current_index > 0:
            self.current_index -= 1
            self.update_display()

    def action_toggle(self):
        """Toggle mark on current segment"""
        idx = self.current_index
        if idx in self.marked:
            self.marked.remove(idx)
        else:
            self.marked.add(idx)
        self.update_display()

    def action_mark_all(self):
        """Mark all segments"""
        self.marked = set(range(len(self.segments)))
        self.update_display()

    def action_clear_all(self):
        """Clear all marks"""
        self.marked = set()
        self.update_display()

    def action_play(self):
        """Play current segment audio"""
        wav_path = self.session_path / f"seg_{self.current_index:04d}.wav"
        if wav_path.exists():
            try:
                subprocess.run(["aplay", "-q", str(wav_path)], check=False)
            except FileNotFoundError:
                pass  # aplay not available, silently ignore

    def action_save(self):
        """Save marks and quit"""
        self.save_marks()
        self.exit(message=f"Saved {len(self.marked)} marked segments")

    def action_quit(self):
        """Quit without saving"""
        if self.marked:
            # Could add confirmation here
            self.exit(message="Quit without saving")
        else:
            self.exit()

    def save_marks(self):
        """Write blocks_to_fix.json"""
        output = {
            "session": self.session_path.name,
            "marked_for_fix": sorted(self.marked),
            "total_segments": len(self.segments),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        blocks_path = self.session_path / "blocks_to_fix.json"
        blocks_path.write_text(json.dumps(output, indent=2))


def main():
    if len(sys.argv) != 2:
        print("Usage: marker.py <session_directory>")
        print("Example: marker.py sessions/20251202_194521")
        sys.exit(1)

    session_path = Path(sys.argv[1]).resolve()

    if not session_path.exists():
        print(f"Error: Session directory not found: {session_path}")
        sys.exit(1)

    if not session_path.is_dir():
        print(f"Error: Not a directory: {session_path}")
        sys.exit(1)

    app = SegmentMarker(session_path)
    result = app.run()

    if result:
        print(result)


if __name__ == "__main__":
    main()
