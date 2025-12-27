#!/usr/bin/env python
"""
tests/test_status_router.py
Unit tests for status router using FastAPI TestClient (no network)
"""

import pytest
from pathlib import Path
from unittest.mock import Mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from palaver.fastapi.routers.status import create_status_router
from palaver.fastapi.event_router import EventRouter
from palaver.stage_markers import Stage, stage


@stage(Stage.PROTOTYPE, track_coverage=True)
def test_health_endpoint():
    """Test /health endpoint returns healthy status."""
    # Create minimal mock server with required attributes
    mock_server = Mock()
    mock_server.model_path = Path("models/ggml-base.en.bin")
    mock_server.draft_dir = None
    mock_server.pipeline = None
    mock_server.event_router = EventRouter()

    # Create test app with status router
    app = FastAPI()
    app.include_router(create_status_router(mock_server))

    # Test without network using TestClient
    client = TestClient(app)
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


@stage(Stage.PROTOTYPE, track_coverage=True)
def test_status_endpoint_pipeline_not_running():
    """Test /status endpoint when pipeline is not running."""
    # Create minimal mock server
    mock_server = Mock()
    mock_server.model_path = Path("models/ggml-base.en.bin")
    mock_server.draft_dir = None
    mock_server.pipeline = None  # Pipeline not running
    mock_server.event_router = EventRouter()

    # Create test app with status router
    app = FastAPI()
    app.include_router(create_status_router(mock_server))

    # Test without network
    client = TestClient(app)
    response = client.get("/status")

    assert response.status_code == 200
    data = response.json()

    assert data["status"] == "running"
    assert data["pipeline_active"] is False
    assert data["connected_clients"] == 0
    assert data["model_path"] == "models/ggml-base.en.bin"
    assert data["draft_recording"] is False


@stage(Stage.PROTOTYPE, track_coverage=True)
def test_status_endpoint_pipeline_running():
    """Test /status endpoint when pipeline is running."""
    # Create mock server with running pipeline
    mock_pipeline = Mock()
    mock_server = Mock()
    mock_server.model_path = Path("models/ggml-base.en.bin")
    mock_server.draft_dir = Path("/tmp/drafts")
    mock_server.pipeline = mock_pipeline  # Pipeline running
    mock_server.event_router = EventRouter()

    # Create test app with status router
    app = FastAPI()
    app.include_router(create_status_router(mock_server))

    # Test without network
    client = TestClient(app)
    response = client.get("/status")

    assert response.status_code == 200
    data = response.json()

    assert data["status"] == "running"
    assert data["pipeline_active"] is True
    assert data["connected_clients"] == 0
    assert data["model_path"] == "models/ggml-base.en.bin"
    assert data["draft_recording"] is True


@stage(Stage.PROTOTYPE, track_coverage=True)
@pytest.mark.asyncio
async def test_status_endpoint_with_connected_clients():
    """Test /status endpoint shows connected client count."""
    # Create mock server with clients
    mock_server = Mock()
    mock_server.model_path = Path("models/ggml-base.en.bin")
    mock_server.draft_dir = None
    mock_server.pipeline = Mock()  # Pipeline running

    # Create event router with mock clients
    event_router = EventRouter()
    mock_ws1 = Mock()
    mock_ws2 = Mock()
    mock_ws3 = Mock()

    # Register clients
    await event_router.register_client(mock_ws1, {"all"})
    await event_router.register_client(mock_ws2, {"TextEvent"})
    await event_router.register_client(mock_ws3, {"AudioChunkEvent"})

    mock_server.event_router = event_router

    # Create test app with status router
    app = FastAPI()
    app.include_router(create_status_router(mock_server))

    # Test without network
    client = TestClient(app)
    response = client.get("/status")

    assert response.status_code == 200
    data = response.json()

    assert data["connected_clients"] == 3


@stage(Stage.PROTOTYPE, track_coverage=True)
def test_status_router_does_not_interfere_with_other_routes():
    """Test that status router can coexist with other routes."""
    # Create minimal mock server
    mock_server = Mock()
    mock_server.model_path = Path("models/test.bin")
    mock_server.draft_dir = None
    mock_server.pipeline = None
    mock_server.event_router = EventRouter()

    # Create app with status router and another route
    app = FastAPI()
    app.include_router(create_status_router(mock_server))

    @app.get("/test")
    def test_route():
        return {"test": "value"}

    # Test both routes work
    client = TestClient(app)

    health_response = client.get("/health")
    assert health_response.status_code == 200

    status_response = client.get("/status")
    assert status_response.status_code == 200

    test_response = client.get("/test")
    assert test_response.status_code == 200
    assert test_response.json() == {"test": "value"}
