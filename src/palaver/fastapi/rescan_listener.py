"""RescanListener - WebSocket client for distributed rescan architecture.

Connects to remote audio source server, buffers audio events, and rescans
completed drafts using local high-quality Whisper model.

Story 008: Rescan Mode for Distributed High-Quality Transcription
"""
import asyncio
import json
import logging
from enum import Enum
from typing import Optional
import httpx
import numpy as np
import websockets

from palaver.scribe.audio_events import (
    AudioRingBuffer, AudioChunkEvent, AudioEvent,
    AudioStartEvent, AudioStopEvent, AudioSpeechStartEvent, AudioSpeechStopEvent,
    AudioErrorEvent
)
from palaver.scribe.draft_events import DraftEvent, DraftStartEvent, DraftEndEvent
from palaver.scribe.text_events import TextEvent
from palaver.scribe.scriven.whisper import WhisperWrapper
from palaver.scribe.scriven.drafts import DraftMaker
from palaver.stage_markers import Stage, stage

logger = logging.getLogger("RescanListener")


class RescanState(Enum):
    """State machine for rescan workflow."""
    IDLE = "idle"                    # Waiting for remote draft to start
    COLLECTING = "collecting"        # Buffering audio for active draft
    RESCANNING = "rescanning"       # Local rescan in progress


