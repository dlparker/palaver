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
            event.author_uri = self.uri  # Will be set on EventSender

        disconnected = []
        for ws, subscribed in self.active_connections.items():
            if event_type in subscribed:
                try:
                    await ws.send_json(event_dict)
                except Exception:
                    logger.error("Error sending to client", exc_info=True)
                    disconnected.append(ws)

        for ws in disconnected:
            self.disconnect(ws)

class DraftSubmissionManager:
    async def handle_submission(self, websocket: WebSocket, processor_callback):
        try:
            data = await websocket.receive_json()
            draft_event = event_from_dict(data)

            result = await processor_callback(draft_event)

            await websocket.send_json({
                "status": "success",
                "event_id": draft_event.event_id,
                "result": result or {}
            })

        except WebSocketDisconnect:
            logger.info("Client disconnected during draft submission")
        except Exception as e:
            logger.error("Error processing submitted draft", exc_info=True)
            try:
                await websocket.send_json({
                    "status": "error",
                    "message": str(e)
                })
            except:
                pass
        # No need to disconnect() — FastAPI handles cleanup on return/exit
