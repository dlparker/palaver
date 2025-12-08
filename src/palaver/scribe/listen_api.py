from typing import Protocol, Any, Optional, ClassVar
from enum import Enum
import time
import uuid
from dataclasses import dataclass, field
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

