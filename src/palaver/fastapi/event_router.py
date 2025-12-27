"""Event routing component for streaming pipeline events to websocket clients.

This module provides the EventRouter class, which routes audio/text/draft events
from the audio pipeline to subscribed websocket clients with server-side filtering.
"""
import asyncio
import logging
from typing import Set, Dict, Any

import numpy as np

from palaver.scribe.audio_events import (
    AudioEvent,
    AudioEventListener,
    AudioRingBuffer,
    AudioChunkEvent,
    AudioSpeechStartEvent,
)
from palaver.scribe.text_events import TextEvent, TextEventListener
from palaver.scribe.draft_events import DraftEvent, DraftEventListener
from palaver.stage_markers import Stage, stage

logger = logging.getLogger("EventRouter")


@stage(Stage.PROTOTYPE, track_coverage=True)
class EventRouter(AudioEventListener, TextEventListener, DraftEventListener):
    """Routes pipeline events to subscribed websocket clients.

    Implements server-side filtering:
    - "all" subscription includes all event types EXCEPT AudioChunkEvent
    - Clients must explicitly subscribe to "AudioChunkEvent" to receive chunks
    - AudioChunkEvent only sent when in_speech=True (VAD detected speech)

    Pre-buffering support:
    - Buffers recent AudioChunkEvents (silence) before speech detection
    - When AudioSpeechStartEvent arrives, emits buffered chunks first
    - Compensates for VAD latency to capture actual speech start

    This is a pure routing component with no FastAPI dependencies, making it
    reusable across different server implementations.
    """

    def __init__(self, pre_buffer_seconds: float = 1.0):
        """Initialize EventRouter with optional pre-buffering.

        Args:
            pre_buffer_seconds: Seconds of audio to buffer before speech detection.
                               Default 1.0 matches WhisperWrapper. Set to 0 to disable.
        """
        self.clients: Dict[Any, Set[str]] = {}
        self._lock = asyncio.Lock()

        # Pre-buffer for capturing audio before VAD detects speech
        if pre_buffer_seconds > 0:
            self._pre_buffer = AudioRingBuffer(max_seconds=pre_buffer_seconds)
        else:
            self._pre_buffer = None

    async def register_client(self, websocket: Any, event_types: Set[str]):
        """Register a websocket client for specific event types.

        Args:
            websocket: WebSocket connection object
            event_types: Set of event type names to subscribe to
        """
        async with self._lock:
            self.clients[websocket] = event_types
            logger.info(f"Client registered for events: {event_types}")

    async def unregister_client(self, websocket: Any):
        """Remove a websocket client from the registry.

        Args:
            websocket: WebSocket connection object to remove
        """
        async with self._lock:
            if websocket in self.clients:
                del self.clients[websocket]
                logger.info("Client unregistered")

    async def on_audio_event(self, event: AudioEvent) -> None:
        """Receive audio events from pipeline and route to clients.

        For AudioChunkEvents with in_speech=False, buffer them for pre-buffering.
        They will be emitted before AudioSpeechStartEvent to capture speech start.
        """
        # Buffer silence chunks for pre-buffering (before speech detection)
        if (isinstance(event, AudioChunkEvent) and
            not getattr(event, 'in_speech', False) and
            self._pre_buffer is not None):
            self._pre_buffer.add(event)
            # Don't route silence chunks yet - they'll be emitted with speech start
            return

        # When speech starts, emit buffered chunks first to capture actual speech start
        if isinstance(event, AudioSpeechStartEvent):
            if self._pre_buffer and self._pre_buffer.has_data():
                logger.info(f"Emitting {len(self._pre_buffer.buffer)} pre-buffered chunks before speech start")
                for buffered_event in self._pre_buffer.get_all(clear=True):
                    await self._route_event(buffered_event)

        await self._route_event(event)

    async def on_text_event(self, event: TextEvent) -> None:
        """Receive text events from pipeline and route to clients."""
        await self._route_event(event)

    async def on_draft_event(self, event: DraftEvent) -> None:
        """Receive draft events from pipeline and route to clients."""
        await self._route_event(event)

    async def on_pipeline_ready(self, pipeline):
        """Called when pipeline is ready."""
        pass

    async def on_pipeline_shutdown(self):
        """Called when pipeline is shutting down."""
        pass

    async def _route_event(self, event: Any):
        """Route event to subscribed clients.

        Server-side filtering:
        - "all" means all event types EXCEPT AudioChunkEvent
        - Client must explicitly subscribe to "AudioChunkEvent" to receive chunks
        - AudioChunkEvent only sent when in_speech=True (VAD detected speech)
        """
        event_type = type(event).__name__

        # Skip AudioChunkEvent if not in speech (silence/irrelevant sound)
        if event_type == "AudioChunkEvent" and not getattr(event, 'in_speech', False):
            return

        # Convert event to JSON-serializable dict
        event_dict = self._serialize_event(event, event_type)

        # Send to subscribed clients
        async with self._lock:
            dead_clients = []
            for websocket, subscribed_types in self.clients.items():
                # Check if client should receive this event
                should_send = False

                if event_type in subscribed_types:
                    # Explicitly subscribed to this event type
                    should_send = True
                elif "all" in subscribed_types and event_type != "AudioChunkEvent":
                    # "all" means everything except AudioChunkEvent
                    should_send = True

                if should_send:
                    try:
                        await websocket.send_json(event_dict)
                    except Exception as e:
                        logger.warning(f"Failed to send to client: {e}")
                        dead_clients.append(websocket)

            # Clean up dead connections
            for websocket in dead_clients:
                del self.clients[websocket]

    def _serialize_event(self, event: Any, event_type: str) -> Dict[str, Any]:
        """Convert event to JSON-serializable dictionary.

        Handles numpy arrays and nested dataclasses.

        Args:
            event: Event object to serialize
            event_type: Name of the event type

        Returns:
            JSON-serializable dictionary representation
        """
        event_dict = {"event_type": event_type}

        # Extract dataclass fields
        if hasattr(event, "__dataclass_fields__"):
            for field_name in event.__dataclass_fields__:
                value = getattr(event, field_name)
                event_dict[field_name] = self._serialize_value(value, field_name)

        return event_dict

    def _serialize_value(self, value: Any, field_name: str = None) -> Any:
        """Recursively serialize a value to JSON-compatible format.

        Args:
            value: Value to serialize
            field_name: Optional field name for special handling

        Returns:
            JSON-serializable representation of value
        """
        # Handle None
        if value is None:
            return None

        # Convert numpy arrays to lists
        if isinstance(value, np.ndarray):
            return value.tolist()

        # Handle event_type enum specially
        if field_name == "event_type":
            if hasattr(value, 'value'):
                return value.value
            else:
                return str(value)

        # Recursively handle nested dataclasses
        if hasattr(value, "__dataclass_fields__"):
            nested_dict = {}
            for nested_field_name in value.__dataclass_fields__:
                nested_value = getattr(value, nested_field_name)
                nested_dict[nested_field_name] = self._serialize_value(nested_value, nested_field_name)
            return nested_dict

        # Handle lists recursively
        if isinstance(value, list):
            return [self._serialize_value(item) for item in value]

        # Handle dicts recursively
        if isinstance(value, dict):
            return {k: self._serialize_value(v) for k, v in value.items()}

        # Return primitive types as-is
        return value
