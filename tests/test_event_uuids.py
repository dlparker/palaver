"""
Unit tests for event UUIDs.

Tests that all AudioEvent instances get unique event_id values.
"""

import pytest
import numpy as np
from dataclasses import asdict

from palaver.recorder.async_vad_recorder import (
    AudioEvent,
    RecordingStateChanged,
    VADModeChanged,
    SpeechStarted,
    SpeechEnded,
    TranscriptionQueued,
    TranscriptionComplete,
    CommandDetected,
    BucketStarted,
    BucketFilled,
    CommandCompleted,
    NoteCommandDetected,
    NoteTitleCaptured,
)


def test_audio_event_has_event_id():
    """Test that base AudioEvent has event_id field."""
    # Note: Can't instantiate abstract AudioEvent directly, use concrete subclass
    event = RecordingStateChanged(
        timestamp=1000.0,
        is_recording=True
    )

    assert hasattr(event, 'event_id')
    assert event.event_id is not None
    assert isinstance(event.event_id, str)
    assert len(event.event_id) == 36  # UUID format: 8-4-4-4-12


def test_event_ids_are_unique():
    """Test that different events get different UUIDs."""
    events = [
        RecordingStateChanged(timestamp=1000.0, is_recording=True),
        RecordingStateChanged(timestamp=1001.0, is_recording=False),
        VADModeChanged(timestamp=1002.0, mode="normal", min_silence_ms=800),
        SpeechStarted(timestamp=1003.0, segment_index=0, vad_mode="normal"),
    ]

    event_ids = [event.event_id for event in events]

    # All should be unique
    assert len(event_ids) == len(set(event_ids))


def test_event_id_survives_asdict():
    """Test that event_id is preserved when converting to dict."""
    event = TranscriptionComplete(
        timestamp=1000.0,
        segment_index=0,
        text="hello world",
        success=True,
        processing_time_sec=1.5,
        error_msg=None
    )

    original_id = event.event_id
    event_dict = asdict(event)

    assert 'event_id' in event_dict
    assert event_dict['event_id'] == original_id


def test_all_event_types_have_uuid():
    """Test that all concrete event types get UUIDs."""
    from pathlib import Path

    events = [
        RecordingStateChanged(timestamp=1000.0, is_recording=True),
        VADModeChanged(timestamp=1000.0, mode="normal", min_silence_ms=800),
        SpeechStarted(timestamp=1000.0, segment_index=0, vad_mode="normal"),
        SpeechEnded(timestamp=1000.0, segment_index=0, audio_data=np.array([]), duration_sec=2.5, kept=True),
        TranscriptionQueued(timestamp=1000.0, segment_index=0, wav_path=Path("/tmp/test.wav"), duration_sec=2.5),
        TranscriptionComplete(
            timestamp=1000.0,
            segment_index=0,
            text="test",
            success=True,
            processing_time_sec=1.0,
            error_msg=None
        ),
        CommandDetected(
            timestamp=1000.0,
            command_doc_type="SimpleNote",
            command_phrase="start new note",
            matched_text="start note",
            similarity_score=0.9
        ),
        BucketStarted(
            timestamp=1000.0,
            command_doc_type="SimpleNote",
            bucket_name="title",
            bucket_display_name="Title",
            bucket_index=0,
            start_window_sec=2.0
        ),
        BucketFilled(
            timestamp=1000.0,
            command_doc_type="SimpleNote",
            bucket_name="title",
            bucket_display_name="Title",
            text="My Title",
            duration_sec=2.0,
            chunk_count=1
        ),
        CommandCompleted(
            timestamp=1000.0,
            command_doc_type="SimpleNote",
            output_files=[Path("/tmp/test.md")],
            bucket_contents={"title": "Test"}
        ),
        NoteCommandDetected(timestamp=1000.0, segment_index=0),
        NoteTitleCaptured(timestamp=1000.0, segment_index=1, title="Title"),
    ]

    for event in events:
        assert hasattr(event, 'event_id'), f"{type(event).__name__} missing event_id"
        assert event.event_id is not None, f"{type(event).__name__} has None event_id"
        assert len(event.event_id) == 36, f"{type(event).__name__} has invalid UUID format"


def test_event_id_is_keyword_only():
    """Test that event_id doesn't interfere with positional args."""
    # This should work - timestamp is positional, event_id is keyword-only
    event = RecordingStateChanged(1000.0, True)
    assert event.timestamp == 1000.0
    assert event.is_recording is True
    assert event.event_id is not None


def test_custom_event_id():
    """Test that we can provide custom event_id if needed."""
    custom_id = "custom-test-id-12345"
    event = RecordingStateChanged(
        timestamp=1000.0,
        is_recording=True,
        event_id=custom_id
    )

    assert event.event_id == custom_id
