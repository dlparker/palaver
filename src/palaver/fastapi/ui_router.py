import logging
from pathlib import Path
from datetime import datetime
import time
import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from palaver.scribe.audio_events import AudioSpeechStartEvent, AudioSpeechStopEvent
from palaver.scribe.text_events import TextEvent
from palaver.scribe.draft_events import DraftStartEvent, DraftEndEvent

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
        # Event queues for SSE clients
        self.event_queues = []

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

        @router.get("/events", response_class=HTMLResponse)
        async def events_page(request: Request):
            """Render the events feed page."""
            return self.templates.TemplateResponse(
                "events.html",
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

        @router.get("/ui/events-stream")
        async def events_stream(request: Request):
            """SSE endpoint for real-time event updates."""
            return StreamingResponse(
                self._event_generator(request),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                }
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

    async def _event_generator(self, request: Request):
        """Generate SSE events for a client connection."""
        # Create a queue for this client
        queue = asyncio.Queue()
        self.event_queues.append(queue)

        try:
            while True:
                # Check if client disconnected
                if await request.is_disconnected():
                    break

                # Wait for next event (with timeout to check disconnect)
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=1.0)
                    html = self._format_event_html(event)
                    # SSE requires single line or properly formatted multi-line data
                    # Replace newlines with space to ensure single-line HTML
                    html_single_line = html.replace('\n', ' ').replace('\r', '')
                    logger.debug(f"Yielding SSE event: {html_single_line[:100]}...")
                    yield f"data: {html_single_line}\n\n"
                except asyncio.TimeoutError:
                    # Send keepalive
                    yield ": keepalive\n\n"
        finally:
            # Clean up when client disconnects
            self.event_queues.remove(queue)

    async def broadcast_event(self, event):
        """Broadcast event to all SSE clients."""
        # Only broadcast events we care about
        if not isinstance(event, (AudioSpeechStartEvent, AudioSpeechStopEvent,
                                 TextEvent, DraftStartEvent, DraftEndEvent)):
            return

        logger.info(f"Broadcasting {event.__class__.__name__} to {len(self.event_queues)} SSE clients")

        # Send to all connected SSE clients
        for queue in self.event_queues:
            try:
                await queue.put(event)
            except Exception:
                logger.warning("Failed to queue event for SSE client", exc_info=True)

    def _format_event_html(self, event):
        """Format an event as HTML fragment."""
        timestamp = datetime.now().strftime('%H:%M:%S')

        if isinstance(event, AudioSpeechStartEvent):
            return self.templates.get_template("event_item.html").render(
                event_type="Speech Start",
                event_class="badge-info",  # Cyan instead of green
                timestamp=timestamp,
                details=None
            )
        elif isinstance(event, AudioSpeechStopEvent):
            return self.templates.get_template("event_item.html").render(
                event_type="Speech Stop",
                event_class="badge-error",  # Red
                timestamp=timestamp,
                details=None
            )
        elif isinstance(event, TextEvent):
            return self.templates.get_template("event_item.html").render(
                event_type="Text",
                event_class="badge-accent",  # Blue-cyan
                timestamp=timestamp,
                details=event.text
            )
        elif isinstance(event, DraftStartEvent):
            return self.templates.get_template("event_item.html").render(
                event_type="Draft Start",
                event_class="badge-primary",  # Purple
                timestamp=timestamp,
                details=f"Start: {event.draft.start_text}"
            )
        elif isinstance(event, DraftEndEvent):
            return self.templates.get_template("event_item.html").render(
                event_type="Draft End",
                event_class="badge-secondary",  # Orange/amber
                timestamp=timestamp,
                details=f"Text: {event.draft.full_text[:100]}..."
            )
        else:
            return ""
