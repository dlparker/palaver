#!/usr/bin/env python3
"""
palaver/tui/recorder_tui.py
Textual-based TUI for voice recorder

Features:
- Modal record/stop button
- Recording mode display (normal/long note/waiting for title)
- Real-time transcript monitor
- Processing queue status
"""

import sys
from pathlib import Path
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical, ScrollableContainer
from textual.widgets import Header, Footer, Button, Static, Label, DataTable
from textual.binding import Binding
from textual.reactive import reactive
from rich.text import Text
from rich.panel import Panel

# Import async recorder and events
from palaver.recorder.async_vad_recorder import (
    AsyncVADRecorder,
    AudioEvent,
    RecordingStateChanged,
    VADModeChanged,
    SpeechStarted,
    SpeechEnded,
    TranscriptionQueued,
    TranscriptionComplete,
    NoteCommandDetected,
    NoteTitleCaptured,
    QueueStatus,
)
from palaver.config.recorder_config import RecorderConfig
from palaver.mqtt.mqtt_adapter import MQTTAdapter
from palaver.mqtt.client import MQTTPublisher
from datetime import datetime


class RecordButton(Button):
    """Large modal record/stop button"""

    def __init__(self):
        super().__init__("⏺  START RECORDING", id="record-button", variant="success")
        self.is_recording = False

    def set_recording(self, recording: bool):
        """Update button state"""
        self.is_recording = recording
        if recording:
            self.label = "⏹  STOP RECORDING"
            self.variant = "error"
        else:
            self.label = "⏺  START RECORDING"
            self.variant = "success"


class ModeDisplay(Static):
    """Display current recording mode"""

    mode = reactive("normal")
    vad_active = reactive(False)

    def render(self):
        """Render mode display"""
        if self.mode == "normal":
            mode_text = "NORMAL (0.8s silence)"
            style = "bold white on blue"
        elif self.mode == "long_note":
            mode_text = "LONG NOTE (5s silence)"
            style = "bold white on green"
        else:
            mode_text = self.mode.upper()
            style = "bold white"

        activity = " 🎙️ SPEAKING" if self.vad_active else ""

        content = Text(f"{mode_text}{activity}", style=style)
        return Panel(content, title="Recording Mode", border_style="cyan")


class StatusDisplay(Static):
    """Display session and queue status"""

    session_path = reactive(None)
    queued_jobs = reactive(0)
    completed = reactive(0)
    total_segments = reactive(0)

    def render(self):
        """Render status"""
        lines = []

        if self.session_path:
            lines.append(f"Session: {self.session_path.name}")
        else:
            lines.append("Session: [not started]")

        lines.append(f"Segments: {self.total_segments}")
        lines.append(f"Transcribing: {self.queued_jobs}")
        lines.append(f"Completed: {self.completed}")

        content = "\n".join(lines)
        return Panel(content, title="Status", border_style="yellow")


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

    def update_line(self, segment_index: int, text: str, status: str = "✓"):
        """Update existing line"""
        # Find and update the line
        for i, line in enumerate(self.transcript_lines):
            if line.startswith(f"{status} {segment_index + 1}.") or \
               line.startswith(f"⏳ {segment_index + 1}."):
                self.transcript_lines[i] = f"{status} {segment_index + 1}. {text}"
                break
        else:
            # Not found, add new
            self.add_line(segment_index, text, status)
        self.update_display()

    def update_display(self):
        """Refresh display"""
        content = "\n".join(self.transcript_lines) if self.transcript_lines else "[No segments yet]"
        self.update(Panel(content, title="Current Note", border_style="green"))

    def clear(self):
        """Clear transcript"""
        self.transcript_lines = []
        self.update_display()


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

    def clear(self):
        """Clear all note titles"""
        self.note_titles = []
        self.update_display()


class NotificationDisplay(Static):
    """Display notifications and alerts"""

    def __init__(self):
        super().__init__()
        self.notifications = []

    def add_notification(self, message: str, style: str = "bold white"):
        """Add notification"""
        self.notifications.append(Text(message, style=style))
        # Keep last 5
        if len(self.notifications) > 5:
            self.notifications = self.notifications[-5:]
        self.update_display()

    def update_display(self):
        """Refresh display"""
        if not self.notifications:
            content = "[No notifications]"
        else:
            content = Text("\n").join(self.notifications)

        self.update(Panel(content, title="Notifications", border_style="magenta"))

    def clear(self):
        """Clear notifications"""
        self.notifications = []
        self.update_display()


