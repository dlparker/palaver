#!/usr/bin/env python
"""
tests/test_revision_api.py
Unit tests for revision API router using FastAPI TestClient (no network)
"""

import pytest
import json
import tempfile
from pathlib import Path
from unittest.mock import Mock, AsyncMock
from datetime import datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

from palaver.fastapi.routers.revisions import create_revision_router
from palaver.scribe.recorders.sql_drafts import SQLDraftRecorder
from palaver.scribe.draft_events import Draft, TextMark
from palaver.scribe.text_events import TextEvent
from palaver.stage_markers import Stage, stage


@stage(Stage.PROTOTYPE, track_coverage=True)
@pytest.fixture
def mock_server_with_recorder():
    """Create a mock server with a real SQLDraftRecorder in temp directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        recorder = SQLDraftRecorder(Path(tmpdir))

        mock_server = Mock()
        mock_server.draft_recorder = recorder

        yield mock_server


@stage(Stage.PROTOTYPE, track_coverage=True)
@pytest.fixture
def mock_server_no_recorder():
    """Create a mock server without draft recorder."""
    mock_server = Mock()
    mock_server.draft_recorder = None

    yield mock_server


@stage(Stage.PROTOTYPE, track_coverage=True)
@pytest.fixture
async def sample_draft():
    """Create a sample Draft object for testing."""
    draft = Draft(
        draft_id="test-draft-abc123",
        timestamp=1735330000.0,
        start_text=TextMark(start=0, end=10, text="start note"),
        end_text=TextMark(start=50, end=60, text="end note"),
        full_text="start note this is a test draft end note",
        start_matched_events=[
            TextEvent(
                text="start note",
                audio_source_id="test_source",
                timestamp=1735330000.0,
                audio_start_time=1735330000.0,
                audio_end_time=1735330001.0
            )
        ],
        end_matched_events=[
            TextEvent(
                text="end note",
                audio_source_id="test_source",
                timestamp=1735330005.0,
                audio_start_time=1735330005.0,
                audio_end_time=1735330006.0
            )
        ]
    )
    return draft


@stage(Stage.PROTOTYPE, track_coverage=True)
@pytest.mark.asyncio
async def test_post_revision_success(mock_server_with_recorder, sample_draft):
    """Test POST /api/revisions successfully stores a revision."""
    # Store original draft first
    recorder = mock_server_with_recorder.draft_recorder
    recorder._current_draft = sample_draft
    recorder._current_dir = recorder._output_dir / "test-draft"
    recorder._current_dir.mkdir()
    await recorder._save_to_database()

    # Create test app with revision router
    app = FastAPI()
    app.include_router(create_revision_router(mock_server_with_recorder))

    # Create revision submission
    from dataclasses import asdict
    revised_draft_dict = asdict(sample_draft)
    revised_draft_dict['full_text'] = "start note this is an improved draft end note"
    revised_draft_dict['start_matched_events'] = [
        asdict(e) for e in sample_draft.start_matched_events
    ]
    revised_draft_dict['end_matched_events'] = [
        asdict(e) for e in sample_draft.end_matched_events
    ]

    submission = {
        "original_draft_id": "test-draft-abc123",
        "revised_draft": revised_draft_dict,
        "metadata": {
            "model": "multilang_whisper_large3_turbo.ggml",
            "source": "rescan_server",
            "source_uri": "http://192.168.100.214:8765/transcription/v1"
        }
    }

    # Test without network
    client = TestClient(app)
    response = client.post("/api/revisions", json=submission)

    assert response.status_code == 201
    data = response.json()

    assert "revision_id" in data
    assert data["original_draft_id"] == "test-draft-abc123"
    assert data["stored"] is True
    assert "created_at" in data


@stage(Stage.PROTOTYPE, track_coverage=True)
def test_post_revision_draft_not_found(mock_server_with_recorder):
    """Test POST /api/revisions returns 404 when original draft doesn't exist."""
    # Create test app with revision router
    app = FastAPI()
    app.include_router(create_revision_router(mock_server_with_recorder))

    submission = {
        "original_draft_id": "nonexistent-draft-id",
        "revised_draft": {"full_text": "test"},
        "metadata": {"model": "test_model"}
    }

    # Test without network
    client = TestClient(app)
    response = client.post("/api/revisions", json=submission)

    assert response.status_code == 404
    data = response.json()

    assert "detail" in data
    assert "original_draft_not_found" in str(data["detail"])


@stage(Stage.PROTOTYPE, track_coverage=True)
def test_post_revision_no_recorder(mock_server_no_recorder):
    """Test POST /api/revisions returns 503 when draft recording disabled."""
    # Create test app with revision router
    app = FastAPI()
    app.include_router(create_revision_router(mock_server_no_recorder))

    submission = {
        "original_draft_id": "test-draft-123",
        "revised_draft": {"full_text": "test"},
        "metadata": {"model": "test_model"}
    }

    # Test without network
    client = TestClient(app)
    response = client.post("/api/revisions", json=submission)

    assert response.status_code == 503
    data = response.json()

    assert "draft recording not enabled" in data["detail"].lower()


