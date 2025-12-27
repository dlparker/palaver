"""FastAPI server components for Palaver event streaming.

This package provides modular server components for streaming audio pipeline
events via FastAPI websockets.
"""

from palaver.fastapi.event_router import EventRouter
from palaver.fastapi.server import EventNetServer

__all__ = ["EventRouter", "EventNetServer"]