class RecorderApp(App):
    """Voice Recorder TUI Application"""

    CSS = """
    Screen {
        background: $surface;
    }

    #main-container {
        height: 100%;
        padding: 1;
    }

    #control-section {
        height: auto;
        padding: 1;
    }

    #record-button {
        width: 100%;
        height: 5;
        margin-bottom: 1;
    }

    #info-row {
        height: auto;
        margin-bottom: 1;
    }

    #mode-display {
        width: 1fr;
        margin-right: 1;
    }

    #status-display {
        width: 1fr;
    }

    #transcript-section {
        height: 1fr;
        margin-bottom: 1;
    }

    #transcript-row {
        width: 100%;
        height: 100%;
    }

    #current-transcript {
        width: 2fr;
        height: 100%;
        margin-right: 1;
    }

    #note-titles {
        width: 1fr;
        height: 100%;
    }

    #notification-section {
        height: 8;
    }
    """

    BINDINGS = [
        Binding("space", "toggle_recording", "Start/Stop", key_display="SPACE"),
        Binding("q", "quit", "Quit"),
        Binding("c", "clear_notifications", "Clear Notifications", show=False),
    ]

    def __init__(self):
        super().__init__()

        # Load configuration
        config_path = Path("config.yaml")
        if config_path.exists():
            self.config = RecorderConfig.from_file(config_path)
        else:
            self.config = RecorderConfig.defaults()

        # Setup MQTT if enabled
        self.mqtt_adapter = None
        self.mqtt_client = None
        if self.config.mqtt_enabled:
            # Note: MQTT setup happens in async on_mount() to handle async connection
            pass

        # Create combined event handler (will add MQTT in on_mount if enabled)
        self.backend = AsyncVADRecorder(
            event_callback=self.handle_recorder_event,
            keep_segment_files=self.config.keep_segment_files
        )
        self.current_segment = -1
        self.current_note_title = None  # Track current note title
        self.in_note_mode = False  # Track if we're currently in a note

    def compose(self) -> ComposeResult:
        """Build UI"""
        yield Header()

        with Vertical(id="main-container"):
            with Container(id="control-section"):
                self.record_button = RecordButton()
                yield self.record_button

                with Horizontal(id="info-row"):
                    self.mode_display = ModeDisplay(id="mode-display")
                    yield self.mode_display

                    self.status_display = StatusDisplay(id="status-display")
                    yield self.status_display

            with Container(id="transcript-section"):
                with Horizontal(id="transcript-row"):
                    with ScrollableContainer(id="current-transcript"):
                        self.current_transcript = CurrentTranscriptMonitor()
                        yield self.current_transcript

                    with ScrollableContainer(id="note-titles"):
                        self.note_titles = NoteTitlesMonitor()
                        yield self.note_titles

            with Container(id="notification-section"):
                self.notification_display = NotificationDisplay()
                yield self.notification_display

        yield Footer()

    async def on_mount(self):
        """Initialize after mounting"""
        self.current_transcript.update_display()
        self.note_titles.update_display()
        self.notification_display.update_display()

        # Setup MQTT if enabled
        if self.config.mqtt_enabled:
            try:
                self.mqtt_client = MQTTPublisher(
                    broker=self.config.mqtt_broker,
                    port=self.config.mqtt_port,
                    qos=self.config.mqtt_qos
                )
                await self.mqtt_client.connect()

                # Use timestamp session_id (will be updated when recording starts)
                session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
                self.mqtt_adapter = MQTTAdapter(self.mqtt_client, session_id)

                self.notification_display.add_notification(
                    f"✓ MQTT: {self.config.mqtt_broker}:{self.config.mqtt_port}",
                    "bold green"
                )
            except Exception as e:
                self.notification_display.add_notification(
                    f"⚠ MQTT failed: {e}",
                    "bold yellow"
                )
                self.mqtt_adapter = None

        self.notification_display.add_notification(
            "Press SPACE or click button to start recording",
            "bold cyan"
        )

    async def handle_recorder_event(self, event: AudioEvent):
        """Handle events from backend (async callback)"""
        # Forward to MQTT if enabled
        if self.mqtt_adapter:
            await self.mqtt_adapter.handle_event(event)

        # Update UI
        self._handle_event_on_ui_thread(event)

    def _handle_event_on_ui_thread(self, event: AudioEvent):
        """Handle event on UI thread"""
        if isinstance(event, RecordingStateChanged):
            self.record_button.set_recording(event.is_recording)
            if event.is_recording:
                self.status_display.session_path = self.backend.session_dir
                self.current_transcript.clear()
                self.note_titles.clear()
                self.in_note_mode = False
                self.current_note_title = None

                # Update MQTT adapter session_id to match actual session directory
                if self.mqtt_adapter and self.backend.session_dir:
                    self.mqtt_adapter.session_id = self.backend.session_dir.name

                self.notification_display.add_notification(
                    "🎙️  Recording started",
                    "bold green"
                )
            else:
                # If we have a current note that wasn't completed, add it
                if self.in_note_mode and self.current_note_title:
                    self.note_titles.add_note(self.current_note_title)

                self.notification_display.add_notification(
                    f"✓ Recording stopped → {self.backend.session_dir}",
                    "bold yellow"
                )

        elif isinstance(event, VADModeChanged):
            self.mode_display.mode = event.mode
            if event.mode == "long_note":
                # Mode switched to long_note - but user already knows from NoteTitleCaptured
                # So this is redundant, skip notification
                pass
            else:
                # Switched back to normal - note is complete
                self.notification_display.add_notification(
                    f"✓ Note complete, normal mode restored ({event.min_silence_ms}ms)",
                    "bold blue"
                )

        elif isinstance(event, SpeechStarted):
            self.current_segment = event.segment_index
            self.mode_display.vad_active = True
            self.status_display.total_segments = event.segment_index + 1

        elif isinstance(event, SpeechEnded):
            self.mode_display.vad_active = False
            if event.kept:
                self.current_transcript.add_line(
                    event.segment_index,
                    f"[Processing... {event.duration_sec:.1f}s]",
                    "⏳"
                )
            else:
                self.notification_display.add_notification(
                    f"Segment discarded ({event.duration_sec:.1f}s < 1.2s)",
                    "dim"
                )

        elif isinstance(event, TranscriptionQueued):
            pass  # Already shown as "Processing..."

        elif isinstance(event, TranscriptionComplete):
            if event.success:
                self.current_transcript.update_line(
                    event.segment_index,
                    event.text[:100],  # Truncate for display
                    "✓"
                )
            else:
                self.current_transcript.update_line(
                    event.segment_index,
                    "[transcription failed]",
                    "✗"
                )

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

        elif isinstance(event, NoteTitleCaptured):
            # Save the title for this note
            self.current_note_title = event.title

            self.notification_display.add_notification(
                f"📌 TITLE: {event.title} - Long note mode active, continue speaking...",
                "bold cyan"
            )

        elif isinstance(event, QueueStatus):
            self.status_display.queued_jobs = event.queued_jobs
            self.status_display.completed = event.completed_transcriptions

    def on_button_pressed(self, event: Button.Pressed):
        """Handle button press"""
        if event.button.id == "record-button":
            # Run action in background task
            self.run_worker(self.action_toggle_recording())

    async def action_toggle_recording(self):
        """Toggle recording state (async)"""
        if self.backend.is_recording:
            await self.backend.stop_recording()
        else:
            await self.backend.start_recording()

    def action_clear_notifications(self):
        """Clear notification display"""
        self.notification_display.clear()

    async def action_quit(self):
        """Quit application (async)"""
        if self.backend.is_recording:
            await self.backend.stop_recording()

        # Disconnect MQTT if connected
        if self.mqtt_client:
            await self.mqtt_client.disconnect()

        self.exit()


def main():
    """Entry point"""
    app = RecorderApp()
    app.run()


if __name__ == "__main__":
    main()
