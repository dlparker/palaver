"""Event routing component for streaming pipeline events to websocket clients.

This module provides the EventRouter class, which routes audio/text/draft events
from the audio pipeline to subscribed websocket clients with server-side filtering.
"""
import asyncio
import logging
from typing import Any
from dataclasses import asdict
import traceback
import socket

import numpy as np
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from palaver.utils.serializers import serialize_event
from palaver.scribe.audio_events import (
    AudioEvent,
    AudioStartEvent,
    AudioStopEvent,
    AudioChunkEvent,
    AudioSpeechStartEvent,
    AudioSpeechStopEvent,
    AudioErrorEvent,
)
from palaver.scribe.text_events import TextEvent
from palaver.scribe.draft_events import DraftEvent, DraftStartEvent, DraftEndEvent, DraftRevisionEvent


logger = logging.getLogger("EventSender")

class EventSender:

    def __init__(self, my_port, server):
        self.my_port = my_port
        self.server = server
        self.hostname = socket.gethostname()
        self.ip_address = socket.gethostbyname(self.hostname)
        self.uri = f"http://{self.hostname}:{self.my_port}/scribe_events/v1-0"
        self.clients: Dict[Any, Set[str]] = {}
        logger.info("Sender started with uri %s", self.uri)

    async def send_event(self, event: [AudioEvent | TextEvent | DraftEvent]):
        if event.author_uri is None:
            event.author_uri = self.uri
        event_dict = serialize_event(event)
        event_type = str(event.__class__)
        deleters = []
        for websocket, subscribed_types in self.clients.items():
            if event_type in subscribed_types:
                try:
                    await websocket.send_json(event_dict)
                except:
                    logger.error(traceback.format_exc())
                    deleters.append(websocket)
        for websocket in deleters:
            logger.error("Removing client registration after error on %s", websocket)
            del self.clients[websocket]

            

    async def register_client(self, websocket: Any, event_types: list[str]):
        if 'all_but_chunks' in event_types or 'all' in event_types:
            r_types = {
                str(AudioStartEvent),
                str(AudioStopEvent),
                str(AudioSpeechStartEvent),
                str(AudioSpeechStopEvent),
                str(AudioErrorEvent),
                str(TextEvent),
                str(DraftStartEvent),
                str(DraftEndEvent),
                str(DraftRevisionEvent),
                }
            if not "all_but_chunks" in event_types:
                r_types.add(str(AudioChunkEvent))
        else:
            r_types = event_types
        r_types = set(r_types)
        self.clients[websocket] = r_types
        logger.info(f"Client registered for events: {r_types}")

    async def unregister_client(self, websocket: Any):
        if websocket in self.clients:
            del self.clients[websocket]
            logger.info("Client unregistered")

    async def become_router(self):
        router = APIRouter()

        @router.websocket("/events")
        async def websocket_endpoint(websocket: WebSocket):
            await websocket.accept()
            logger.info("Client connected")
            try:
                # Wait for subscription message
                data = await websocket.receive_json()
                event_types = set(data.get("subscribe", []))
                logger.debug("Registering types %s", event_types)

                if not event_types:
                    await websocket.close(code=1003, reason="No event types specified")
                    return

                # Register client with shared event router
                await self.register_client(websocket, event_types)
                logger.info(f"Client subscribed to: {event_types}")

                # Keep connection alive until client disconnects
                while True:
                    await asyncio.sleep(1)

            except WebSocketDisconnect:
                logger.info("Client disconnected")
            except Exception as e:
                logger.error(f"Error in websocket handler: {e}", exc_info=True)
            finally:
                await self.unregister_client(websocket)

        @router.get("/health")
        async def health_check() -> dict[str, str]:
            """Basic health check endpoint.
            
            Returns:
                Simple status message indicating server is running
            """
            return {"status": "healthy"}

        @router.get("/status")
        async def server_status() -> dict[str, Any]:
            """Detailed server status endpoint.

            Returns:
                Dictionary with server status information including:
                - Pipeline running state
                - Connected client count
                - Model path
            """
            pipeline_running = self.server.pipeline is not None
            client_count = len(self.clients) 

            return {
                "status": "running",
                "pipeline_active": pipeline_running,
                "connected_clients": client_count,
                "pipeline_config": asdict(self.server.pipeline_config),
            }
        return router

