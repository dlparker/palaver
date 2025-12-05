"""
tests/test_tui_basic.py
Basic tests for TUI initialization and imports
"""

import pytest


class TestTUIBasic:
    """Basic TUI tests"""

    def test_tui_imports(self):
        """Test that TUI can be imported"""
        from palaver.tui.recorder_tui import RecorderApp

        # Should import without error
        assert RecorderApp is not None

    def test_tui_app_creation(self):
        """Test that TUI app can be instantiated"""
        from palaver.tui.recorder_tui import RecorderApp

        # Create app
        app = RecorderApp()

        # Verify backend is set up
        assert app.backend is not None
        assert hasattr(app.backend, 'event_callback')
        assert app.backend.event_callback == app.handle_recorder_event

    def test_tui_components_exist(self):
        """Test that TUI has expected components"""
        from palaver.tui.recorder_tui import (
            RecordButton,
            ModeDisplay,
            StatusDisplay,
            CurrentTranscriptMonitor,
            NoteTitlesMonitor,
            NotificationDisplay,
        )

        # All components should import
        assert RecordButton is not None
        assert ModeDisplay is not None
        assert StatusDisplay is not None
        assert CurrentTranscriptMonitor is not None
        assert NoteTitlesMonitor is not None
        assert NotificationDisplay is not None

    def test_event_types_imported(self):
        """Test that TUI imports correct event types"""
        from palaver.tui import recorder_tui

        # Verify event types are available in module
        assert hasattr(recorder_tui, 'RecordingStateChanged')
        assert hasattr(recorder_tui, 'VADModeChanged')
        assert hasattr(recorder_tui, 'SpeechStarted')
        assert hasattr(recorder_tui, 'SpeechEnded')
        assert hasattr(recorder_tui, 'TranscriptionQueued')


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
