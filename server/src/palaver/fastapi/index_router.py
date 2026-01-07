import asyncio
import logging
from typing import Any, Optional
import traceback
import json
import socket
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = logging.getLogger("IndexRouter")

class IndexRouter:
    def __init__(self, server):
        self.server = server
        self.my_port = server.port
        self.mode = server.mode
        self.audio_listener = server.audio_listener
        self.draft_recorder = server.draft_recorder
        self.pipeline = None
        self.hostname = socket.gethostname()
        self.ip_address = socket.gethostbyname(self.hostname)
        self.url_base = f"http://{self.hostname}:{self.my_port}"
        self.ws_url_base = f"ws://{self.hostname}:{self.my_port}"
        self.rescanner = None
        self.last_rescanner_registration: Optional[float] = None
        self.audio_source_url = self.ws_url_base

    def is_rescanner_available(self, timeout: float = 5.0) -> bool:
        """Check if rescanner is available (registered within timeout seconds)."""
        if self.last_rescanner_registration is None:
            return False
        elapsed = time.time() - self.last_rescanner_registration
        return elapsed < timeout

    async def become_router(self):
        self.pipeline = self.server.pipeline
        self.audio_source_url = self.server.get_audio_url()
        
        router = APIRouter()
        
        @router.get("/index")
        async def index():
            res = dict(event_stream=f"{self.ws_url_base}/events",
                       add_stream=f"{self.ws_url_base}/new_draft",
                       register_rescanner=f"{self.ws_url_base}/register_rescanner",
                       status=f"{self.url_base}/status",
                       health=f"{self.url_base}/health",
                       )
            return res

        @router.get("/health")
        async def health_check():
            return {"status": "healthy"}

        @router.get("/status")
        async def server_status():
            return {
                "status": "running",
                "pipeline_active": self.pipeline is not None,
                "event_clients": len(self.server.event_router.active_connections),
                "url": self.url_base,
                "ws_url": self.ws_url_base,
                "rescanner_url": self.rescanner,
                "rescanner_available": self.is_rescanner_available(),
                "last_rescanner_ping": self.last_rescanner_registration
            }

        @router.websocket("/register_rescanner")
        async def register_rescanner(websocket: WebSocket):
            await websocket.accept()
            data = await websocket.receive_json()
            self.rescanner = data['url']
            self.last_rescanner_registration = time.time()
            logger.debug(f"Rescanner registered from {self.rescanner}")
            res = {'code': 'success'}
            await websocket.send_json(res)
        

        @router.websocket("/get_rescanner")
        async def register_rescanner(websocket: WebSocket):
            await websocket.accept()
            data = await websocket.receive_json()
            res = {'code': 'success', 'url': self.rescanner}
            await websocket.send_json(res)
        

        return router            