@stage(Stage.PROTOTYPE, track_coverage=True)
@pytest.mark.asyncio
async def test_get_revisions_success(mock_server_with_recorder, sample_draft):
    """Test GET /api/revisions/{draft_id} returns original draft and revisions."""
    # Store original draft
    recorder = mock_server_with_recorder.draft_recorder
    recorder._current_draft = sample_draft
    recorder._current_dir = recorder._output_dir / "test-draft"
    recorder._current_dir.mkdir()
    await recorder._save_to_database()

    # Store a revision
    from dataclasses import asdict
    revised_draft_dict = asdict(sample_draft)
    revised_draft_dict['full_text'] = "start note improved text end note"
    revised_draft_dict['start_matched_events'] = [
        asdict(e) for e in sample_draft.start_matched_events
    ]
    revised_draft_dict['end_matched_events'] = [
        asdict(e) for e in sample_draft.end_matched_events
    ]
    revised_draft_json = json.dumps(revised_draft_dict)

    metadata = {
        "model": "large3_turbo",
        "source": "rescan_server",
        "source_uri": "http://192.168.100.214:8765"
    }

    await recorder.store_revision(
        original_draft_id="test-draft-abc123",
        revised_draft_json=revised_draft_json,
        metadata=metadata
    )

    # Create test app
    app = FastAPI()
    app.include_router(create_revision_router(mock_server_with_recorder))

    # Test GET
    client = TestClient(app)
    response = client.get("/api/revisions/test-draft-abc123")

    assert response.status_code == 200
    data = response.json()

    assert data["draft_id"] == "test-draft-abc123"
    assert "original_draft" in data
    assert data["original_draft"]["draft_id"] == "test-draft-abc123"
    assert data["original_draft"]["full_text"] == sample_draft.full_text

    assert "revisions" in data
    assert len(data["revisions"]) == 1

    revision = data["revisions"][0]
    assert "revision_id" in revision
    assert revision["model"] == "large3_turbo"
    assert revision["source"] == "rescan_server"
    assert revision["source_uri"] == "http://192.168.100.214:8765"
    assert revision["full_text"] == "start note improved text end note"
    assert "text_preview" in revision


@stage(Stage.PROTOTYPE, track_coverage=True)
@pytest.mark.asyncio
async def test_get_revisions_empty(mock_server_with_recorder, sample_draft):
    """Test GET /api/revisions/{draft_id} returns empty list when no revisions exist."""
    # Store original draft
    recorder = mock_server_with_recorder.draft_recorder
    recorder._current_draft = sample_draft
    recorder._current_dir = recorder._output_dir / "test-draft"
    recorder._current_dir.mkdir()
    await recorder._save_to_database()

    # Create test app
    app = FastAPI()
    app.include_router(create_revision_router(mock_server_with_recorder))

    # Test GET
    client = TestClient(app)
    response = client.get("/api/revisions/test-draft-abc123")

    assert response.status_code == 200
    data = response.json()

    assert data["draft_id"] == "test-draft-abc123"
    assert "revisions" in data
    assert data["revisions"] == []


@stage(Stage.PROTOTYPE, track_coverage=True)
def test_get_revisions_draft_not_found(mock_server_with_recorder):
    """Test GET /api/revisions/{draft_id} returns 404 when draft doesn't exist."""
    # Create test app
    app = FastAPI()
    app.include_router(create_revision_router(mock_server_with_recorder))

    # Test GET
    client = TestClient(app)
    response = client.get("/api/revisions/nonexistent-draft-id")

    assert response.status_code == 404
    data = response.json()

    assert "detail" in data
    assert "draft_not_found" in str(data["detail"])


@stage(Stage.PROTOTYPE, track_coverage=True)
def test_get_revisions_no_recorder(mock_server_no_recorder):
    """Test GET /api/revisions/{draft_id} returns 503 when draft recording disabled."""
    # Create test app
    app = FastAPI()
    app.include_router(create_revision_router(mock_server_no_recorder))

    # Test GET
    client = TestClient(app)
    response = client.get("/api/revisions/test-draft-123")

    assert response.status_code == 503
    data = response.json()

    assert "draft recording not enabled" in data["detail"].lower()


@stage(Stage.PROTOTYPE, track_coverage=True)
@pytest.mark.asyncio
async def test_get_revisions_multiple_ordered(mock_server_with_recorder, sample_draft):
    """Test GET returns multiple revisions ordered by created_at descending."""
    # Store original draft
    recorder = mock_server_with_recorder.draft_recorder
    recorder._current_draft = sample_draft
    recorder._current_dir = recorder._output_dir / "test-draft"
    recorder._current_dir.mkdir()
    await recorder._save_to_database()

    # Store multiple revisions
    from dataclasses import asdict
    import asyncio

    for i in range(3):
        revised_draft_dict = asdict(sample_draft)
        revised_draft_dict['full_text'] = f"revision {i+1} text"
        revised_draft_dict['start_matched_events'] = [
            asdict(e) for e in sample_draft.start_matched_events
        ]
        revised_draft_dict['end_matched_events'] = [
            asdict(e) for e in sample_draft.end_matched_events
        ]
        revised_draft_json = json.dumps(revised_draft_dict)

        metadata = {"model": f"model_v{i+1}"}
        await recorder.store_revision(
            original_draft_id="test-draft-abc123",
            revised_draft_json=revised_draft_json,
            metadata=metadata
        )
        await asyncio.sleep(0.01)  # Ensure different timestamps

    # Create test app
    app = FastAPI()
    app.include_router(create_revision_router(mock_server_with_recorder))

    # Test GET
    client = TestClient(app)
    response = client.get("/api/revisions/test-draft-abc123")

    assert response.status_code == 200
    data = response.json()

    assert len(data["revisions"]) == 3

    # Verify ordering (newest first)
    assert data["revisions"][0]["full_text"] == "revision 3 text"
    assert data["revisions"][1]["full_text"] == "revision 2 text"
    assert data["revisions"][2]["full_text"] == "revision 1 text"
