import asyncio
import logging
import uuid
from copy import deepcopy

from palaver.scribe.audio_events import (
    AudioEvent, AudioChunkEvent, AudioSpeechStartEvent, AudioSpeechStopEvent,
    AudioErrorEvent, AudioEventListener
)
from eventemitter import AsyncIOEventEmitter

logger = logging.getLogger("AudioMerge")

class AudioMerge(AudioEventListener):
    """
    Merges full-rate audio chunks from a MicListener with speech detection signals
    from a VADFilter. Emits full-rate AudioChunkEvents with in_speech flag set
    based on VAD, along with speech start/stop events.
    
    Usage:
    merger = AudioMerge(full_rate_source=listener, vad_source=vadfilter)
    merger.add_event_listener(your_downstream_listener)
    """
    def __init__(self, full_rate_source, vad_source):
        self.emitter = AsyncIOEventEmitter()
        self.full_queue = asyncio.Queue()
        full_rate_source.add_event_listener(self.on_full_rate_event)
        vad_source.add_event_listener(self.on_vad_event)

    async def on_full_rate_event(self, event: AudioEvent):
        if isinstance(event, AudioChunkEvent):
            await self.full_queue.put(event)
        else:
            # Pass through non-chunk events from full-rate source (e.g., AudioStartEvent with original params)
            await self.emitter.emit(AudioEvent, deepcopy(event))

    async def on_vad_event(self, event: AudioEvent):
        if isinstance(event, AudioChunkEvent):
            try:
                full_chunk = await self.full_queue.get_nowait()
            except asyncio.queues.QueueEmpty:
                logger.warning("Full-rate queue empty; possible desync")
                return

            marked_event = AudioChunkEvent(
                source_id=full_chunk.source_id,
                data=full_chunk.data,
                duration=full_chunk.duration,
                in_speech=event.in_speech,
                sample_rate=full_chunk.sample_rate,
                channels=full_chunk.channels,
                blocksize=full_chunk.blocksize,
                datatype=full_chunk.datatype,
                meta_data=full_chunk.meta_data,
                timestamp=full_chunk.timestamp,
                event_id=str(uuid.uuid4())
            )
            await self.emitter.emit(AudioEvent, marked_event)
        elif isinstance(event, (AudioSpeechStartEvent, AudioSpeechStopEvent, AudioErrorEvent)):
            # Pass through VAD-specific events
            await self.emitter.emit(AudioEvent, deepcopy(event))

    def add_event_listener(self, e_listener: AudioEventListener) -> None:
        self.emitter.on(AudioEvent, e_listener.on_audio_event)
