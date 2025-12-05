"""
Unit tests for MQTT adapter.

Tests the MQTTAdapter's ability to handle events and publish
correctly formatted messages.
"""

import pytest
import json
import numpy as np
from pathlib import Path
from unittest.mock import AsyncMock

from palaver.mqtt.mqtt_adapter import MQTTAdapter
from palaver.recorder.async_vad_recorder import (
    TranscriptionComplete,
    SpeechEnded,
    CommandDetected,
    BucketStarted,
    BucketFilled,
    CommandCompleted,
    VADModeChanged,
)


class MockMQTTClient:
    """Mock MQTT client that captures published messages."""

    def __init__(self):
        self.published_messages = []

    async def publish(self, topic: str, payload: str, qos: int = 1, retain: bool = False):
        """Capture published message."""
        self.published_messages.append({
            "topic": topic,
            "payload": payload,
            "qos": qos,
            "retain": retain
        })

    def get_last_message(self):
        """Get last published message."""
        return self.published_messages[-1] if self.published_messages else None

    def get_messages_by_topic(self, topic_filter: str):
        """Get all messages matching topic filter."""
        return [msg for msg in self.published_messages if topic_filter in msg["topic"]]

    def clear(self):
        """Clear all captured messages."""
        self.published_messages = []


@pytest.fixture
def mock_mqtt_client():
    """Provide mock MQTT client."""
    return MockMQTTClient()


@pytest.fixture
def mqtt_adapter(mock_mqtt_client):
    """Provide MQTT adapter with mock client."""
    return MQTTAdapter(mock_mqtt_client, session_id="test_session_123")


@pytest.mark.asyncio
async def test_segment_message_format(mqtt_adapter, mock_mqtt_client):
    """Test that segment messages have correct format."""
    # Create SpeechEnded event first (for duration enrichment)
    speech_ended = SpeechEnded(
        timestamp=1000.0,
        segment_index=0,
        audio_data=np.array([]),  # Empty array for test
        duration_sec=2.5,
        kept=True
    )
    await mqtt_adapter.handle_event(speech_ended)

    # Create TranscriptionComplete event
    event = TranscriptionComplete(
        timestamp=1001.5,
        segment_index=0,
        text="hello world",
        success=True,
        processing_time_sec=1.5,
        error_msg=None
    )

    # Handle event
    await mqtt_adapter.handle_event(event)

    # Verify message was published
    last_msg = mock_mqtt_client.get_last_message()
    assert last_msg is not None
    assert last_msg["topic"] == "palaver/session/test_session_123/segment"
    assert last_msg["qos"] == 1
    assert last_msg["retain"] is False

    # Parse and verify payload
    payload = json.loads(last_msg["payload"])
    assert "event_id" in payload
    assert payload["timestamp"] == 1001.5
    assert payload["session_id"] == "test_session_123"
    assert payload["segment_index"] == 0
    assert payload["text"] == "hello world"
    assert payload["duration_sec"] == 2.5  # From SpeechEnded
    assert payload["processing_time_sec"] == 1.5
    assert payload["success"] is True
    assert "session_state" in payload
    assert payload["session_state"]["state"] == "idle"


@pytest.mark.asyncio
async def test_command_completion_message_format(mqtt_adapter, mock_mqtt_client):
    """Test that command completion messages have correct format."""
    event = CommandCompleted(
        timestamp=2000.0,
        command_doc_type="SimpleNote",
        output_files=[Path("/tmp/note_0001_test.md")],
        bucket_contents={
            "note_title": "Test Title",
            "note_body": "Test body content"
        }
    )

    # Handle event
    await mqtt_adapter.handle_event(event)

    # Verify message was published
    last_msg = mock_mqtt_client.get_last_message()
    assert last_msg is not None
    assert last_msg["topic"] == "palaver/session/test_session_123/command/completed"

    # Parse and verify payload
    payload = json.loads(last_msg["payload"])
    assert "event_id" in payload
    assert payload["timestamp"] == 2000.0
    assert payload["session_id"] == "test_session_123"
    assert payload["command_type"] == "SimpleNote"
    assert payload["bucket_contents"]["note_title"] == "Test Title"
    assert payload["bucket_contents"]["note_body"] == "Test body content"
    assert len(payload["output_files"]) == 1
    assert "note_0001_test.md" in payload["output_files"][0]


