# palaver/scribe/listen_api.py
from typing import Protocol, Any, Optional, ClassVar, AsyncIterator
from enum import Enum
import time
import uuid
from dataclasses import dataclass, field
import numpy as np
from eventemitter import AsyncIOEventEmitter

class AudioEventType(str, Enum):
    audio_start = "AUDIO_START"
    audio_stop = "AUDIO_STOP"
    audio_chunk = "AUDIO_CHUNK"
    audio_input_error = "AUDIO_INPUT_ERROR"

@dataclass(kw_only=True)
class AudioEvent:
    event_type: AudioEventType
    timestamp: float = field(default_factory=time.time)
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))

@dataclass(kw_only=True)
class AudioErrorEvent(AudioEvent):
    event_type: ClassVar[AudioEventType] = AudioEventType.audio_input_error
    message: str

@dataclass(kw_only=True)
class AudioStartEvent(AudioEvent):
    event_type: ClassVar[AudioEventType] = AudioEventType.audio_start
    sample_rate: int                      # actual sample rate of this chunk
    channels: int                        # actual channel count
    blocksize: int
    datatype: str

@dataclass(kw_only=True)
class AudioStopEvent(AudioEvent):
    event_type: ClassVar[AudioEventType] = AudioEventType.audio_stop

@dataclass(kw_only=True)
class AudioChunkEvent(AudioEvent):
    event_type: ClassVar[AudioEventType] = AudioEventType.audio_chunk
    data: np.ndarray  = field(repr=False) # float32, shape (samples, channels)
    duration: float                       # seconds
    sample_rate: int                      # actual sample rate of this chunk
    channels: int                         # actual channel count
    blocksize: int                        # this block size
    datatype: str                         # string for numpy, "float15", "float32" etc.
    in_speech: bool = False               # Marked as containing speech
    meta_data: Any = None                 # optional metadata, depends on source of audio

class AudioEventListener(Protocol):

    async def on_event(self, AudioEvent) -> None: ...
   
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

