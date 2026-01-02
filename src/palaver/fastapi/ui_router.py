import logging
from pathlib import Path
from datetime import datetime
import time

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

logger = logging.getLogger("UIRouter")


def format_timestamp(ts):
    """Format Unix timestamp as readable datetime."""
    if ts is None:
        return None
    return datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S')


def time_ago(ts):
    """Format Unix timestamp as relative time (e.g., '2 seconds ago')."""
    if ts is None:
        return None
    elapsed = time.time() - ts
    if elapsed < 60:
        return f"{int(elapsed)}s ago"
    elif elapsed < 3600:
        return f"{int(elapsed / 60)}m ago"
    elif elapsed < 86400:
        return f"{int(elapsed / 3600)}h ago"
    else:
        return f"{int(elapsed / 86400)}d ago"


class UIRouter:
    """Router for HTML UI pages using HTMX, Tailwind CSS, and daisyUI."""

    def __init__(self, server):
        self.server = server
        # Set up Jinja2 templates
        templates_dir = Path(__file__).parent / "templates"
        self.templates = Jinja2Templates(directory=str(templates_dir))
        # Add custom filters
        self.templates.env.filters['format_timestamp'] = format_timestamp
        self.templates.env.filters['time_ago'] = time_ago

    async def become_router(self):
        """Create and configure the UI router."""
        router = APIRouter()

        @router.get("/", response_class=HTMLResponse)
        async def home(request: Request):
            """Render the home page."""
            return self.templates.TemplateResponse(
                "home.html",
                {"request": request}
            )

        @router.get("/ui/status-partial", response_class=HTMLResponse)
        async def status_partial(request: Request):
            """Return partial HTML for status display (for HTMX)."""
            # Get status from the server's index_router
            status_data = await self._get_status_data()
            return self.templates.TemplateResponse(
                "status_partial.html",
                {"request": request, "status": status_data}
            )

        return router

    async def _get_status_data(self):
        """Fetch current status from the server."""
        # Access the index_router to get status info
        index_router = self.server.index_router
        pipeline = self.server.pipeline

        return {
            "status": "running",
            "pipeline_active": pipeline is not None,
            "event_clients": len(self.server.event_router.active_connections),
            "url": index_router.url_base,
            "ws_url": index_router.ws_url_base,
            "rescanner_url": index_router.rescanner,
            "rescanner_available": index_router.is_rescanner_available(),
            "last_rescanner_ping": index_router.last_rescanner_registration
        }