@pytest.mark.asyncio
async def test_state_transitions(mqtt_adapter, mock_mqtt_client):
    """Test that adapter tracks state transitions correctly."""
    # Initial state: idle
    assert mqtt_adapter.current_state == "idle"

    # Detect command
    cmd_event = CommandDetected(
        timestamp=1000.0,
        command_doc_type="SimpleNote",
        command_phrase="start new note",
        matched_text="start a new note",
        similarity_score=0.95
    )
    await mqtt_adapter.handle_event(cmd_event)

    # State should be in_command
    assert mqtt_adapter.current_state == "in_command"
    assert mqtt_adapter.command_doc_type == "SimpleNote"

    # Bucket starts
    bucket_start = BucketStarted(
        timestamp=1001.0,
        command_doc_type="SimpleNote",
        bucket_name="note_title",
        bucket_display_name="Note Title",
        bucket_index=0,
        start_window_sec=2.0
    )
    await mqtt_adapter.handle_event(bucket_start)
    assert mqtt_adapter.current_bucket == "note_title"

    # Bucket fills
    bucket_filled = BucketFilled(
        timestamp=1003.0,
        command_doc_type="SimpleNote",
        bucket_name="note_title",
        bucket_display_name="Note Title",
        text="My Title",
        duration_sec=2.0,
        chunk_count=1
    )
    await mqtt_adapter.handle_event(bucket_filled)

    # Command completes
    cmd_complete = CommandCompleted(
        timestamp=1010.0,
        command_doc_type="SimpleNote",
        output_files=[Path("/tmp/note.md")],
        bucket_contents={"note_title": "My Title", "note_body": "Body"}
    )
    await mqtt_adapter.handle_event(cmd_complete)

    # State should be back to idle
    assert mqtt_adapter.current_state == "idle"
    assert mqtt_adapter.command_doc_type is None
    assert mqtt_adapter.current_bucket is None


@pytest.mark.asyncio
async def test_session_state_enrichment(mqtt_adapter, mock_mqtt_client):
    """Test that segment messages include enriched session state."""
    # Setup: in command with active bucket
    cmd_event = CommandDetected(
        timestamp=1000.0,
        command_doc_type="SimpleNote",
        command_phrase="start new note",
        matched_text="start new note",
        similarity_score=0.95
    )
    await mqtt_adapter.handle_event(cmd_event)

    bucket_start = BucketStarted(
        timestamp=1001.0,
        command_doc_type="SimpleNote",
        bucket_name="note_body",
        bucket_display_name="Note Body",
        bucket_index=1,
        start_window_sec=4.0
    )
    await mqtt_adapter.handle_event(bucket_start)

    # Clear previous messages
    mock_mqtt_client.clear()

    # Now publish a segment
    transcription = TranscriptionComplete(
        timestamp=1005.0,
        segment_index=5,
        text="some note content",
        success=True,
        processing_time_sec=0.8,
        error_msg=None
    )
    await mqtt_adapter.handle_event(transcription)

    # Verify session state in message
    last_msg = mock_mqtt_client.get_last_message()
    payload = json.loads(last_msg["payload"])

    assert payload["session_state"]["state"] == "in_command"
    assert payload["session_state"]["command_type"] == "SimpleNote"
    assert payload["session_state"]["current_bucket"] == "note_body"


@pytest.mark.asyncio
async def test_segment_duration_lookup(mqtt_adapter, mock_mqtt_client):
    """Test that segment durations are correctly looked up from SpeechEnded events."""
    # Store multiple segment durations
    for i in range(3):
        speech_ended = SpeechEnded(
            timestamp=float(1000 + i),
            segment_index=i,
            audio_data=np.array([]),  # Empty array for test
            duration_sec=float(2 + i * 0.5),
            kept=True
        )
        await mqtt_adapter.handle_event(speech_ended)

    # Clear messages
    mock_mqtt_client.clear()

    # Publish transcription for segment 1
    transcription = TranscriptionComplete(
        timestamp=1005.0,
        segment_index=1,
        text="segment one",
        success=True,
        processing_time_sec=0.5,
        error_msg=None
    )
    await mqtt_adapter.handle_event(transcription)

    # Verify correct duration
    last_msg = mock_mqtt_client.get_last_message()
    payload = json.loads(last_msg["payload"])
    assert payload["duration_sec"] == 2.5  # 2 + 1*0.5


@pytest.mark.asyncio
async def test_event_id_included(mqtt_adapter, mock_mqtt_client):
    """Test that all messages include event_id from the event."""
    event = TranscriptionComplete(
        timestamp=1000.0,
        segment_index=0,
        text="test",
        success=True,
        processing_time_sec=1.0,
        error_msg=None
    )

    # Verify event has UUID
    assert event.event_id is not None
    assert len(event.event_id) == 36  # UUID format

    await mqtt_adapter.handle_event(event)

    last_msg = mock_mqtt_client.get_last_message()
    payload = json.loads(last_msg["payload"])

    # Verify event_id is included and matches
    assert payload["event_id"] == event.event_id


@pytest.mark.asyncio
async def test_no_crash_on_unhandled_events(mqtt_adapter, mock_mqtt_client):
    """Test that adapter doesn't crash on unhandled event types."""
    # VADModeChanged is tracked but not published
    vad_event = VADModeChanged(
        timestamp=1000.0,
        mode="long_note",
        min_silence_ms=5000
    )

    # Should not raise exception
    await mqtt_adapter.handle_event(vad_event)

    # Should not publish anything
    assert len(mock_mqtt_client.published_messages) == 0
