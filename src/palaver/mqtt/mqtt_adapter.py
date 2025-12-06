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
    SpeechStarted,
    CommandDetected,
    BucketStarted,
    BucketFilled,
    CommandCompleted,
    CommandAborted,
    RecordingStateChanged,
    VADModeChanged,
    QueueStatus,
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

            # Publish events based on type
            if isinstance(event, RecordingStateChanged):
                await self._publish_recording_state(event)
                # Publish session_started when recording begins
                if event.is_recording:
                    await self._publish_session_started(event)

            elif isinstance(event, VADModeChanged):
                await self._publish_vad_mode(event)

            elif isinstance(event, SpeechStarted):
                await self._publish_speech_activity(event, started=True)

            elif isinstance(event, SpeechEnded):
                await self._publish_speech_activity(event, started=False)

            elif isinstance(event, QueueStatus):
                await self._publish_queue_status(event)

            elif isinstance(event, TranscriptionComplete):
                await self._publish_segment(event)

            elif isinstance(event, CommandDetected):
                self._update_state("in_command", event.command_doc_type)
                await self._publish_command_detected(event)

            elif isinstance(event, BucketStarted):
                self.current_bucket = event.bucket_name
                await self._publish_bucket_started(event)

            elif isinstance(event, BucketFilled):
                await self._publish_bucket_filled(event)

            elif isinstance(event, CommandCompleted):
                await self._publish_command_completion(event)
                self._update_state("idle")

            elif isinstance(event, CommandAborted):
                await self._publish_command_aborted(event)
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

    async def _publish_session_started(self, event: RecordingStateChanged):
        """
        Publish session_started event when recording begins.

        Topic: palaver/session/{session_id}/session_started

        Args:
            event: RecordingStateChanged event
        """
        message = {
            "event_id": event.event_id,
            "timestamp": event.timestamp,
            "session_id": self.session_id,
        }

        topic = f"palaver/session/{self.session_id}/session_started"
        payload = json.dumps(message)
        await self.mqtt_client.publish(topic, payload)

        print(f"[MQTT] Published session_started: {self.session_id}")

    async def _publish_recording_state(self, event: RecordingStateChanged):
        """
        Publish recording state change.

        Topic: palaver/session/{session_id}/recording_state

        Args:
            event: RecordingStateChanged event
        """
        message = {
            "event_id": event.event_id,
            "timestamp": event.timestamp,
            "session_id": self.session_id,
            "is_recording": event.is_recording,
        }

        topic = f"palaver/session/{self.session_id}/recording_state"
        payload = json.dumps(message)
        await self.mqtt_client.publish(topic, payload)

        status = "STARTED" if event.is_recording else "STOPPED"
        print(f"[MQTT] Published recording_state: {status}")

    async def _publish_vad_mode(self, event: VADModeChanged):
        """
        Publish VAD mode change.

        Topic: palaver/session/{session_id}/vad_mode

        Args:
            event: VADModeChanged event
        """
        message = {
            "event_id": event.event_id,
            "timestamp": event.timestamp,
            "session_id": self.session_id,
            "mode": event.mode,
            "min_silence_ms": event.min_silence_ms,
        }

        topic = f"palaver/session/{self.session_id}/vad_mode"
        payload = json.dumps(message)
        await self.mqtt_client.publish(topic, payload)

        print(f"[MQTT] Published vad_mode: {event.mode} ({event.min_silence_ms}ms)")

    async def _publish_speech_activity(self, event, started: bool):
        """
        Publish speech activity (started or ended).

        Topic: palaver/session/{session_id}/speech_activity

        Args:
            event: SpeechStarted or SpeechEnded event
            started: True for SpeechStarted, False for SpeechEnded
        """
        message = {
            "event_id": event.event_id,
            "timestamp": event.timestamp,
            "session_id": self.session_id,
            "started": started,
            "segment_index": event.segment_index,
        }

        # Add duration for SpeechEnded events
        if not started and hasattr(event, 'duration_sec'):
            message["duration_sec"] = event.duration_sec
            message["kept"] = event.kept

        topic = f"palaver/session/{self.session_id}/speech_activity"
        payload = json.dumps(message)
        await self.mqtt_client.publish(topic, payload)

    async def _publish_queue_status(self, event: QueueStatus):
        """
        Publish transcription queue status.

        Topic: palaver/session/{session_id}/queue_status

        Args:
            event: QueueStatus event
        """
        message = {
            "event_id": event.event_id,
            "timestamp": event.timestamp,
            "session_id": self.session_id,
            "queued_jobs": event.queued_jobs,
            "completed_transcriptions": event.completed_transcriptions,
            "total_segments": event.total_segments,
        }

        topic = f"palaver/session/{self.session_id}/queue_status"
        payload = json.dumps(message)
        await self.mqtt_client.publish(topic, payload)

    async def _publish_command_detected(self, event: CommandDetected):
        """
        Publish command detection.

        Topic: palaver/session/{session_id}/command/detected

        Args:
            event: CommandDetected event
        """
        message = {
            "event_id": event.event_id,
            "timestamp": event.timestamp,
            "session_id": self.session_id,
            "command_doc_type": event.command_doc_type,
            "command_phrase": event.command_phrase,
            "matched_text": event.matched_text,
            "similarity_score": event.similarity_score,
        }

        topic = f"palaver/session/{self.session_id}/command/detected"
        payload = json.dumps(message)
        await self.mqtt_client.publish(topic, payload)

        print(f"[MQTT] Published command/detected: {event.command_doc_type}")

    async def _publish_bucket_started(self, event: BucketStarted):
        """
        Publish bucket started.

        Topic: palaver/session/{session_id}/bucket/started

        Args:
            event: BucketStarted event
        """
        message = {
            "event_id": event.event_id,
            "timestamp": event.timestamp,
            "session_id": self.session_id,
            "command_doc_type": event.command_doc_type,
            "bucket_name": event.bucket_name,
            "bucket_display_name": event.bucket_display_name,
            "bucket_index": event.bucket_index,
            "start_window_sec": event.start_window_sec,
        }

        topic = f"palaver/session/{self.session_id}/bucket/started"
        payload = json.dumps(message)
        await self.mqtt_client.publish(topic, payload)

        print(f"[MQTT] Published bucket/started: {event.bucket_display_name}")

    async def _publish_bucket_filled(self, event: BucketFilled):
        """
        Publish bucket filled.

        Topic: palaver/session/{session_id}/bucket/filled

        Args:
            event: BucketFilled event
        """
        message = {
            "event_id": event.event_id,
            "timestamp": event.timestamp,
            "session_id": self.session_id,
            "command_doc_type": event.command_doc_type,
            "bucket_name": event.bucket_name,
            "bucket_display_name": event.bucket_display_name,
            "text": event.text,
            "duration_sec": event.duration_sec,
            "chunk_count": event.chunk_count,
        }

        topic = f"palaver/session/{self.session_id}/bucket/filled"
        payload = json.dumps(message)
        await self.mqtt_client.publish(topic, payload)

        print(f"[MQTT] Published bucket/filled: {event.bucket_display_name}")

    async def _publish_command_aborted(self, event: CommandAborted):
        """
        Publish command aborted.

        Topic: palaver/session/{session_id}/command/aborted

        Args:
            event: CommandAborted event
        """
        message = {
            "event_id": event.event_id,
            "timestamp": event.timestamp,
            "session_id": self.session_id,
            "command_doc_type": event.command_doc_type,
            "reason": event.reason,
            "partial_buckets": event.partial_buckets,
        }

        topic = f"palaver/session/{self.session_id}/command/aborted"
        payload = json.dumps(message)
        await self.mqtt_client.publish(topic, payload)

        print(f"[MQTT] Published command/aborted: {event.reason}")
