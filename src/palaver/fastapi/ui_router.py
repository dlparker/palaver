import logging
from pathlib import Path
from datetime import datetime
import time
import asyncio
from collections import deque
from typing import Optional

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
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
        # Event buffer for polling (stores recent events with sequence numbers)
        self.event_buffer = deque(maxlen=100)  # Keep last 100 events
        self.event_sequence = 0

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

        @router.get("/ui/events-poll", response_class=HTMLResponse)
        async def events_poll(request: Request, since: int = 0):
            """Polling endpoint for event updates. Returns HTML for events since given sequence number."""
            new_events = [e for e in self.event_buffer if e['seq'] > since]

            if not new_events:
                # Return empty response with current sequence number
                return HTMLResponse(
                    content="",
                    headers={"X-Event-Sequence": str(self.event_sequence)}
                )

            # Render events as HTML
            html_parts = []
            for event_data in new_events:
                html = self._format_event_html(event_data['event'])
                html_parts.append(html)

            return HTMLResponse(
                content="\n".join(html_parts),
                headers={"X-Event-Sequence": str(self.event_sequence)}
            )

        @router.get("/ui/recording-button", response_class=HTMLResponse)
        async def recording_button(request: Request):
            """Return recording button HTML based on current state."""
            audio_listener = self.server.audio_listener
            is_streaming = audio_listener.is_streaming() if hasattr(audio_listener, 'is_streaming') else False
            is_paused = audio_listener.is_paused() if hasattr(audio_listener, 'is_paused') else False
            recording = is_streaming and not is_paused

            return self.templates.TemplateResponse(
                "recording_button.html",
                {"request": request, "recording": recording}
            )

        @router.post("/ui/recording/pause", response_class=HTMLResponse)
        async def pause_recording(request: Request):
            """Pause audio recording."""
            audio_listener = self.server.audio_listener
            if not hasattr(audio_listener, 'pause_streaming'):
                raise HTTPException(status_code=400, detail="Pause not supported by this audio listener")

            if not audio_listener.is_streaming():
                raise HTTPException(status_code=400, detail="Recording is not active")

            await audio_listener.pause_streaming()

            # Return updated button
            return self.templates.TemplateResponse(
                "recording_button.html",
                {"request": request, "recording": False}
            )

        @router.post("/ui/recording/resume", response_class=HTMLResponse)
        async def resume_recording(request: Request):
            """Resume audio recording."""
            audio_listener = self.server.audio_listener
            if not hasattr(audio_listener, 'resume_streaming'):
                raise HTTPException(status_code=400, detail="Resume not supported by this audio listener")

            if not audio_listener.is_streaming():
                raise HTTPException(status_code=400, detail="Recording is not active")

            await audio_listener.resume_streaming()

            # Return updated button
            return self.templates.TemplateResponse(
                "recording_button.html",
                {"request": request, "recording": True}
            )

        @router.get("/ui/recording/status")
        async def get_recording_status():
            """Get current recording status."""
            audio_listener = self.server.audio_listener
            is_streaming = audio_listener.is_streaming() if hasattr(audio_listener, 'is_streaming') else False
            is_paused = audio_listener.is_paused() if hasattr(audio_listener, 'is_paused') else False

            return JSONResponse({
                "streaming": is_streaming,
                "paused": is_paused,
                "recording": is_streaming and not is_paused
            })

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

    async def broadcast_event(self, event):
        """Add event to buffer for polling clients."""
        # Only store events we care about
        if not isinstance(event, (AudioSpeechStartEvent, AudioSpeechStopEvent,
                                 TextEvent, DraftStartEvent, DraftEndEvent)):
            return

        # Increment sequence and add to buffer
        self.event_sequence += 1
        self.event_buffer.append({
            'seq': self.event_sequence,
            'event': event,
            'timestamp': time.time()
        })
        logger.debug(f"Added {event.__class__.__name__} to event buffer (seq={self.event_sequence})")

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
