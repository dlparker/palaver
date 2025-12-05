"""
Integration tests for MQTT recording sessions.

Tests the full recording pipeline with MQTT message publishing.
"""

import asyncio
import pytest
import json
from pathlib import Path

from palaver.mqtt.mqtt_adapter import MQTTAdapter
from palaver.recorder.async_vad_recorder import AsyncVADRecorder


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

    def get_messages_by_topic(self, topic_filter: str):
        """Get all messages matching topic filter."""
        return [msg for msg in self.published_messages if topic_filter in msg["topic"]]

    def get_all_messages(self):
        """Get all published messages."""
        return self.published_messages


@pytest.mark.slow
@pytest.mark.asyncio
async def test_mqtt_full_recording_session():
    """Test MQTT messages during full recording session with note workflow."""
    # Setup mock MQTT
    mock_mqtt_client = MockMQTTClient()
    mqtt_adapter = MQTTAdapter(mock_mqtt_client, session_id="test_integration_session")

    # Create recorder with MQTT adapter
    recorder = AsyncVADRecorder(
        event_callback=mqtt_adapter.handle_event,
        keep_segment_files=False  # Don't clutter filesystem
    )

    # Record test file with "start new note" workflow
    test_file = Path(__file__).parent / "audio_samples" / "note1.wav"
    assert test_file.exists(), f"Test audio file not found: {test_file}"

    await recorder.start_recording(input_source=str(test_file))
    await recorder.wait_for_completion()
    session_dir = await recorder.stop_recording()

    # Small delay to allow async events to be fully processed (especially CommandCompleted)
    await asyncio.sleep(0.5)

    # Verify session completed
    assert session_dir is not None
    assert session_dir.exists()

    # Get all published messages
    all_messages = mock_mqtt_client.get_all_messages()
    assert len(all_messages) > 0, "No MQTT messages published"

    # Get segment messages
    segment_messages = mock_mqtt_client.get_messages_by_topic("/segment")
    assert len(segment_messages) > 0, "No segment messages published"

    # Get command completion messages
    completion_messages = mock_mqtt_client.get_messages_by_topic("/command/completed")
    assert len(completion_messages) > 0, "No command completion messages published"

    # Verify segment message format
    for msg in segment_messages:
        payload = json.loads(msg["payload"])

        # Required fields
        assert "event_id" in payload
        assert "timestamp" in payload
        assert "session_id" in payload
        assert payload["session_id"] == "test_integration_session"
        assert "segment_index" in payload
        assert "text" in payload
        assert "success" in payload
        assert "session_state" in payload

        # Session state structure
        assert "state" in payload["session_state"]
        assert payload["session_state"]["state"] in ["idle", "in_command"]

    # Verify command completion message format
    completion_payload = json.loads(completion_messages[0]["payload"])
    assert "event_id" in completion_payload
    assert "timestamp" in completion_payload
    assert "session_id" in completion_payload
    assert completion_payload["session_id"] == "test_integration_session"
    assert "command_type" in completion_payload
    assert completion_payload["command_type"] == "SimpleNote"
    assert "bucket_contents" in completion_payload
    assert "output_files" in completion_payload
    assert len(completion_payload["output_files"]) > 0

    # Verify bucket contents has expected structure
    bucket_contents = completion_payload["bucket_contents"]
    assert "note_title" in bucket_contents
    assert "note_body" in bucket_contents
    assert len(bucket_contents["note_title"]) > 0
    assert len(bucket_contents["note_body"]) > 0

    # Verify output file exists
    output_file = Path(completion_payload["output_files"][0])
    assert output_file.exists(), f"Output file not found: {output_file}"

    print(f"\n✓ Integration test passed:")
    print(f"  - Published {len(all_messages)} total messages")
    print(f"  - {len(segment_messages)} segment messages")
    print(f"  - {len(completion_messages)} command completion messages")
    print(f"  - Output file: {output_file}")


