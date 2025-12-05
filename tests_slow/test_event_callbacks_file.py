"""
tests_slow/test_event_callbacks_file.py
Integration tests for event callback system with file input

Tests that all events are properly emitted from TextProcessor and AsyncVADRecorder
when processing real audio files.
"""

import pytest
from pathlib import Path
from palaver.recorder.async_vad_recorder import (
    AsyncVADRecorder,
    TranscriptionComplete,
    NoteCommandDetected,
    NoteTitleCaptured,
    VADModeChanged,
    RecordingStateChanged,
    SpeechStarted,
    SpeechEnded,
    TranscriptionQueued,
)


class TestEventCallbacksFile:
    """Test event emission with file input (full integration)"""

    @pytest.fixture
    def cleanup_sessions(self):
        """Cleanup sessions directory after test"""
        yield
        # Note: We'll keep sessions for manual inspection during development
        # Uncomment to auto-cleanup:
        # import shutil
        # sessions_dir = Path("sessions")
        # if sessions_dir.exists():
        #     shutil.rmtree(sessions_dir)

    @pytest.fixture
    def audio_file(self):
        """Path to test audio file with note workflow"""
        audio_path = Path(__file__).parent / "audio_samples" / "note1.wav"
        if not audio_path.exists():
            pytest.skip(f"Test audio file not found: {audio_path}")
        return audio_path

    @pytest.mark.asyncio
    async def test_event_callbacks_with_file_input(self, audio_file, cleanup_sessions):
        """Test that all events are emitted correctly with file input"""
        received_events = []

        async def event_callback(event):
            """Capture all events"""
            received_events.append(event)

        # Create recorder with event callback
        recorder = AsyncVADRecorder(event_callback=event_callback)

        # Start recording with file input
        await recorder.start_recording(input_source=str(audio_file))

        # Wait for file to complete
        await recorder.wait_for_completion()

        # Stop recording
        session_dir = await recorder.stop_recording()

        # Verify session was created
        assert session_dir.exists()

        # Count event types
        event_types = {}
        for event in received_events:
            event_type = type(event).__name__
            event_types[event_type] = event_types.get(event_type, 0) + 1

        # Verify we got events
        assert len(received_events) > 0, "No events received"

        # Print summary for debugging
        print(f"\nTotal events received: {len(received_events)}")
        print("Event breakdown:")
        for event_type, count in sorted(event_types.items()):
            print(f"  {event_type}: {count}")

        # Verify critical event types were emitted
        assert "RecordingStateChanged" in event_types, "No RecordingStateChanged events"
        assert event_types["RecordingStateChanged"] >= 2, "Should have start and stop events"

        assert "SpeechStarted" in event_types, "No SpeechStarted events"
        assert "SpeechEnded" in event_types, "No SpeechEnded events"

        # Verify transcription events
        transcription_events = [e for e in received_events if isinstance(e, TranscriptionComplete)]
        assert len(transcription_events) > 0, "No TranscriptionComplete events"

        # Verify all transcription events have required fields
        for event in transcription_events:
            assert hasattr(event, "segment_index")
            assert hasattr(event, "text")
            assert hasattr(event, "success")
            assert hasattr(event, "processing_time_sec")

        print(f"\n✓ TranscriptionComplete events: {len(transcription_events)}")

    @pytest.mark.asyncio
    async def test_note_workflow_events(self, audio_file, cleanup_sessions):
        """Test that note workflow events are emitted correctly"""
        received_events = []

        async def event_callback(event):
            """Capture all events"""
            received_events.append(event)

        # Create recorder with event callback
        recorder = AsyncVADRecorder(event_callback=event_callback)

        # Start recording with file input
        await recorder.start_recording(input_source=str(audio_file))

        # Wait for file to complete
        await recorder.wait_for_completion()

        # Stop recording
        session_dir = await recorder.stop_recording()

        # Check for note workflow events
        note_command_events = [e for e in received_events if isinstance(e, NoteCommandDetected)]
        title_events = [e for e in received_events if isinstance(e, NoteTitleCaptured)]
        mode_change_events = [e for e in received_events if isinstance(e, VADModeChanged)]

        # Verify note command detected
        assert len(note_command_events) > 0, "No NoteCommandDetected events"
        print(f"\n✓ NoteCommandDetected events: {len(note_command_events)}")

        for event in note_command_events:
            assert hasattr(event, "segment_index")
            print(f"  - Detected in segment {event.segment_index}")

        # Verify title captured
        assert len(title_events) > 0, "No NoteTitleCaptured events"
        print(f"\n✓ NoteTitleCaptured events: {len(title_events)}")

        for event in title_events:
            assert hasattr(event, "segment_index")
            assert hasattr(event, "title")
            assert len(event.title) > 0, "Title should not be empty"
            print(f"  - Title: {event.title}")

        # Verify mode changes happened
        assert len(mode_change_events) > 0, "No VADModeChanged events"
        print(f"\n✓ VADModeChanged events: {len(mode_change_events)}")

        # Should have at least 2 mode changes: normal -> long_note -> normal
        assert len(mode_change_events) >= 2, "Should have at least 2 mode changes"

        mode_sequence = [e.mode for e in mode_change_events]
        print(f"  - Mode sequence: {' -> '.join(mode_sequence)}")

        # Verify long_note mode was activated
        assert "long_note" in mode_sequence, "long_note mode should be activated"

        # Verify mode returned to normal
        assert mode_change_events[-1].mode == "normal", "Should end in normal mode"

    @pytest.mark.asyncio
    async def test_event_ordering(self, audio_file, cleanup_sessions):
        """Test that events bracket the session correctly"""
        received_events = []

        async def event_callback(event):
            """Capture all events with timestamps"""
            received_events.append(event)

        # Create recorder with event callback
        recorder = AsyncVADRecorder(event_callback=event_callback)

        # Start recording with file input
        await recorder.start_recording(input_source=str(audio_file))

        # Wait for file to complete
        await recorder.wait_for_completion()

        # Stop recording
        await recorder.stop_recording()

        # Verify we got events
        assert len(received_events) > 0, "No events received"

        # Verify RecordingStateChanged(is_recording=True) is first
        first_event = received_events[0]
        assert isinstance(first_event, RecordingStateChanged)
        assert first_event.is_recording is True

        # Verify RecordingStateChanged(is_recording=False) is last
        last_event = received_events[-1]
        assert isinstance(last_event, RecordingStateChanged)
        assert last_event.is_recording is False

        # Verify all events have timestamps
        for event in received_events:
            assert hasattr(event, "timestamp")
            assert isinstance(event.timestamp, float)

        # Verify first and last event timestamps make sense
        assert last_event.timestamp > first_event.timestamp, \
            "Last event timestamp should be after first"

        print("\n✓ Events bracket session correctly")
        print(f"  - First: RecordingStateChanged(is_recording=True) at {first_event.timestamp:.3f}")
        print(f"  - Last: RecordingStateChanged(is_recording=False) at {last_event.timestamp:.3f}")
        print(f"  - Duration: {last_event.timestamp - first_event.timestamp:.3f}s")
        print(f"  - Total events: {len(received_events)}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
