"""
tests/test_tui_with_mock.py
TUI tests using MockAsyncVADRecorder and Textual's test framework
"""

import pytest
from textual.widgets import Button

from palaver.tui.recorder_tui import RecorderApp
from tests.mocks.mock_recorder import MockAsyncVADRecorder


class TestTUIWithMock:
    """Test TUI with mock recorder"""

    @pytest.mark.asyncio
    async def test_app_starts_and_renders(self):
        """Test that app starts and renders all components"""
        app = RecorderApp()
        app.backend = MockAsyncVADRecorder(event_callback=app.handle_recorder_event)

        async with app.run_test() as pilot:
            # Verify main widgets exist
            assert app.query_one("#record-button")
            assert app.query_one("#mode-display")
            assert app.query_one("#status-display")

            # Verify initial state
            button = app.query_one("#record-button", Button)
            assert "START" in str(button.label)

    @pytest.mark.asyncio
    async def test_record_button_toggle(self):
        """Test record button toggles recording state"""
        app = RecorderApp()
        app.backend = MockAsyncVADRecorder(event_callback=app.handle_recorder_event)

        async with app.run_test() as pilot:
            button = app.query_one("#record-button", Button)

            # Initially not recording
            assert "START" in str(button.label)
            assert not app.backend.is_recording

            # Click to start recording
            await pilot.click("#record-button")
            await pilot.pause(0.1)

            # Should now be recording
            assert "STOP" in str(button.label)
            assert app.backend.is_recording

            # Click to stop
            await pilot.click("#record-button")
            await pilot.pause(0.1)

            # Should be stopped
            assert "START" in str(button.label)
            assert not app.backend.is_recording

    @pytest.mark.asyncio
    async def test_keyboard_space_toggles_recording(self):
        """Test SPACE key toggles recording"""
        app = RecorderApp()
        app.backend = MockAsyncVADRecorder(event_callback=app.handle_recorder_event)

        async with app.run_test() as pilot:
            button = app.query_one("#record-button", Button)

            # Press SPACE to start
            await pilot.press("space")
            await pilot.pause(0.1)

            assert "STOP" in str(button.label)
            assert app.backend.is_recording

            # Press SPACE to stop
            await pilot.press("space")
            await pilot.pause(0.1)

            assert "START" in str(button.label)
            assert not app.backend.is_recording

    @pytest.mark.asyncio
    async def test_mode_display_updates(self):
        """Test mode display updates with VAD mode changes"""
        app = RecorderApp()
        app.backend = MockAsyncVADRecorder(event_callback=app.handle_recorder_event)

        async with app.run_test() as pilot:
            # Start recording
            await pilot.press("space")
            await pilot.pause(0.1)

            # Initially normal mode
            assert app.mode_display.mode == "normal"

            # Simulate mode change
            await app.backend.simulate_mode_change("long_note")
            await pilot.pause(0.1)

            # Should update to long_note
            assert app.mode_display.mode == "long_note"

            # Change back to normal
            await app.backend.simulate_mode_change("normal")
            await pilot.pause(0.1)

            assert app.mode_display.mode == "normal"

    @pytest.mark.asyncio
    async def test_speech_segment_updates_transcript(self):
        """Test that speech segments update transcript monitor"""
        app = RecorderApp()
        app.backend = MockAsyncVADRecorder(event_callback=app.handle_recorder_event)

        async with app.run_test() as pilot:
            # Start recording
            await pilot.press("space")
            await pilot.pause(0.1)

            # Initially no transcript lines
            assert len(app.current_transcript.transcript_lines) == 0

            # Simulate speech segment
            await app.backend.simulate_speech_segment("Hello world", 2.0)
            await pilot.pause(0.2)

            # Should have transcript line
            assert len(app.current_transcript.transcript_lines) == 1
            assert "Hello world" in app.current_transcript.transcript_lines[0]

            # Simulate another segment
            await app.backend.simulate_speech_segment("Second segment", 1.5)
            await pilot.pause(0.2)

            assert len(app.current_transcript.transcript_lines) == 2
            assert "Second segment" in app.current_transcript.transcript_lines[1]

    @pytest.mark.asyncio
    async def test_vad_active_indicator(self):
        """Test VAD active indicator during speech"""
        app = RecorderApp()
        app.backend = MockAsyncVADRecorder(event_callback=app.handle_recorder_event)

        async with app.run_test() as pilot:
            # Start recording
            await pilot.press("space")
            await pilot.pause(0.1)

            # Initially not speaking
            assert not app.mode_display.vad_active

            # Simulate speech (manually trigger events)
            from palaver.recorder.async_vad_recorder import SpeechStarted, SpeechEnded

            # Speech started
            await app.handle_recorder_event(SpeechStarted(
                timestamp=0.0,
                segment_index=0,
                vad_mode="normal"
            ))
            await pilot.pause(0.1)

            assert app.mode_display.vad_active

            # Speech ended
            await app.handle_recorder_event(SpeechEnded(
                timestamp=0.0,
                segment_index=0,
                audio_data=None,
                duration_sec=2.0,
                kept=True
            ))
            await pilot.pause(0.1)

            assert not app.mode_display.vad_active

    @pytest.mark.asyncio
    async def test_notification_display(self):
        """Test notification display shows events"""
        app = RecorderApp()
        app.backend = MockAsyncVADRecorder(event_callback=app.handle_recorder_event)

        async with app.run_test() as pilot:
            # Should have initial notification
            assert len(app.notification_display.notifications) > 0

            # Start recording
            await pilot.press("space")
            await pilot.pause(0.1)

            # Should show recording started notification
            notifications_text = str(app.notification_display.notifications)
            assert "Recording started" in notifications_text

    @pytest.mark.asyncio
    async def test_status_display_updates(self):
        """Test status display updates with segments"""
        app = RecorderApp()
        app.backend = MockAsyncVADRecorder(event_callback=app.handle_recorder_event)

        async with app.run_test() as pilot:
            # Start recording
            await pilot.press("space")
            await pilot.pause(0.1)

            # Should show session path
            assert app.status_display.session_path is not None

            # Initially 0 segments
            assert app.status_display.total_segments == 0

            # Simulate speech segment
            await app.backend.simulate_speech_segment("Test", 2.0)
            await pilot.pause(0.2)

            # Should update segment count
            assert app.status_display.total_segments == 1

    @pytest.mark.asyncio
    async def test_complete_note_workflow(self):
        """Test complete note-taking workflow"""
        app = RecorderApp()
        app.backend = MockAsyncVADRecorder(event_callback=app.handle_recorder_event)

        async with app.run_test() as pilot:
            # Start recording
            await pilot.press("space")
            await pilot.pause(0.1)

            # Simulate note workflow
            await app.backend.simulate_note_workflow(
                title="My Important Note",
                body_segments=["First paragraph", "Second paragraph"]
            )
            await pilot.pause(0.3)

            # Verify transcript has body segments (command and title should be cleared)
            transcript_lines = app.current_transcript.transcript_lines
            assert len(transcript_lines) >= 2

            # Check for body paragraphs in current transcript
            assert any("First paragraph" in line for line in transcript_lines)
            assert any("Second paragraph" in line for line in transcript_lines)

            # Verify mode changes happened
            # Should be back to normal after note
            assert app.mode_display.mode == "normal"

    @pytest.mark.asyncio
    async def test_multiple_segments(self):
        """Test handling multiple speech segments"""
        app = RecorderApp()
        app.backend = MockAsyncVADRecorder(event_callback=app.handle_recorder_event)

        async with app.run_test() as pilot:
            # Start recording
            await pilot.press("space")
            await pilot.pause(0.1)

            # Simulate 5 segments
            for i in range(5):
                await app.backend.simulate_speech_segment(f"Segment {i+1}", 1.5)
                await pilot.pause(0.1)

            # Should have 5 transcript lines
            assert len(app.current_transcript.transcript_lines) == 5

            # Verify segment count
            assert app.status_display.total_segments == 5

    @pytest.mark.asyncio
    async def test_transcript_scrolling(self):
        """Test that transcript keeps last 20 lines"""
        app = RecorderApp()
        app.backend = MockAsyncVADRecorder(event_callback=app.handle_recorder_event)

        async with app.run_test() as pilot:
            # Start recording
            await pilot.press("space")
            await pilot.pause(0.1)

            # Simulate 25 segments (more than 20 line limit)
            for i in range(25):
                await app.backend.simulate_speech_segment(f"Segment {i+1}", 0.5)
                await pilot.pause(0.05)

            # Should have all 25 lines (limit is 50)
            assert len(app.current_transcript.transcript_lines) == 25

            # Should have most recent segment last
            assert "Segment 25" in app.current_transcript.transcript_lines[-1]
            assert "Segment 1" in app.current_transcript.transcript_lines[0]

    @pytest.mark.asyncio
    async def test_clear_notifications(self):
        """Test clearing notifications with 'c' key"""
        app = RecorderApp()
        app.backend = MockAsyncVADRecorder(event_callback=app.handle_recorder_event)

        async with app.run_test() as pilot:
            # Start recording to generate notifications
            await pilot.press("space")
            await pilot.pause(0.1)

            # Should have notifications
            assert len(app.notification_display.notifications) > 0

            # Press 'c' to clear
            await pilot.press("c")
            await pilot.pause(0.1)

            # Notifications should be cleared
            assert len(app.notification_display.notifications) == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