@stage(Stage.PROTOTYPE, track_coverage=True)
class RescanListener:
    """Subscribes to remote audio source, buffers audio, rescans completed drafts.

    Workflow:
    1. Connect to remote WebSocket (audio_source_url)
    2. Buffer AudioChunkEvents in AudioRingBuffer
    3. When DraftStartEvent arrives, trim buffer to audio_start_time
    4. Continue buffering until DraftEndEvent
    5. Submit buffered audio to local WhisperWrapper via process_rescan()
    6. Send rescan result to revision_target via HTTP POST

    State Machine:
        IDLE → (remote DraftStartEvent) → COLLECTING
        COLLECTING → (remote DraftEndEvent) → RESCANNING
        RESCANNING → (local DraftEndEvent) → IDLE

    Story 008: Encapsulates rescan processing for future queueing.
    """

    def __init__(
        self,
        audio_source_url: str,
        revision_target: str,
        local_whisper: WhisperWrapper,
        local_draft_maker: DraftMaker,
        buffer_seconds: float = 60.0,
    ):
        """Initialize RescanListener.

        Args:
            audio_source_url: WebSocket URL to subscribe to (e.g., ws://machine1:8765/events)
            revision_target: HTTP URL to send revisions (e.g., http://machine1:8765/api/revisions)
            local_whisper: Local WhisperWrapper for high-quality rescanning
            local_draft_maker: Local DraftMaker to receive rescan results
            buffer_seconds: Audio buffer size in seconds (default 60.0)
        """
        self.audio_source_url = audio_source_url
        self.revision_target = revision_target
        self.whisper = local_whisper
        self.draft_maker = local_draft_maker
        self.buffer_seconds = buffer_seconds

        # Audio buffering
        self.audio_buffer = AudioRingBuffer(max_seconds=buffer_seconds)

        # State tracking
        self.state = RescanState.IDLE
        self.current_draft_id: Optional[str] = None
        self.current_draft_start_time: Optional[float] = None

        # WebSocket connection (initialized in connect())
        self.ws_client = None
        self.ws_task: Optional[asyncio.Task] = None

        # HTTP client for revision submission
        self.http_client = httpx.AsyncClient()

        logger.info(
            f"RescanListener initialized: source={audio_source_url}, "
            f"target={revision_target}, buffer={buffer_seconds}s"
        )

    async def connect(self):
        """Connect to remote audio source WebSocket.

        Subscribes to AudioChunkEvent and DraftEvent, then starts
        background task to receive and route events.

        Task 3: WebSocket client subscription.
        """
        logger.info(f"Connecting to {self.audio_source_url}...")

        try:
            self.ws_client = await websockets.connect(self.audio_source_url)
            logger.info("WebSocket connected")

            # Send subscription message
            subscription = {
                "subscribe": ["AudioChunkEvent", "DraftStartEvent", "DraftEndEvent"]
            }
            await self.ws_client.send(json.dumps(subscription))
            logger.info(f"Subscribed to: {subscription['subscribe']}")

            # Start background task to receive events
            self.ws_task = asyncio.create_task(self._ws_event_loop())
            logger.info("WebSocket event loop started")

        except Exception as e:
            logger.error(f"Failed to connect to {self.audio_source_url}: {e}")
            raise

    def _serialize_draft(self, draft) -> dict:
        """Serialize Draft object to dict for revision submission.

        Recursively handles nested dataclasses (TextMark, Section, TextEvent).

        Args:
            draft: Draft object to serialize

        Returns:
            Dict suitable for JSON serialization
        """
        import dataclasses

        def _serialize_value(obj):
            """Recursively serialize dataclass objects and primitives."""
            if obj is None:
                return None
            elif dataclasses.is_dataclass(obj) and not isinstance(obj, type):
                # Recursively serialize dataclass instance
                return {
                    field.name: _serialize_value(getattr(obj, field.name))
                    for field in dataclasses.fields(obj)
                }
            elif isinstance(obj, list):
                return [_serialize_value(item) for item in obj]
            elif isinstance(obj, dict):
                return {k: _serialize_value(v) for k, v in obj.items()}
            elif isinstance(obj, np.ndarray):
                return obj.tolist()
            else:
                # Primitive types (str, int, float, bool, etc.)
                return obj

        return _serialize_value(draft)

    async def _ws_event_loop(self):
        """Background task to receive and route WebSocket events.

        Deserializes incoming JSON events, reconstructs event objects,
        and routes them to appropriate handlers.

        Task 3: WebSocket event handling loop.
        """
        try:
            async for message in self.ws_client:
                try:
                    event_dict = json.loads(message)
                    event = self._deserialize_event(event_dict)

                    if event:
                        await self._route_event(event)

                except json.JSONDecodeError as e:
                    logger.warning(f"Failed to decode event: {e}")
                except Exception as e:
                    logger.error(f"Error processing event: {e}", exc_info=True)

        except websockets.exceptions.ConnectionClosed:
            logger.warning("WebSocket connection closed")
        except asyncio.CancelledError:
            logger.info("WebSocket event loop cancelled")
            raise
        except Exception as e:
            logger.error(f"WebSocket event loop error: {e}", exc_info=True)

    def _deserialize_event(self, event_dict: dict):
        """Deserialize JSON event dict to event object.

        Task 3: Event deserialization.

        Args:
            event_dict: JSON event dictionary from WebSocket

        Returns:
            Event object or None if deserialization fails
        """
        event_type = event_dict.get("event_type")

        # For Prototype, we only need AudioChunkEvent and DraftEvents
        # Full deserialization would require reconstructing all event types
        # For now, we'll pass the dict and handle it in routing

        # TODO: Proper event deserialization (create actual event objects)
        # For Prototype: store dict and access fields directly
        event_dict['_event_type'] = event_type
        return event_dict

    async def _route_event(self, event):
        """Route deserialized event to appropriate handler.

        Task 3: Event routing.

        Args:
            event: Event object or dict from deserialization
        """
        event_type = event.get('_event_type') if isinstance(event, dict) else type(event).__name__

        if event_type == "AudioChunkEvent":
            # Convert dict to minimal AudioChunkEvent-like object for buffering
            await self.on_audio_event(event)
        elif event_type in ["DraftStartEvent", "DraftEndEvent"]:
            # Convert dict to minimal DraftEvent-like object
            await self.on_draft_event(event)
        else:
            # Ignore other event types
            pass

    async def disconnect(self):
        """Disconnect from remote audio source WebSocket."""
        if self.ws_task:
            self.ws_task.cancel()
            try:
                await self.ws_task
            except asyncio.CancelledError:
                pass
        if self.ws_client:
            await self.ws_client.close()
        await self.http_client.aclose()
        logger.info("RescanListener disconnected")

    async def on_audio_event(self, event):
        """Handle remote AudioEvent (buffer for rescan).

        Task 4: Audio buffering logic.

        Args:
            event: AudioChunkEvent dict from WebSocket deserialization
        """
        # Only buffer AudioChunkEvents while IDLE or COLLECTING
        if self.state not in [RescanState.IDLE, RescanState.COLLECTING]:
            return

        event_type = event.get('_event_type') if isinstance(event, dict) else type(event).__name__

        if event_type == "AudioChunkEvent":
            # For Prototype: Store event dict with timestamp/duration for AudioRingBuffer
            # MVP: Proper event object reconstruction

            # Create minimal event object for buffering
            # AudioRingBuffer needs: timestamp, duration attributes for pruning
            class AudioChunkProxy:
                def __init__(self, event_dict):
                    self.event_dict = event_dict
                    self.timestamp = event_dict.get('timestamp')
                    self.duration = event_dict.get('duration', 0)

                # Pass through attribute access to dict
                def __getattr__(self, name):
                    return self.event_dict.get(name)

            chunk_proxy = AudioChunkProxy(event)
            self.audio_buffer.add(chunk_proxy)

            # Log buffer state periodically (every 100 chunks for debugging)
            if len(self.audio_buffer.buffer) % 100 == 0:
                oldest = self.audio_buffer.buffer[0].timestamp if self.audio_buffer.buffer else None
                newest = self.audio_buffer.buffer[-1].timestamp if self.audio_buffer.buffer else None
                logger.debug(
                    f"Buffer: {len(self.audio_buffer.buffer)} chunks, "
                    f"oldest={oldest}, newest={newest}"
                )

    async def on_draft_event(self, event):
        """Handle DraftEvent from remote or local source.

        Routes to appropriate handler based on author_uri (Story 007).
        - Remote events (from audio source) → handle_remote_draft()
        - Local events (from our WhisperWrapper) → handle_rescan_result()

        Args:
            event: DraftEvent dict from WebSocket or local pipeline
        """
        # Use author_uri to distinguish remote from local events
        author_uri = event.get('author_uri') if isinstance(event, dict) else getattr(event, 'author_uri', None)
        is_remote = author_uri and self.audio_source_url in author_uri

        if is_remote:
            await self.handle_remote_draft(event)
        else:
            await self.handle_rescan_result(event)

    async def handle_remote_draft(self, event):
        """Handle DraftEvent from remote audio source.

        Task 5: DraftStartEvent handling
        Task 6: DraftEndEvent handling (rescan trigger)

        Args:
            event: DraftEvent dict from WebSocket
        """
        event_type = event.get('_event_type') if isinstance(event, dict) else type(event).__name__
        draft = event.get('draft', {}) if isinstance(event, dict) else event.draft

        if event_type == "DraftStartEvent":
            # Task 5: DraftStartEvent handling
            await self._handle_draft_start(event, draft)
        elif event_type == "DraftEndEvent":
            # Task 6: DraftEndEvent handling (rescan trigger)
            await self._handle_draft_end(event, draft)

    async def _handle_draft_start(self, event, draft):
        """Handle remote DraftStartEvent.

        Task 5: Trim buffer to audio_start_time, transition to COLLECTING.

        Args:
            event: DraftStartEvent dict
            draft: Draft dict from event
        """
        draft_id = draft.get('draft_id') if isinstance(draft, dict) else draft.draft_id
        audio_start_time = draft.get('audio_start_time') if isinstance(draft, dict) else draft.audio_start_time

        logger.info(f"Remote draft started: {draft_id}")

        # Check state
        if self.state != RescanState.IDLE:
            logger.warning(
                f"Draft already in progress ({self.state}), ignoring new draft {draft_id}. "
                "Prototype limitation: one draft at a time."
            )
            return

        # Store draft context
        self.current_draft_id = draft_id
        self.current_draft_start_time = audio_start_time

        # Trim buffer to audio_start_time (remove older audio)
        # AudioRingBuffer doesn't have trim_before(), so we'll handle this in extraction
        # For Prototype: Keep full buffer, extract range later
        logger.info(
            f"Draft context stored: draft_id={draft_id}, "
            f"audio_start_time={audio_start_time}"
        )

        # Transition state: IDLE → COLLECTING
        self.state = RescanState.COLLECTING
        logger.info(f"State transition: IDLE → COLLECTING (draft {draft_id})")

    async def handle_rescan_result(self, event: DraftEvent):
        """Handle DraftEvent from local WhisperWrapper (rescan result).

        Task 7: Submit revision to revision_target.
        """
        if isinstance(event, DraftEndEvent):
            logger.info(f"Local rescan complete: {event.draft.draft_id}")

            # Verify we're in RESCANNING state
            if self.state != RescanState.RESCANNING:
                logger.warning(
                    f"Unexpected rescan result in state {self.state}, ignoring"
                )
                return

            try:
                # Serialize draft to dict (recursively handle nested dataclasses)
                revised_draft_dict = self._serialize_draft(event.draft)

                # Prepare revision submission payload
                revision_payload = {
                    "original_draft_id": self.current_draft_id,
                    "revised_draft": revised_draft_dict,
                    "metadata": {
                        "model": self.whisper.model_path if hasattr(self.whisper, 'model_path') else "unknown",
                        "source": "whisper_reprocess",
                        "source_uri": event.author_uri or "local",
                        "timestamp": event.timestamp,
                    }
                }

                # POST revision to remote server
                logger.info(f"Submitting revision for draft {self.current_draft_id} to {self.revision_target}")
                response = await self.http_client.post(
                    self.revision_target,
                    json=revision_payload,
                    timeout=10.0
                )

                response.raise_for_status()
                result = response.json()

                logger.info(
                    f"Revision submitted successfully: revision_id={result.get('revision_id')}, "
                    f"original_draft_id={self.current_draft_id}"
                )

            except httpx.HTTPStatusError as e:
                logger.error(
                    f"HTTP error submitting revision: {e.response.status_code} - {e.response.text}",
                    exc_info=True
                )
            except httpx.RequestError as e:
                logger.error(f"Network error submitting revision: {e}", exc_info=True)
            except Exception as e:
                logger.error(f"Failed to submit revision: {e}", exc_info=True)
            finally:
                # Transition state: RESCANNING → IDLE
                self.state = RescanState.IDLE
                self.current_draft_id = None
                self.current_draft_start_time = None
                logger.info("State transition: RESCANNING → IDLE")

    async def _handle_draft_end(self, event, draft):
        """Handle remote DraftEndEvent.

        Task 6: Extract audio, call process_rescan(), transition to RESCANNING.

        Args:
            event: DraftEndEvent dict
            draft: Draft dict from event
        """
        draft_id = draft.get('draft_id') if isinstance(draft, dict) else draft.draft_id

        logger.info(f"Remote draft ended: {draft_id}")

        # Check state
        if self.state != RescanState.COLLECTING:
            logger.warning(
                f"Unexpected draft end in state {self.state}, ignoring draft {draft_id}"
            )
            return

        # Verify this is the current draft
        if draft_id != self.current_draft_id:
            logger.warning(
                f"Draft ID mismatch: expected {self.current_draft_id}, got {draft_id}"
            )
            return

        # Transition state: COLLECTING → RESCANNING
        self.state = RescanState.RESCANNING
        logger.info(f"State transition: COLLECTING → RESCANNING (draft {draft_id})")

        try:
            # Call encapsulated process_rescan method
            rescan_result = await self.process_rescan(event, self.audio_buffer)
            logger.info(f"Rescan complete: {draft_id}")

            # rescan_result will be handled by handle_rescan_result() when
            # local DraftEndEvent arrives from WhisperWrapper

        except Exception as e:
            logger.error(f"Rescan failed for draft {draft_id}: {e}", exc_info=True)
            # Transition back to IDLE on failure
            self.state = RescanState.IDLE
            self.current_draft_id = None
            self.current_draft_start_time = None

    async def process_rescan(
        self,
        draft_event,
        audio_buffer: AudioRingBuffer
    ):
        """Encapsulated rescan processing for future queueing.

        Extracts audio segment from buffer, submits to WhisperWrapper,
        and waits for rescan to complete via local DraftMaker.

        This method encapsulates all rescan logic to enable future queueing
        without redesigning the pipeline (Story 008 architectural requirement).

        Args:
            draft_event: Remote DraftEndEvent dict that triggered rescan
            audio_buffer: AudioRingBuffer containing buffered audio

        Raises:
            Exception: If rescan fails (caller handles error)

        Task 6: Audio extraction and submission implementation.
        """
        # Extract draft info
        draft = draft_event.get('draft', {}) if isinstance(draft_event, dict) else draft_event.draft
        draft_id = draft.get('draft_id') if isinstance(draft, dict) else draft.draft_id
        audio_start_time = draft.get('audio_start_time') if isinstance(draft, dict) else draft.audio_start_time
        audio_end_time = draft.get('audio_end_time') if isinstance(draft, dict) else draft.audio_end_time

        logger.info(
            f"process_rescan: draft_id={draft_id}, "
            f"start={audio_start_time}, end={audio_end_time}"
        )

        # Extract audio segment from buffer
        # get_from() returns events with timestamp >= start_time
        buffered_events = audio_buffer.get_from(audio_start_time) if audio_start_time else audio_buffer.get_all()

        # Filter to audio_end_time if provided
        if audio_end_time:
            buffered_events = [
                e for e in buffered_events
                if e.timestamp <= audio_end_time
            ]

        if not buffered_events:
            raise ValueError(
                f"No audio in buffer for draft {draft_id} "
                f"(start={audio_start_time}, end={audio_end_time})"
            )

        logger.info(
            f"Extracted {len(buffered_events)} audio chunks for rescan "
            f"({buffered_events[0].timestamp} to {buffered_events[-1].timestamp})"
        )

        # Reconstruct AudioChunkEvent objects from dicts and submit to WhisperWrapper
        # Task 6: Audio event reconstruction
        for chunk_proxy in buffered_events:
            # chunk_proxy is AudioChunkProxy wrapping event_dict
            event_dict = chunk_proxy.event_dict

            # Reconstruct numpy array from serialized list
            # EventRouter._serialize_value() converts np.ndarray to list
            data_list = event_dict.get('data', [])
            if not data_list:
                logger.warning(f"Skipping chunk with no audio data at {chunk_proxy.timestamp}")
                continue

            # Convert back to numpy array (float32, shape (samples, channels))
            data = np.array(data_list, dtype=np.float32)

            # Reconstruct AudioChunkEvent
            # Note: Some fields use defaults (event_id, creation_location auto-generated)
            chunk_event = AudioChunkEvent(
                source_id=event_dict.get('source_id', 'unknown'),
                stream_start_time=event_dict.get('stream_start_time', 0.0),
                speech_start_time=event_dict.get('speech_start_time'),
                timestamp=event_dict.get('timestamp', chunk_proxy.timestamp),
                data=data,
                duration=event_dict.get('duration', chunk_proxy.duration),
                sample_rate=event_dict.get('sample_rate', 16000),
                channels=event_dict.get('channels', 1),
                blocksize=event_dict.get('blocksize', 0),
                datatype=event_dict.get('datatype', 'float32'),
                in_speech=event_dict.get('in_speech', True),  # Default True for rescan
                author_uri=event_dict.get('author_uri'),
            )

            # Submit to local WhisperWrapper
            await self.whisper.on_audio_event(chunk_event)

        logger.info(f"Submitted {len(buffered_events)} chunks to local WhisperWrapper for rescan")

        # Note: In Prototype, we don't wait for result here - it will arrive
        # via local DraftMaker → handle_rescan_result() pathway
        # This is intentional - the local pipeline operates independently
