import asyncio
import logging
import uuid
from copy import deepcopy

from palaver.scribe.audio_events import (
    AudioEvent, AudioChunkEvent, AudioSpeechStartEvent, AudioSpeechStopEvent,
    AudioErrorEvent, AudioStopEvent, AudioEventListener
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
        self.vad_queue = asyncio.Queue()
        self._processing_task = None
        self._running = False
        self._full_rate_shim = FullShim(self)
        self._vad_shim = VADShim(self)

    def get_shims(self) -> [FullShim, VADShim]:
        return self._full_rate_shim, self._vad_shim
    
    async def start(self):
        """Start the merge processing task."""
        if not self._running:
            self._running = True
            self._processing_task = asyncio.create_task(self._process_merge())
            logger.info("AudioMerge processing started")

    async def on_full_rate_event(self, event: AudioEvent):
        if isinstance(event, AudioChunkEvent):
            await self.full_queue.put(event)
        else:
            # Pass through non-chunk events from full-rate source (e.g., AudioStartEvent with original params)
            await self.emitter.emit(AudioEvent, deepcopy(event))

    async def on_vad_event(self, event: AudioEvent):
        if isinstance(event, AudioChunkEvent):
            # Queue VAD chunk for synchronized processing
            await self.vad_queue.put(event)
        elif isinstance(event, (AudioSpeechStartEvent, AudioSpeechStopEvent)):
            # Pass through VAD-specific events immediately
            await self.emitter.emit(AudioEvent, deepcopy(event))
        elif isinstance(event, (AudioErrorEvent, AudioStopEvent)):
            # Termination signals - send to queue and pass through
            await self.vad_queue.put(None)
            await self.full_queue.put(None)
            await self.emitter.emit(AudioEvent, deepcopy(event))

    async def _process_merge(self):
        """Main processing loop - synchronizes both queues."""
        try:
            while self._running:
                full_chunk = await self.full_queue.get()
                vad_chunk = await self.vad_queue.get()

                if full_chunk is None or vad_chunk is None:
                    logger.info("AudioMerge received termination signal")
                    break

                # Validate timestamp alignment
                time_diff = abs(full_chunk.timestamp - vad_chunk.timestamp)
                if time_diff > 0.1:  # 100ms tolerance
                    logger.warning(f"AudioMerge desync: {time_diff:.3f}s difference")

                # Create merged event with full-rate audio + VAD in_speech flag
                marked_event = AudioChunkEvent(
                    source_id=full_chunk.source_id,
                    data=full_chunk.data,
                    duration=full_chunk.duration,
                    in_speech=vad_chunk.in_speech,
                    sample_rate=full_chunk.sample_rate,
                    channels=full_chunk.channels,
                    blocksize=full_chunk.blocksize,
                    datatype=full_chunk.datatype,
                    meta_data=full_chunk.meta_data,
                    timestamp=full_chunk.timestamp,
                    event_id=str(uuid.uuid4())
                )
                await self.emitter.emit(AudioEvent, marked_event)
        except Exception as e:
            logger.error(f"AudioMerge processing error: {e}", exc_info=True)
        finally:
            self._running = False
            logger.info("AudioMerge processing stopped")

    async def flush(self):
        """Flush remaining events and stop processing."""
        logger.info("AudioMerge flushing...")
        await self.vad_queue.put(None)
        await self.full_queue.put(None)

        if self._processing_task:
            try:
                await asyncio.wait_for(self._processing_task, timeout=2.0)
            except asyncio.TimeoutError:
                logger.warning("AudioMerge flush timed out")
                self._processing_task.cancel()

        self._running = False
        logger.info("AudioMerge flush complete")

    def add_event_listener(self, e_listener: AudioEventListener) -> None:
        self.emitter.on(AudioEvent, e_listener.on_audio_event)
