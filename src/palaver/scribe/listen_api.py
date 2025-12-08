from typing import Protocol, Any, Optional, ClassVar, List
from enum import Enum
import socket
from datetime import datetime
import time
import uuid
from dataclasses import dataclass, field
from collections import deque
import numpy as np
from eventemitter import AsyncIOEventEmitter

from palaver.scribe.audio_events import AudioEvent, AudioEventListener

class Listener(Protocol):

    def add_event_listener(self, e_listener: AudioEventListener) -> None: ...

    async def emit_event(self, event: AudioEvent) -> None: ...

    async def start_recording(self) -> None: ...

    async def stop_recording(self) -> None: ...


class ListenerCCSMixin:

    def __init__(self, chunk_duration) -> None:
        self.chunk_duration = chunk_duration
        self.emitter = AsyncIOEventEmitter()

    def add_event_listener(self, e_listener: AudioEventListener) -> None:
        self.emitter.on(AudioEvent, e_listener.on_event)

    async def emit_event(self, event: AudioEvent) -> None:
        await self.emitter.emit(AudioEvent, event)

def create_source_id(source_type: str, start_datetime: datetime, port: int) -> str:
    """
    Creates a source_id in URI form: ase://{local_ip}:{port}/palaver/audio_source/{source_type}/{start_datetime}
    
    - source_type: The type of the source (string).
    - start_datetime: The start datetime (datetime object), converted to ISO-like format without colons (e.g., 2025-12-08T123456).
    - port: The port number (integer).
    
    The local IPv4 address is determined automatically.
    """
    # Get local IPv4 address
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
    except Exception:
        local_ip = "127.0.0.1"  # Fallback to localhost if unable to determine IP
    finally:
        s.close()
    
    # Convert datetime to ISO-like format without colons
    dt_str = start_datetime.strftime("%Y-%m-%dT%H%M%S")
    
    # Build the path
    path = f"/palaver/audio_source/{source_type}/{dt_str}"
    
    # Build the full URI
    uri = f"ase://{local_ip}:{port}{path}"
    
    return uri    


