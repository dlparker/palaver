#!/usr/bin/env python3
"""
MQTT Event Publisher for Scribe transcription system.

Publishes audio events, text events, and command events to MQTT broker.
Excludes AudioChunkEvents to reduce bandwidth.
"""
import json
import logging
from typing import Optional
from datetime import datetime

try:
    import paho.mqtt.client as mqtt
    MQTT_AVAILABLE = True
except ImportError:
    MQTT_AVAILABLE = False

from palaver.scribe.audio_events import (
    AudioEvent, AudioChunkEvent, AudioStartEvent, AudioStopEvent,
    AudioSpeechStartEvent, AudioSpeechStopEvent, AudioErrorEvent,
    AudioEventListener
)
from palaver.scribe.text_events import TextEvent, TextEventListener
from palaver.scribe.scriven.wire_commands import ScribeCommandEvent, CommandEventListener

logger = logging.getLogger("MQTTPublisher")


class MQTTPublisher(AudioEventListener, TextEventListener, CommandEventListener):
    """
    Publishes Scribe events to MQTT broker.

    Subscribes to:
    - Audio events (except chunks)
    - Text events
    - Command events

    Publishes to topics:
    - {base_topic}/audio/{event_type}
    - {base_topic}/text
    - {base_topic}/command

    Usage:
        publisher = MQTTPublisher(
            broker="localhost",
            port=1883,
            base_topic="palaver/scribe"
        )
        await publisher.connect()

        # Add as listener to pipeline components
        listener.add_event_listener(publisher)
        whisper.add_text_event_listener(publisher)
        command_dispatch.add_event_listener(publisher)

        # Later...
        await publisher.disconnect()
    """

    def __init__(
        self,
        broker: str = "localhost",
        port: int = 1883,
        base_topic: str = "palaver/scribe",
        client_id: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
    ):
        if not MQTT_AVAILABLE:
            raise ImportError(
                "paho-mqtt is not installed. Install with: pip install paho-mqtt"
            )

        self.broker = broker
        self.port = port
        self.base_topic = base_topic
        self.client_id = client_id or f"palaver_scribe_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        # Create MQTT client
        self.client = mqtt.Client(client_id=self.client_id)

        # Set credentials if provided
        if username:
            self.client.username_pw_set(username, password)

        # Set callbacks
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect

        self._connected = False

    def _on_connect(self, client, userdata, flags, rc):
        """Callback when connected to MQTT broker."""
        if rc == 0:
            self._connected = True
            logger.info(f"Connected to MQTT broker {self.broker}:{self.port}")
        else:
            logger.error(f"Failed to connect to MQTT broker: {rc}")

    def _on_disconnect(self, client, userdata, rc):
        """Callback when disconnected from MQTT broker."""
        self._connected = False
        if rc != 0:
            logger.warning(f"Unexpected MQTT disconnect: {rc}")
        else:
            logger.info("Disconnected from MQTT broker")

    async def connect(self):
        """Connect to MQTT broker."""
        try:
            self.client.connect(self.broker, self.port, keepalive=60)
            self.client.loop_start()
            logger.info(f"Connecting to MQTT broker {self.broker}:{self.port}...")
        except Exception as e:
            logger.error(f"Failed to connect to MQTT broker: {e}")
            raise

    async def disconnect(self):
        """Disconnect from MQTT broker."""
        self.client.loop_stop()
        self.client.disconnect()
        logger.info("MQTT publisher disconnected")

    def _publish(self, topic: str, payload: dict):
        """Publish JSON payload to MQTT topic."""
        if not self._connected:
            logger.warning("Not connected to MQTT broker, skipping publish")
            return

        try:
            json_payload = json.dumps(payload)
            result = self.client.publish(topic, json_payload, qos=1)
            if result.rc != mqtt.MQTT_ERR_SUCCESS:
                logger.warning(f"Failed to publish to {topic}: {result.rc}")
        except Exception as e:
            logger.error(f"Error publishing to MQTT: {e}")

    async def on_audio_event(self, event: AudioEvent):
        """Handle audio events - skip chunks, publish others."""
        # Skip AudioChunkEvent to reduce bandwidth
        if isinstance(event, AudioChunkEvent):
            return

        # Build event payload
        payload = {
            'event_type': event.event_type.value,
            'timestamp': event.timestamp,
            'event_id': event.event_id,
            'source_id': event.source_id,
        }

        # Add type-specific fields
        if isinstance(event, AudioStartEvent):
            payload.update({
                'sample_rate': event.sample_rate,
                'channels': event.channels,
                'blocksize': event.blocksize,
                'datatype': event.datatype,
            })
        elif isinstance(event, AudioSpeechStartEvent):
            payload.update({
                'silence_period_ms': event.silence_period_ms,
                'vad_threshold': event.vad_threshold,
                'sampling_rate': event.sampling_rate,
                'speech_pad_ms': event.speech_pad_ms,
            })
        elif isinstance(event, AudioErrorEvent):
            payload['message'] = event.message

        # Publish to audio topic with event type
        topic = f"{self.base_topic}/audio"
        self._publish(topic, payload)

    async def on_text_event(self, event: TextEvent):
        """Handle text events."""
        payload = {
            'event_type': 'TEXT_EVENT',
            'timestamp': event.timestamp,
            'event_id': event.event_id,
            'segments': [
                {
                    'start_ms': seg.start_ms,
                    'end_ms': seg.end_ms,
                    'text': seg.text
                }
                for seg in event.segments
            ],
        }

        if event.audio_source_id:
            payload['audio_source_id'] = event.audio_source_id
        if event.audio_start_time:
            payload['audio_start_time'] = event.audio_start_time
        if event.audio_end_time:
            payload['audio_end_time'] = event.audio_end_time

        topic = f"{self.base_topic}/text"
        self._publish(topic, payload)

    async def on_command_event(self, event: ScribeCommandEvent):
        """Handle command events."""
        payload = {
            'event_type': 'COMMAND_EVENT',
            'timestamp': event.text_event.timestamp,
            'command_name': event.command.name,
            'pattern': event.pattern,
            'segment_number': event.segment_number,
            'command_properties': {
                'starts_text_block': event.command.starts_text_block,
                'ends_text_block': event.command.ends_text_block,
                'starts_recording_session': event.command.starts_recording_session,
                'ends_recording_session': event.command.ends_recording_session,
                'stops_audio': event.command.stops_audio,
                'starts_audio': event.command.starts_audio,
            },
            'text_event_id': event.text_event.event_id,
        }

        topic = f"{self.base_topic}/command"
        self._publish(topic, payload)
