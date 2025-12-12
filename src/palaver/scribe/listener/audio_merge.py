import asyncio
import logging
import uuid
from copy import deepcopy

from palaver.scribe.audio_events import (
    AudioEvent, AudioChunkEvent, AudioSpeechStartEvent, AudioSpeechStopEvent,
    AudioErrorEvent, AudioStopEvent, AudioEventListener, get_creation_location,
)
from eventemitter import AsyncIOEventEmitter

logger = logging.getLogger("AudioMerge")

class FullShim(AudioEventListener):
    def __init__(self, merge):
        self.merge = merge
        
    async def on_audio_event(self, event: AudioEvent):
        await self.merge.on_full_rate_event(event)

class VADShim(AudioEventListener):

    def __init__(self, merge):
        self.merge = merge
        
    async def on_audio_event(self, event: AudioEvent):
        await self.merge.on_vad_event(event)
        
class AudioMerge(AudioEventListener):
    """
    Merges full-rate audio chunks from a Listener with speech detection signals
    from a VADFilter. Emits full-rate AudioChunkEvents with in_speech flag set
    based on VAD, along with speech start/stop events.
    
    Usage:
    """
    def __init__(self):
        self.emitter = AsyncIOEventEmitter()
        self.full_queue = asyncio.Queue()
        self._full_rate_shim = FullShim(self)
        self._vad_shim = VADShim(self)
        self._last_vad_event = None

    def get_shims(self) -> [FullShim, VADShim]:
        return self._full_rate_shim, self._vad_shim
    
    async def start(self):
        logger.info("AudioMerge processing started")

    async def on_full_rate_event(self, event: AudioEvent):
        if not isinstance(event, AudioChunkEvent):
            return
        new_event = deepcopy(event)
        new_event.creation_location  = get_creation_location()
        if self._last_vad_event and self._last_vad_event.timestamp < event.timestamp:
            await self.full_queue.put(new_event)
        else:
            await self.emitter.emit(AudioEvent, new_event)
            
    async def on_vad_event(self, event: AudioEvent):
        self._last_vad_event = deepcopy(event)
        self._last_vad_event.creation_location  = get_creation_location()
        # the output of VAD trails behind,
        while not self.full_queue.empty():
            full_chunk = await self.full_queue.get()
            if full_chunk.timestamp > self._last_vad_event.timestamp:
                break
            await self.emitter.emit(AudioEvent, full_chunk)
        if not isinstance(event, AudioChunkEvent):
            await self.emitter.emit(AudioEvent, self._last_vad_event)

    def add_event_listener(self, e_listener: AudioEventListener) -> None:
        self.emitter.on(AudioEvent, e_listener.on_audio_event)