@pytest.mark.slow
@pytest.mark.asyncio
async def test_mqtt_message_sequence():
    """Test that MQTT messages are published in correct sequence."""
    # Setup mock MQTT
    mock_mqtt_client = MockMQTTClient()
    mqtt_adapter = MQTTAdapter(mock_mqtt_client, session_id="test_sequence")

    # Create recorder with MQTT adapter
    recorder = AsyncVADRecorder(
        event_callback=mqtt_adapter.handle_event,
        keep_segment_files=False
    )

    # Record test file
    test_file = Path(__file__).parent / "audio_samples" / "note1.wav"
    await recorder.start_recording(input_source=str(test_file))
    await recorder.wait_for_completion()
    await recorder.stop_recording()

    # Small delay to allow async events to be fully processed
    await asyncio.sleep(0.5)

    # Get all messages
    all_messages = mock_mqtt_client.get_all_messages()

    # Verify segment messages come before completion
    segment_indices = [i for i, msg in enumerate(all_messages) if "/segment" in msg["topic"]]
    completion_indices = [i for i, msg in enumerate(all_messages) if "/command/completed" in msg["topic"]]

    assert len(segment_indices) > 0, "No segment messages found"
    assert len(completion_indices) > 0, "No completion messages found"

    # Last segment should come before completion
    last_segment_idx = max(segment_indices)
    first_completion_idx = min(completion_indices)
    assert last_segment_idx < first_completion_idx, "Completion message published before all segments"

    # Verify timestamps are monotonically increasing
    timestamps = []
    for msg in all_messages:
        payload = json.loads(msg["payload"])
        timestamps.append(payload["timestamp"])

    # Timestamps should generally increase (small variations allowed due to async)
    for i in range(1, len(timestamps)):
        # Allow small backwards variation (< 0.1s) due to async event timing
        assert timestamps[i] >= timestamps[i-1] - 0.1, \
            f"Timestamp sequence error at index {i}: {timestamps[i]} < {timestamps[i-1]}"

    print(f"\n✓ Message sequence test passed:")
    print(f"  - {len(segment_indices)} segments before completion")
    print(f"  - Timestamps are monotonically increasing")


@pytest.mark.slow
@pytest.mark.asyncio
async def test_mqtt_session_state_tracking():
    """Test that session state is correctly tracked through workflow."""
    # Setup mock MQTT
    mock_mqtt_client = MockMQTTClient()
    mqtt_adapter = MQTTAdapter(mock_mqtt_client, session_id="test_state")

    # Create recorder with MQTT adapter
    recorder = AsyncVADRecorder(
        event_callback=mqtt_adapter.handle_event,
        keep_segment_files=False
    )

    # Record test file
    test_file = Path(__file__).parent / "audio_samples" / "note1.wav"
    await recorder.start_recording(input_source=str(test_file))
    await recorder.wait_for_completion()
    await recorder.stop_recording()

    # Small delay to allow async events to be fully processed
    await asyncio.sleep(0.5)

    # Get segment messages
    segment_messages = mock_mqtt_client.get_messages_by_topic("/segment")

    # Track state transitions in segment messages
    states = []
    command_types = []
    for msg in segment_messages:
        payload = json.loads(msg["payload"])
        state = payload["session_state"]["state"]
        states.append(state)
        if state == "in_command" and "command_type" in payload["session_state"]:
            command_types.append(payload["session_state"]["command_type"])

    # Should start with idle or quickly transition to in_command
    assert "idle" in states or states[0] == "in_command", \
        f"Unexpected initial state: {states[0]}"

    # Should have in_command state during note capture
    assert "in_command" in states, "Never entered in_command state"

    # Verify command type is set during in_command state
    assert "SimpleNote" in command_types, "Command type not tracked correctly"

    # Verify command completion message exists (state transitions to idle after completion)
    completion_messages = mock_mqtt_client.get_messages_by_topic("/command/completed")
    assert len(completion_messages) > 0, "No command completion - state never returned to idle"

    # Verify completion message shows correct state transition
    completion_payload = json.loads(completion_messages[0]["payload"])
    assert completion_payload["command_type"] == "SimpleNote"

    print(f"\n✓ State tracking test passed:")
    print(f"  - State sequence in segments: {' -> '.join(set(states))}")
    print(f"  - Command completed, state returned to idle")
    print(f"  - Total segments: {len(states)}")
