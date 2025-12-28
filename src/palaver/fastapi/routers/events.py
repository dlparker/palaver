"""Event streaming router factory for FastAPI websocket endpoints.

Provides factory function to create an APIRouter with websocket event streaming
functionality using the EventNetServer's shared event router.
"""
import asyncio
import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from palaver.stage_markers import Stage, stage

if TYPE_CHECKING:
    from palaver.fastapi.server import EventNetServer

logger = logging.getLogger("EventsRouter")


@stage(Stage.PROTOTYPE, track_coverage=True)
def create_event_router(server: "EventNetServer") -> APIRouter:
    """Create FastAPI router for event streaming via websockets.

    The router provides a /events websocket endpoint that:
    - Accepts subscription messages from clients
    - Routes pipeline events through the server's EventRouter
    - Handles client disconnection

    Protocol:
        Client sends: {"subscribe": ["EventType1", "EventType2", ...]}
        Or: {"subscribe": ["all"]}

        Server streams events as JSON dicts.

    Args:
        server: EventNetServer instance providing shared event router

    Returns:
        APIRouter configured with websocket endpoint
    """
    router = APIRouter()

    @router.websocket("/events")
    async def websocket_endpoint(websocket: WebSocket):
        """Handle websocket connections for event streaming.

        Waits for subscription message, registers client with event router,
        and maintains connection until client disconnects.
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

            # Register client with shared event router
            await server.event_router.register_client(websocket, event_types)
            logger.info(f"Client subscribed to: {event_types}")

            # Keep connection alive until client disconnects
            while True:
                await asyncio.sleep(1)

        except WebSocketDisconnect:
            logger.info("Client disconnected")
        finally:
            await server.event_router.unregister_client(websocket)

    return router
