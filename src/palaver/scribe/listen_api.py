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

from palaver.scribe.audio_events import AudioEvent, AudioEventListener, AudioChunkEvent
   
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


class AudioRingBuffer:
    def __init__(self, max_seconds: float):
        """
        Initialize the ring buffer.
        
        :param max_seconds: Maximum seconds of audio history to retain.
        """
        if max_seconds <= 0:
            raise ValueError("max_seconds must be positive")
        self.max_seconds = max_seconds
        self.buffer: deque[AudioChunkEvent] = deque()

    def add(self, event: AudioChunkEvent) -> None:
        """
        Add a new AudioChunkEvent to the buffer and prune old entries.
        """
        self.buffer.append(event)
        self._prune()

    def _prune(self, now: float = None) -> None:
        """
        Remove events entirely older than the retention window.
        
        :param now: Optional current time (defaults to time.time()).
        """
        if now is None:
            now = time.time()
        while self.buffer and (self.buffer[0].timestamp + self.buffer[0].duration < now - self.max_seconds):
            self.buffer.popleft()

    def get_all(self) -> List[AudioChunkEvent]:
        """Return a list of all current events in the buffer (oldest to newest)."""
        return list(self.buffer)

    def get_recent(self, min_seconds: float = None) -> List[AudioChunkEvent]:
        """
        Return the most recent events covering at least min_seconds of audio (or all if None).
        Starts from the newest and works backward.
        
        :param min_seconds: Minimum seconds to cover (default: None, returns all).
        :return: List of events (oldest to newest within the subset).
        """
        if min_seconds is None:
            return self.get_all()
        
        if min_seconds <= 0:
            return []
        
        subset = []
        total_dur = 0.0
        for event in reversed(self.buffer):
            subset.append(event)
            total_dur += event.duration
            if total_dur >= min_seconds:
                break
        return subset[::-1]  # Reverse to oldest-first order

    def get_concatenated_samples(self, min_seconds: float = None) -> np.ndarray:
        """
        Optional: Concatenate the data arrays from the recent events into a single np.ndarray.
        Assumes all events have compatible shapes (same channels, dtype, etc.).
        
        :param min_seconds: Minimum seconds to cover (default: None, uses all).
        :return: Concatenated float32 array, shape (total_samples, channels).
        """
        events = self.get_recent(min_seconds)
        if not events:
            return np.empty((0, 0), dtype=np.float32)
        return np.concatenate([ev.data for ev in events], axis=0)

    @property
    def total_duration(self) -> float:
        """Total duration of audio in the buffer."""
        return sum(ev.duration for ev in self.buffer)
