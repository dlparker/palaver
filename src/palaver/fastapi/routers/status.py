"""Status router factory for health and status endpoints.

Provides factory function to create an APIRouter with basic status monitoring
endpoints for the EventNetServer.
"""
import logging
from typing import TYPE_CHECKING, Dict, Any

from fastapi import APIRouter

from palaver.stage_markers import Stage, stage

if TYPE_CHECKING:
    from palaver.fastapi.server import EventNetServer

logger = logging.getLogger("StatusRouter")


@stage(Stage.PROTOTYPE, track_coverage=True)
def create_status_router(server: "EventNetServer") -> APIRouter:
    """Create FastAPI router for status and health endpoints.

    Provides basic endpoints for monitoring server health and status.
    Demonstrates the router factory pattern for future extensions.

    Args:
        server: EventNetServer instance for accessing server state

    Returns:
        APIRouter configured with status endpoints
    """
    router = APIRouter()

    @router.get("/health")
    async def health_check() -> Dict[str, str]:
        """Basic health check endpoint.

        Returns:
            Simple status message indicating server is running
        """
        return {"status": "healthy"}

    @router.get("/status")
    async def server_status() -> Dict[str, Any]:
        """Detailed server status endpoint.

        Returns:
            Dictionary with server status information including:
            - Pipeline running state
            - Connected client count
            - Model path
        """
        pipeline_running = server.pipeline is not None
        client_count = len(server.event_router.clients) if server.event_router else 0

        return {
            "status": "running",
            "pipeline_active": pipeline_running,
            "connected_clients": client_count,
            "model_path": str(server.model_path),
            "draft_recording": server.draft_dir is not None,
        }

    return router
