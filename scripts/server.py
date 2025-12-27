#!/usr/bin/env python3
"""Event Net Server - POC for streaming audio pipeline events via websockets.

This is a POC-stage implementation to prove the concept of streaming events
from the audio pipeline to remote clients via FastAPI websockets.
"""
import asyncio
import json
import logging
from pathlib import Path
from typing import Set, Dict, Any
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
import uvicorn
import numpy as np

from palaver.scribe.audio.mic_listener import MicListener
from palaver.scribe.core import PipelineConfig, ScribePipeline
from palaver.scribe.audio_events import AudioEvent, AudioEventListener, AudioChunkEvent
from palaver.scribe.text_events import TextEvent, TextEventListener
from palaver.scribe.draft_events import DraftEvent, DraftEventListener
from palaver.scribe.recorders.sql_drafts import SQLDraftRecorder
from palaver.stage_markers import Stage, stage
from palaver.utils.top_error import TopErrorHandler, TopLevelCallback, ERROR_HANDLER
from script_utils import create_base_parser, validate_model_path
from loggers import setup_logging
from api_wrapper import DefaultAPIWrapper

logger = logging.getLogger("EventNetServer")


@stage(Stage.POC, track_coverage=False)
class EventRouter(AudioEventListener, TextEventListener, DraftEventListener):
    """Routes pipeline events to subscribed websocket clients.

    POC implementation - minimal subscription management, no error recovery.
    """

    def __init__(self):
        self.clients: Dict[WebSocket, Set[str]] = {}
        self._lock = asyncio.Lock()

    async def register_client(self, websocket: WebSocket, event_types: Set[str]):
        """Register a websocket client for specific event types."""
        async with self._lock:
            self.clients[websocket] = event_types
            logger.info(f"Client registered for events: {event_types}")

    async def unregister_client(self, websocket: WebSocket):
        """Remove a websocket client from the registry."""
        async with self._lock:
            if websocket in self.clients:
                del self.clients[websocket]
                logger.info("Client unregistered")

    async def on_audio_event(self, event: AudioEvent) -> None:
        """Receive audio events from pipeline and route to clients."""
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
        """Route event to subscribed clients."""
        event_type = type(event).__name__

        # Convert event to JSON-serializable dict
        event_dict = self._serialize_event(event, event_type)

        # Send to subscribed clients
        async with self._lock:
            dead_clients = []
            for websocket, subscribed_types in self.clients.items():
                if "all" in subscribed_types or event_type in subscribed_types:
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
        """
        event_dict = {"event_type": event_type}

        # Extract dataclass fields
        if hasattr(event, "__dataclass_fields__"):
            for field_name in event.__dataclass_fields__:
                value = getattr(event, field_name)
                event_dict[field_name] = self._serialize_value(value, field_name)

        return event_dict

    def _serialize_value(self, value: Any, field_name: str = None) -> Any:
        """Recursively serialize a value to JSON-compatible format."""
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


@stage(Stage.POC, track_coverage=False)
class EventNetServer:
    """FastAPI server for streaming audio pipeline events via websockets.

    POC implementation - proves the concept works, minimal features.
    """

    def __init__(self, model_path: Path, draft_dir: Path = None):
        self.model_path = model_path
        self.draft_dir = draft_dir
        self.event_router = EventRouter()
        self.pipeline = None
        self.mic_listener = None
        self.app = FastAPI(lifespan=self.lifespan)

        # Register websocket endpoint
        self.app.add_websocket_route("/events", self.websocket_endpoint)

    @asynccontextmanager
    async def lifespan(self, app: FastAPI):
        """Manage pipeline lifecycle with FastAPI app."""
        # Setup error handler for pipeline context
        class ErrorCallback(TopLevelCallback):
            async def on_error(self, error_dict: dict):
                logger.error(f"Pipeline error: {error_dict}")

        error_handler = TopErrorHandler(top_level_callback=ErrorCallback(), logger=logger)
        token = ERROR_HANDLER.set(error_handler)

        try:
            # Startup: Create and start pipeline
            logger.info("Starting audio pipeline...")

            # Setup draft recorder if requested
            draft_recorder = None
            if self.draft_dir:
                draft_recorder = SQLDraftRecorder(self.draft_dir)
                logger.info(f"Draft recorder enabled: {self.draft_dir}")

            # Create API wrapper
            api_wrapper = DefaultAPIWrapper(draft_recorder=draft_recorder)

            # Create mic listener
            self.mic_listener = MicListener(chunk_duration=0.03)

            # Create pipeline config
            config = PipelineConfig(
                model_path=self.model_path,
                api_listener=api_wrapper,
                target_samplerate=16000,
                target_channels=1,
                use_multiprocessing=True,
            )

            # Start pipeline with nested context managers
            async with self.mic_listener:
                async with ScribePipeline(self.mic_listener, config) as pipeline:
                    self.pipeline = pipeline

                    # Add event router as listener
                    pipeline.add_api_listener(self.event_router)

                    # Start listening
                    await pipeline.start_listener()
                    logger.info("Audio pipeline started")

                    # Yield to run the app
                    yield

                    # Shutdown handled by context manager exit
                    logger.info("Shutting down audio pipeline...")
        finally:
            ERROR_HANDLER.reset(token)

    async def websocket_endpoint(self, websocket: WebSocket):
        """Handle websocket connections for event streaming.

        Protocol:
            Client sends: {"subscribe": ["EventType1", "EventType2", ...]}
            Or: {"subscribe": ["all"]}

            Server streams events as JSON dicts.
        """
        await websocket.accept()
        logger.info("Client connected")

        try:
            # Wait for subscription message
            data = await websocket.receive_json()
            event_types = set(data.get("subscribe", []))

            if not event_types:
                await websocket.close(code=1003, reason="No event types specified")
                return

            # Register client
            await self.event_router.register_client(websocket, event_types)
            logger.info(f"Client subscribed to: {event_types}")

            # Keep connection alive until client disconnects
            while True:
                await asyncio.sleep(1)

        except WebSocketDisconnect:
            logger.info("Client disconnected")
        except Exception as e:
            logger.error(f"Error in websocket handler: {e}", exc_info=True)
        finally:
            await self.event_router.unregister_client(websocket)


@stage(Stage.POC, track_coverage=False)
def create_parser():
    """Create argument parser for server."""
    default_model = Path("models/ggml-base.en.bin")
    parser = create_base_parser(
        'Event Net Server - Stream audio pipeline events via websockets',
        default_model
    )

    parser.add_argument(
        '--output-dir',
        type=Path,
        default=None,
        help='Enable draft recording to this directory (disabled if not provided)'
    )

    parser.add_argument(
        '--host',
        type=str,
        default='127.0.0.1',
        help='Host to bind server (default: 127.0.0.1)'
    )

    parser.add_argument(
        '--port',
        type=int,
        default=8000,
        help='Port to bind server (default: 8000)'
    )

    return parser


def main():
    parser = create_parser()
    args = parser.parse_args()

    # Set logging level
    setup_logging(
        default_level=args.log_level,
        info_loggers=[logger.name],
        debug_loggers=[],
        more_loggers=[logger]
    )

    # Validate model path
    validate_model_path(args, parser)

    # Create server
    server = EventNetServer(
        model_path=args.model,
        draft_dir=args.output_dir
    )

    # Run server
    logger.info(f"Starting server on {args.host}:{args.port}")
    uvicorn.run(
        server.app,
        host=args.host,
        port=args.port,
        log_level=args.log_level.lower()
    )


if __name__ == "__main__":
    main()
