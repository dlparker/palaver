"""Revision router factory for draft revision submission and querying.

Provides REST API endpoints for rescan servers to submit improved draft
transcriptions and for clients to query revisions for a given draft.
"""
import json
import logging
from typing import TYPE_CHECKING, Optional
from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from palaver.stage_markers import Stage, stage

if TYPE_CHECKING:
    from palaver.fastapi.server import EventNetServer

logger = logging.getLogger("RevisionRouter")


class RevisionSubmission(BaseModel):
    """Request body for POST /api/revisions."""
    original_draft_id: str
    revised_draft: dict  # Draft serialized as dict
    metadata: dict  # Model, source, source_uri, timestamp, etc.


class RevisionResponse(BaseModel):
    """Response for POST /api/revisions."""
    revision_id: str
    original_draft_id: str
    stored: bool
    created_at: str


class RevisionInfo(BaseModel):
    """Information about a single revision."""
    revision_id: str
    created_at: str
    model: Optional[str] = None
    source: Optional[str] = None
    source_uri: Optional[str] = None
    text_preview: str
    full_text: str


class RevisionsQueryResponse(BaseModel):
    """Response for GET /api/revisions/{draft_id}."""
    draft_id: str
    original_draft: dict
    revisions: list[RevisionInfo]


@stage(Stage.PROTOTYPE, track_coverage=True)
def create_revision_router(server: "EventNetServer") -> APIRouter:
    """Create FastAPI router for revision submission and querying.

    Provides REST API endpoints for draft revisions:
    - POST /api/revisions - Submit a revision from rescan server
    - GET /api/revisions/{draft_id} - Query all revisions for a draft

    Args:
        server: EventNetServer instance with draft_recorder

    Returns:
        APIRouter configured with revision endpoints
    """
    router = APIRouter(prefix="/api/revisions", tags=["revisions"])

    @router.post("", response_model=RevisionResponse, status_code=201)
    async def submit_revision(submission: RevisionSubmission):
        """Accept a draft revision from rescan server.

        Args:
            submission: RevisionSubmission with original_draft_id, revised_draft, metadata

        Returns:
            RevisionResponse with revision_id and storage confirmation

        Raises:
            HTTPException 404: Original draft not found
            HTTPException 500: Database error during storage
        """
        if not server.draft_recorder:
            raise HTTPException(
                status_code=503,
                detail="Draft recording not enabled on this server"
            )

        try:
            # Serialize revised_draft to JSON
            revised_draft_json = json.dumps(submission.revised_draft)

            # Store revision
            revision_id = await server.draft_recorder.store_revision(
                original_draft_id=submission.original_draft_id,
                revised_draft_json=revised_draft_json,
                metadata=submission.metadata
            )

            return RevisionResponse(
                revision_id=revision_id,
                original_draft_id=submission.original_draft_id,
                stored=True,
                created_at=datetime.now().isoformat()
            )

    @router.get("/{draft_id}", response_model=RevisionsQueryResponse)
    async def get_revisions(draft_id: str):
        """Get all revisions for a draft.

        Args:
            draft_id: UUID of the original draft

        Returns:
            RevisionsQueryResponse with original draft and list of revisions

        Raises:
            HTTPException 404: Draft not found
        """
        if not server.draft_recorder:
            raise HTTPException(
                status_code=503,
                detail="Draft recording not enabled on this server"
            )

        # Get original draft
        original_draft = server.draft_recorder.get_draft_by_uuid(draft_id)
        if not original_draft:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": "draft_not_found",
                    "draft_id": draft_id,
                    "message": "Draft not found in database"
                }
            )

        # Get revisions
        revision_records = await server.draft_recorder.get_revisions(draft_id)

        # Convert to response format
        revisions = []
        for record in revision_records:
            # Parse the revised draft JSON to extract text
            revised_draft = json.loads(record.revised_draft_json)
            full_text = revised_draft.get("full_text", "")
            text_preview = full_text[:100] + "..." if len(full_text) > 100 else full_text

            revisions.append(RevisionInfo(
                revision_id=record.revision_id,
                created_at=record.created_at.isoformat(),
                model=record.model,
                source=record.source,
                source_uri=record.source_uri,
                text_preview=text_preview,
                full_text=full_text
            ))

        # Parse original draft properties
        original_draft_dict = json.loads(original_draft.properties_json)
        original_draft_info = {
            "draft_id": original_draft.draft_id,
            "full_text": original_draft.full_text,
            "created_at": original_draft.created_at.isoformat(),
            "model": original_draft_dict.get("model", "unknown")
        }

        return RevisionsQueryResponse(
            draft_id=draft_id,
            original_draft=original_draft_info,
            revisions=revisions
        )

    return router
