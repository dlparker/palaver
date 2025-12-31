import asyncio
import logging
from typing import Any
from dataclasses import asdict
import traceback
import socket

import numpy as np
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

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
from palaver.fastapi.ws_managers import PipelineEventManager, DraftSubmissionManager


logger = logging.getLogger("EventSender")

class EventSender:
    def __init__(self, my_port: int, server):
        self.my_port = my_port
        self.server = server
        self.hostname = socket.gethostname()
        self.ip_address = socket.gethostbyname(self.hostname)
        self.uri = f"http://{self.hostname}:{self.my_port}/routes"

        self.event_manager = PipelineEventManager()
        self.event_manager.uri = self.uri  # for author_uri stamping

        self.draft_manager = DraftSubmissionManager()

    async def send_event(self, event: AudioEvent | TextEvent | DraftEvent):
        if event.author_uri is None:
            event.author_uri = self.uri
        await self.event_manager.send_to_subscribers(event)

    def expand_event_types(self, in_types: list):
        main_types = {
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
        valid = set(main_types)
        valid.add(str(AudioChunkEvent))

        if 'all_but_chunks' in in_types or 'all' in in_types:
            r_types = set(main_types)
            if not "all_but_chunks" in event_types:
                r_types.add(str(AudioChunkEvent))
        else:
            for in_type in in_types:
                if in_type not in valid:
                    raise Exception(f'invalid type requested {in_type}')
            r_types = in_types
        r_types = set(r_types)
        return r_types

    async def become_router(self):
        router = APIRouter()

        @router.websocket("/events")
        async def pipeline_events(websocket: WebSocket):
            await websocket.accept()
            try:
                data = await websocket.receive_json()
                event_types = set(data.get("subscribe", []))
                if not event_types:
                    await websocket.close(code=1003, reason="No event types specified")
                    return

                event_types = self.expand_event_types(event_types)
                await self.event_manager.connect(websocket, event_types)

                # Keep alive
                while True:
                    await asyncio.sleep(1)
            except WebSocketDisconnect:
                self.event_manager.disconnect(websocket)
            except Exception as e:
                logger.error(f"Error in /events: {e}", exc_info=True)
                self.event_manager.disconnect(websocket)

        @router.websocket("/new_draft")
        async def submit_draft(websocket: WebSocket):
            await websocket.accept()
            # You'll inject your actual processing logic here
            await self.draft_manager.handle_client(
                websocket,
                draft_processor_callback=self.server.handle_incoming_draft  # define this method
            )
        return router            
