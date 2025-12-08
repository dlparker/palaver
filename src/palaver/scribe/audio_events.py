from typing import Any, Optional, ClassVar, Protocol
from enum import Enum
import time
import uuid
from dataclasses import dataclass, field
import numpy as np

class AudioEventType(str, Enum):
    audio_start = "AUDIO_START"
    audio_stop = "AUDIO_STOP"
    audio_chunk = "AUDIO_CHUNK"
    audio_input_error = "AUDIO_INPUT_ERROR"

@dataclass(kw_only=True)
class AudioEvent:
    event_type: AudioEventType
    source_id: str
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

@dataclass(kw_only=True)
class AudioSpeechStartEvent(AudioEvent):
    event_type: ClassVar[AudioEventType] = AudioEventType.audio_start
    silence_period_ms: int
    vad_threshold: float
    sampling_rate: float
    speech_pad_ms: int

@dataclass(kw_only=True)
class AudioSpeechStopEvent(AudioEvent):
    event_type: ClassVar[AudioEventType] = AudioEventType.audio_stop
    
class AudioEventListener(Protocol):

    async def on_audio_event(self, AudioEvent) -> None: ...
