import asyncio
import logging
from typing import Any, Optional
import traceback
import json
import time

import websockets

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query, Path, HTTPException

from palaver_shared.draft_events import DraftEvent, DraftStartEvent, DraftEndEvent
from palaver_shared.serializers import draft_from_dict, serialize_value, draft_record_to_dict
from palaver.utils.time_utils import parse_timestamp


logger = logging.getLogger("DraftRouter")

class DraftRouter:

    def __init__(self, server):
        self.server = server
        self.ws_base_url = None
        self.audio_url = None
        self.last_rescanner_registration: Optional[float] = None

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

        @router.get("/drafts")
        async def list_drafts(
            since: Optional[str] = Query(None, description="Unix timestamp or ISO datetime string"),
            limit: int = Query(100, ge=1, le=1000, description="Max results (1-1000)"),
            offset: int = Query(0, ge=0, description="Results to skip"),
            order: str = Query("desc", pattern="^(asc|desc)$", description="Sort by timestamp"),
            summary: bool = Query(False,  description="Return draft summary only")
        ):
            """List drafts with optional time filtering and pagination.

            Query parameters:
            - since: Filter drafts from this time (Unix timestamp or ISO datetime)
            - limit: Maximum results (default 100, max 1000)
            - offset: Results to skip (default 0)
            - order: Sort order 'asc' or 'desc' by timestamp (default 'desc')
            - summary: return only id and timestamp
            """
            try:
                # Get drafts from recorder
                if since is not None:
                    try:
                        since_ts = parse_timestamp(since)
                    except ValueError as e:
                        raise HTTPException(status_code=400, detail=str(e))

                    drafts, total = self.server.draft_recorder.get_drafts_since(
                        since_timestamp=since_ts,
                        limit=limit,
                        offset=offset,
                        order=order
                    )
                else:
                    drafts, total = self.server.draft_recorder.get_all_drafts_paginated(
                        limit=limit,
                        offset=offset,
                        order=order
                    )

                # Serialize drafts
                if summary:
                    def summary(d):
                        return {'draft_id': d.draft_id,
                         'timestamp': d.timestamp,
                         'parent_draft_id': d.parent_draft_id} 
                    draft_dicts = [summary(d) for d in drafts]
                else:
                    draft_dicts = [draft_record_to_dict(d) for d in drafts]
                    

                return {
                    "drafts": draft_dicts,
                    "total": total,
                    "limit": limit,
                    "offset": offset,
                    "has_more": (offset + len(drafts)) < total
                }

            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Error listing drafts: {e}", exc_info=True)
                raise HTTPException(status_code=500, detail="Database query failed")

        @router.get("/drafts/{draft_id}")
        async def get_draft(
            draft_id: str = Path(..., description="Draft UUID to retrieve"),
            include_parent: bool = Query(False, description="Include parent draft"),
            include_children: bool = Query(False, description="Include child drafts")
        ):
            """Get a specific draft by UUID with optional parent/children.

            Path parameters:
            - draft_id: UUID of the draft

            Query parameters:
            - include_parent: Include parent draft (default false)
            - include_children: Include child drafts (default false)
            """
            try:
                # Get draft with family if requested
                if include_parent or include_children:
                    draft, parent, children = self.server.draft_recorder.get_draft_with_family(
                        draft_id
                    )
                else:
                    draft = self.server.draft_recorder.get_draft_by_uuid(draft_id)
                    parent = None
                    children = []

                if draft is None:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Draft with ID '{draft_id}' not found"
                    )

                # Build response
                response = {
                    "draft": draft_record_to_dict(draft)
                }

                if include_parent:
                    response["parent"] = draft_record_to_dict(parent)

                if include_children:
                    response["children"] = [draft_record_to_dict(c) for c in children]

                return response

            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Error getting draft {draft_id}: {e}", exc_info=True)
                raise HTTPException(status_code=500, detail="Database query failed")

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

        
        
