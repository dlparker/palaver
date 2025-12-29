from typing import Any, Optional, ClassVar, Protocol
from enum import StrEnum, auto
import time
import uuid
import inspect
from dataclasses import dataclass, field, fields
from collections import deque
import numpy as np

class AudioEventType(StrEnum):
    audio_start = auto()
    audio_stop = auto()
    audio_chunk = auto()
    audio_input_error = auto()
    audio_speech_start = auto()
    audio_speech_stop = auto()

def get_creation_location():
    # Get the frame two levels up: skip the factory func and dataclass __init__
    frame = inspect.currentframe().f_back.f_back
    filename = frame.f_code.co_filename
    lineno = frame.f_lineno
    return f"{filename}:{lineno}"

@dataclass(kw_only=True)
class AudioEvent:
    """
    stream_offset = how many seconds of samples since stream start
    speech_offset =  how many seconds of samples since speech start, only valid from upstream annotation
    """
    event_type: AudioEventType
    source_id: str
    stream_start_time: float
    speech_start_time: Optional[float] = None
    timestamp: float = field(default_factory=time.time)
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    creation_location: str = field(default_factory=get_creation_location, repr=True)
    author_uri: Optional[str] = None  # Source server/service URI (Story 007)

@dataclass(kw_only=True)
class AudioErrorEvent(AudioEvent):
    event_type: ClassVar[AudioEventType] = AudioEventType.audio_input_error
    message: str

@dataclass(kw_only=True)
class AudioStartEvent(AudioEvent):
    """ Emitted by audio source listener such as MicListener or FileListener"""
    event_type: ClassVar[AudioEventType] = AudioEventType.audio_start
    sample_rate: int                      # actual sample rate of this chunk
    channels: int                        # actual channel count
    blocksize: int
    datatype: str

@dataclass(kw_only=True)
class AudioStopEvent(AudioEvent):
    """ Emitted by audio source listener such as MicListener or FileListener"""
    event_type: ClassVar[AudioEventType] = AudioEventType.audio_stop


np.set_printoptions(threshold=50)
@dataclass(kw_only=True)
class AudioChunkEvent(AudioEvent):
    """ Emitted by audio source listener such as MicListener or FileListener"""
    event_type: ClassVar[AudioEventType] = AudioEventType.audio_chunk
    data: np.ndarray = field(default_factory=lambda: np.array([]))
    duration: float
    sample_rate: int
    channels: int
    blocksize: int
    datatype: str
    in_speech: bool = False
    meta_data: Any = None


@dataclass(kw_only=True)
class AudioSpeechStartEvent(AudioEvent):
    """ Emitted by VAD component (or shim) to indicate speech present """
    event_type: ClassVar[AudioEventType] = AudioEventType.audio_speech_start
    silence_period_ms: int
    vad_threshold: float
    sampling_rate: float
    speech_pad_ms: int

@dataclass(kw_only=True)
class AudioSpeechStopEvent(AudioEvent):
    """ Emitted by VAD component (or shim) to indicate speech switched from present to not present """
    event_type: ClassVar[AudioEventType] = AudioEventType.audio_speech_stop
    last_in_speech_chunk_time: float
    
class AudioEventListener(Protocol):

    async def on_audio_event(self, AudioEvent) -> None: ...


class AudioRingBuffer:

    def __init__(self, max_seconds: float = 2):
        """
        Initialize the ring buffer.
        
        :param max_seconds: Maximum seconds of audio history to retain.
        """
        if max_seconds <= 0:
            raise ValueError("max_seconds must be positive")
        self.max_seconds = max_seconds
        self.buffer: deque[AudioEvent] = deque()

    def has_data(self):
        return len(self.buffer)
    
    def add(self, event: AudioEvent) -> None:
        """
        Add a new AudioEvent to the buffer and prune old entries.
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

    def get_all(self, clear=False) -> list[AudioEvent]:
        """Return a list of all current events in the buffer (oldest to newest)."""
        res = list(self.buffer)
        if clear:
            self.buffer.clear()
        return res

    def clear(self):
        self.buffer.clear()
        
    def get_from(self, start_time) -> list[AudioEvent]:
        """Return a list of all current events in the buffer (oldest to newest)."""
        res = []
        for item in self.buffer:
            if item.timestamp >= start_time:
                res.append(item)
        return res

    
