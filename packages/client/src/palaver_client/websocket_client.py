import asyncio
import json
import logging
from typing import Optional, Callable, Awaitable
import websockets
from websockets.client import WebSocketClientProtocol
from palaver_shared.serializers import event_from_dict
from palaver_shared.audio_events import AudioEvent
from palaver_shared.text_events import TextEvent
from palaver_shared.draft_events import DraftEvent

from palaver_client.api import PalaverEventListener
logger = logging.getLogger("PalaverWebSocketClient")

# Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

class PalaverWebSocketClient:
    
    def __init__(self,
                 listener: PalaverEventListener,
                 palaver_url:str,
                 auto_reconnect: bool = True,
                 reconnect_delay: float = 5.0):
        self.listener = listener
        self.base_url = palaver_url

        self.auto_reconnect = auto_reconnect
        self.reconnect_delay = reconnect_delay

        # Convert http:// to ws://
        ws_url = self.base_url.replace("http://", "ws://").replace("https://", "wss://")
        self.ws_url = ws_url.rstrip('/') + "/events"
        
        self._websocket: Optional[WebSocketClientProtocol] = None
        self._event_handler: Optional[Callable[[dict], Awaitable[None]]] = None
        self._subscribed_events: list[str] = []
        self._running = False
        self._listen_task: Optional[asyncio.Task] = None

    async def connect(self) -> None:
        logger.info(f"Connecting to palaver at {self.ws_url}")
        self._websocket = await websockets.connect(self.ws_url)

        # Send subscription request
        subscription = {"subscribe": self.listener.event_types}
        await self._websocket.send(json.dumps(subscription))
        logger.info(f"Subscribed to events: {self.listener.event_types}")

    async def disconnect(self) -> None:
        self._running = False

        if self._listen_task:
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass
            self._listen_task = None

        if self._websocket:
            await self._websocket.close()
            self._websocket = None
            logger.info("Disconnected from palaver")

    async def listen(self) -> None:
        self._running = True

        while self._running:
            try:
                # Connect if not connected
                if not self._websocket or self._websocket.closed:
                    logger.debug(f"connecting")
                    await self.connect()

                # Listen for events
                async for message in self._websocket:
                    if not self._running:
                        break

                    event_data = json.loads(message)
                    logger.debug(f"Received event: {event_data}")
                    event = event_from_dict(event_data)
                    
                    logger.debug(f"Received event: {str(event)}")
                    
                    if isinstance(event, AudioEvent):
                        await self.listener.on_audio_event(event)
                    if isinstance(event, TextEvent):
                        await self.listener.on_text_event(event)
                    if isinstance(event, DraftEvent):
                        await self.listener.on_draft_event(event)
            except websockets.ConnectionClosed as e:
                logger.warning(f"WebSocket connection closed: {e}")

                if not self.auto_reconnect or not self._running:
                    break

                logger.info(f"Reconnecting in {self.reconnect_delay} seconds...")
                await asyncio.sleep(self.reconnect_delay)

        logger.info("Stopped listening for events")

    def start_listening(self) -> asyncio.Task:
        self._listen_task = asyncio.create_task(self.listen())
        return self._listen_task

    async def __aenter__(self):
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.disconnect()
