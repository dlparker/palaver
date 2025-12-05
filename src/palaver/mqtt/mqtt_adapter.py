"""
palaver/mqtt/mqtt_adapter.py
MQTT adapter for publishing recorder events

Subscribes to recorder events via event_callback and publishes to MQTT.
Tracks session state and enriches events with metadata.
"""

import json
import time
from dataclasses import asdict
from typing import Optional, Dict

from palaver.recorder.async_vad_recorder import (
    AudioEvent,
    TranscriptionComplete,
    SpeechEnded,
    CommandDetected,
    BucketStarted,
    BucketFilled,
    CommandCompleted,
    CommandAborted,
    RecordingStateChanged,
    VADModeChanged,
)


class MQTTAdapter:
    """
    Adapter that subscribes to recorder events and publishes to MQTT.

    Tracks session state and enriches events with metadata before publishing.
    Publishes two main message types:
    1. Segment messages - for each transcribed segment
    2. Command completion messages - when a CommandDoc completes
    """

    def __init__(self, mqtt_client, session_id: str):
        """
        Initialize MQTT adapter.

        Args:
            mqtt_client: MQTTPublisher instance
            session_id: Session identifier (timestamp format: YYYYMMDD_HHMMSS)
        """
        self.mqtt_client = mqtt_client
        self.session_id = session_id

        # Track session state for enrichment
        self.current_state = "idle"  # idle, awaiting_command, in_command
        self.current_bucket: Optional[str] = None
        self.command_doc_type: Optional[str] = None

        # Store segment durations from SpeechEnded events for later enrichment
        self.segment_durations: Dict[int, float] = {}

    async def handle_event(self, event: AudioEvent):
        """
        Main event handler - called by AsyncVADRecorder via event_callback.

        Routes events to appropriate handlers and publishes to MQTT.

        Args:
            event: AudioEvent instance (any subclass)
        """
        try:
            # Store segment durations for enrichment
            if isinstance(event, SpeechEnded) and event.kept:
                self.segment_durations[event.segment_index] = event.duration_sec

            # Handle transcription completion (publish segment message)
            if isinstance(event, TranscriptionComplete):
                await self._publish_segment(event)

            # Track state transitions
            elif isinstance(event, CommandDetected):
                self._update_state("in_command", event.command_doc_type)

            elif isinstance(event, BucketStarted):
                self.current_bucket = event.bucket_name

            elif isinstance(event, BucketFilled):
                # Bucket completed, but still in command
                pass

            elif isinstance(event, CommandCompleted):
                await self._publish_command_completion(event)
                self._update_state("idle")

            elif isinstance(event, CommandAborted):
                self._update_state("idle")

        except Exception as e:
            # Don't let MQTT errors crash the recorder
            print(f"[MQTT] Error handling event: {e}")
            import traceback
            traceback.print_exc()

    async def _publish_segment(self, event: TranscriptionComplete):
        """
        Publish segment message for transcription completion.

        Topic: palaver/session/{session_id}/segment

        Args:
            event: TranscriptionComplete event
        """
        # Get segment duration from stored SpeechEnded event
        duration_sec = self.segment_durations.get(event.segment_index, 0.0)

        # Build message payload
        message = {
            "event_id": event.event_id,
            "timestamp": event.timestamp,
            "session_id": self.session_id,
            "segment_index": event.segment_index,
            "text": event.text,
            "duration_sec": duration_sec,
            "processing_time_sec": event.processing_time_sec,
            "session_state": {
                "state": self.current_state,
                "command_type": self.command_doc_type,
                "current_bucket": self.current_bucket
            },
            "success": event.success
        }

        # Publish to MQTT
        topic = f"palaver/session/{self.session_id}/segment"
        payload = json.dumps(message)
        await self.mqtt_client.publish(topic, payload)

        print(f"[MQTT] Published segment {event.segment_index}: {event.text[:50]}...")

    async def _publish_command_completion(self, event: CommandCompleted):
        """
        Publish command completion message.

        Topic: palaver/session/{session_id}/command/completed

        Args:
            event: CommandCompleted event
        """
        # Convert Path objects to strings for JSON serialization
        output_files = [str(f) for f in event.output_files]

        # Build message payload
        message = {
            "event_id": event.event_id,
            "timestamp": event.timestamp,
            "session_id": self.session_id,
            "command_type": event.command_doc_type,
            "bucket_contents": event.bucket_contents,
            "output_files": output_files,
        }

        # Publish to MQTT
        topic = f"palaver/session/{self.session_id}/command/completed"
        payload = json.dumps(message)
        await self.mqtt_client.publish(topic, payload)

        print(f"[MQTT] Published command completion: {event.command_doc_type}")

    def _update_state(self, state: str, command_type: Optional[str] = None):
        """
        Update session state tracking.

        Args:
            state: New state (idle, awaiting_command, in_command)
            command_type: Command type if entering command state
        """
        self.current_state = state
        if state == "in_command":
            self.command_doc_type = command_type
        elif state == "idle":
            self.command_doc_type = None
            self.current_bucket = None
