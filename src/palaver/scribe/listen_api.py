from typing import Protocol, Any, Optional
from enum import Enum
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import ClassVar
import numpy as np

class AudioEventType(str, Enum):
    audio_start = "AUDIO_START"
    audio_stop = "AUDIO_STOP"
    audio_chunk = "AUDIO_CHUNK"
    audio_input_error = "AUDIO_INPUT_ERROR"

    def __str__(self):
        return self.value

@dataclass(kw_only=True)
class AudioEvent:
    event_type: AudioEventType
    timestamp: float = field(default_factory=time.time, kw_only=True)
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()), kw_only=True)


@dataclass(kw_only=True)
class AudioInputErrorEvent(AudioEvent):
    event_type: ClassVar[AudioEventType] = AudioEventType.audio_input_error
    message:str

@dataclass(kw_only=True)
class AudioStartEvent(AudioEvent):
    event_type: ClassVar[AudioEventType] = AudioEventType.audio_start

@dataclass(kw_only=True)
class AudioStopEvent(AudioEvent):
    event_type: ClassVar[AudioEventType] = AudioEventType.audio_stop

@dataclass(kw_only=True)
class AudioChunkEvent(AudioEvent):
    event_type: ClassVar[AudioEventType] = AudioEventType.audio_chunk
    data: np.ndarray
    duration: float
    in_speech: bool
    params: Any = None

class AudioEventListener(Protocol):

    async def on_event(self, AudioEvent) -> None: ...
   

class Listener(Protocol):
    event_listener: AudioEventListener | None

    async def set_event_listener(self, e_listener: AudioEventListener) -> None: ...

    async def emit_event(self, event: AudioEvent) -> None: ...

    async def start_recording(self) -> None: ...

    async def stop_recording(self) -> None: ...


class ListenerCCSMixin:

    def __init__(self, samplerate: int, channels: int, blocksize: int) -> None:
        self.event_listener: AudioEventListener | None = None
        self.samplerate = samplerate
        self.channels = channels
        self.blocksize = blocksize

    async def set_event_listener(self, e_listener: AudioEventListener) -> None:
        self.event_listener = e_listener

    async def emit_event(self, event: AudioEvent) -> None:
        if self.event_listener:
            await self.event_listener.on_event(event)

