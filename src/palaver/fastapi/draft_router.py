import asyncio
import logging
from typing import Any
import traceback
import json

import websockets

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from palaver.scribe.draft_events import DraftEvent, DraftStartEvent, DraftEndEvent, DraftRevisionEvent
from palaver.utils.serializers import draft_from_dict, serialize_value


logger = logging.getLogger("DraftRouter")

class DraftRouter:
    
    def __init__(self, server):
        self.server = server
        self.ws_base_url = None
        self.audio_url = None

    async def become_router(self):
        router = APIRouter()
        self.ws_base_url = self.server.get_ws_base_url()
        self.audio_url = self.server.get_audio_url() # non None only if mode is not normal

        @router.websocket("/new_draft")
        async def submit_draft(fapi_ws: WebSocket):
            await fapi_ws.accept()
            data = await fapi_ws.receive_json()
            draft = draft_from_dict(data)
            logger.info("Received new draft %s with parent %s on websocket",
                        draft.draft_id, draft.parent_draft_id)
            await self.server.pipeline.draft_maker.import_draft(draft)
            logger.info("Posted new draft to draft_maker")
            logger.debug("new draft text %s", draft.full_text)
            await fapi_ws.send_json({'code': 'success'})
        
        return router            

    async def register_rescanner(self):
        if self.audio_url is None:
            raise Exception('need audio url')
        url = self.audio_url + "/register_rescanner"
        async with websockets.connect(url) as websocket:
            await websocket.send(json.dumps({'url': self.ws_base_url}))
            data  = await websocket.recv()
        
    async def send_new_draft(self, draft):
        if self.audio_url is None:
            raise Exception('need audio url')
        url = self.audio_url + "/new_draft"
        data = serialize_value(draft)
        jdata = json.dumps(data)
        async with websockets.connect(url) as websocket:
            await websocket.send(jdata)
            data  = await websocket.recv()
            await asyncio.sleep(0.01)
            logger.info("New draft push result %s ", data)

        
        
