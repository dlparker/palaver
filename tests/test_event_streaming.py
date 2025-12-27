#!/usr/bin/env python
"""
tests/test_event_streaming.py
Integration test for event streaming router setup and websocket protocol.

Note: Full end-to-end event flow testing with async websockets is complex
with TestClient. Event routing logic is thoroughly tested in test_event_router.py.
These tests verify the router factory integration and websocket protocol basics.
"""

import pytest
from pathlib import Path
from unittest.mock import Mock

from fastapi import FastAPI
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from palaver.fastapi.routers.events import create_event_router
from palaver.fastapi.event_router import EventRouter
from palaver.stage_markers import Stage, stage


@stage(Stage.PROTOTYPE, track_coverage=True)
def test_event_router_factory_creates_working_endpoint():
    """Test that create_event_router() returns functional APIRouter."""
    # Create mock server with event router
    mock_server = Mock()
    mock_server.event_router = EventRouter()
    mock_server.model_path = Path("models/test.bin")
    mock_server.draft_dir = None
    mock_server.pipeline = None

    # Create router from factory
    router = create_event_router(mock_server)

    # Verify router has routes
    assert len(router.routes) > 0

    # Find websocket route
    websocket_routes = [r for r in router.routes if hasattr(r, 'path') and '/events' in r.path]
    assert len(websocket_routes) > 0, "Should have /events websocket route"


@stage(Stage.PROTOTYPE, track_coverage=True)
def test_websocket_accepts_connection():
    """Test websocket endpoint accepts connections."""
    mock_server = Mock()
    mock_server.event_router = EventRouter()
    mock_server.model_path = Path("models/test.bin")
    mock_server.draft_dir = None
    mock_server.pipeline = None

    # Create test app
    app = FastAPI()
    app.include_router(create_event_router(mock_server))

    client = TestClient(app)

    # Test connection accepted
    with client.websocket_connect("/events") as websocket:
        # Connection established
        assert websocket is not None

        # Send subscription (required protocol)
        websocket.send_json({"subscribe": ["all"]})

        # Connection should stay open
        # (In real use, events would be sent from pipeline)


@stage(Stage.PROTOTYPE, track_coverage=True)
def test_websocket_requires_subscription():
    """Test websocket closes if no subscription sent."""
    mock_server = Mock()
    mock_server.event_router = EventRouter()
    mock_server.model_path = Path("models/test.bin")
    mock_server.draft_dir = None
    mock_server.pipeline = None

    app = FastAPI()
    app.include_router(create_event_router(mock_server))

    client = TestClient(app)

    # Connect but don't send subscription
    with client.websocket_connect("/events") as websocket:
        # Send invalid subscription (empty event types)
        websocket.send_json({"subscribe": []})

        # Server should close connection
        # (The endpoint closes with code 1003 for no event types)
        with pytest.raises(WebSocketDisconnect):
            websocket.receive_json()


@stage(Stage.PROTOTYPE, track_coverage=True)
def test_event_router_integrates_with_server_context():
    """Test router uses shared event router from server."""
    # Create server with specific event router instance
    mock_server = Mock()
    event_router_instance = EventRouter()
    mock_server.event_router = event_router_instance
    mock_server.model_path = Path("models/test.bin")
    mock_server.draft_dir = None
    mock_server.pipeline = None

    # Create router
    router = create_event_router(mock_server)

    # Create app and test connection
    app = FastAPI()
    app.include_router(router)

    client = TestClient(app)

    with client.websocket_connect("/events") as websocket:
        websocket.send_json({"subscribe": ["TextEvent"]})

        # Verify client registered in shared event router
        assert len(event_router_instance.clients) == 1

    # After disconnect, client should be unregistered
    assert len(event_router_instance.clients) == 0


@stage(Stage.PROTOTYPE, track_coverage=True)
def test_multiple_websocket_clients():
    """Test multiple clients can connect simultaneously."""
    mock_server = Mock()
    mock_server.event_router = EventRouter()
    mock_server.model_path = Path("models/test.bin")
    mock_server.draft_dir = None
    mock_server.pipeline = None

    app = FastAPI()
    app.include_router(create_event_router(mock_server))

    client1 = TestClient(app)
    client2 = TestClient(app)

    # Connect two clients
    with client1.websocket_connect("/events") as ws1:
        ws1.send_json({"subscribe": ["all"]})

        with client2.websocket_connect("/events") as ws2:
            ws2.send_json({"subscribe": ["TextEvent"]})

            # Both clients registered
            assert len(mock_server.event_router.clients) == 2

        # After ws2 disconnects, only ws1 remains
        assert len(mock_server.event_router.clients) == 1

    # After both disconnect
    assert len(mock_server.event_router.clients) == 0
