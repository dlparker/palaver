# palaver/scribe/websocket_managers.py
from typing import Any
import logging

from fastapi import WebSocket, WebSocketDisconnect

from palaver.utils.serializers import serialize_event

logger = logging.getLogger("WebSockets")


# websocket_managers.py
import logging
from typing import Set, Dict
from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

class PipelineEventManager:
    def __init__(self):
        self.active_connections: Dict[WebSocket, set[str]] = {}

    async def connect(self, websocket: WebSocket, event_types: set[str]):
        self.active_connections[websocket] = event_types
        logger.info(f"Client connected and subscribed to: {event_types}")
        logger.info(f"Total event clients: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            del self.active_connections[websocket]
            logger.info(f"Client disconnected. Remaining: {len(self.active_connections)}")

    async def send_to_subscribers(self, event):
        if not self.active_connections:
            return

        event_type = str(event.__class__)
        event_dict = serialize_event(event)
        if event.author_uri is None:
            event.author_uri = self.uri  # Will be set on EventRouter

        disconnected = []
        for ws, subscribed in list(self.active_connections.items()):
            if event_type in subscribed:
                try:
                    await ws.send_json(event_dict)
                except Exception:
                    logger.error("Error sending to client", exc_info=False)
                    disconnected.append(ws)

        for ws in disconnected:
            self.disconnect(ws)

