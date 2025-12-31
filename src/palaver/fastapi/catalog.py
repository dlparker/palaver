import asyncio
import logging
from typing import Any
import traceback
import json
import socket

from fastapi import APIRouter


logger = logging.getLogger("WebCatalog")

class WebCatalog:
    def __init__(self, my_port: int, server):
        self.my_port = my_port
        self.server = server
        self.hostname = socket.gethostname()
        self.ip_address = socket.gethostbyname(self.hostname)
        self.url_base = f"http://{self.hostname}:{self.my_port}"
        self.ws_url_base = f"ws://{self.hostname}:{self.my_port}"

    async def become_router(self):
        router = APIRouter()

        @router.get("/index")
        async def health_check():
            res = dict(event_stream=f"{self.ws_url_base}/events",
                       add_stream=f"{self.ws_url_base}/new_draft",
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
                "pipeline_active": self.server.pipeline is not None,
                "event_clients": len(self.server.event_sender.event_manager.active_connections),
                "url": self.url_base
            }

        return router            
