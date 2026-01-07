import asyncio
from contextlib import asynccontextmanager
from typing import Optional
from pathlib import Path
import os
import time
import logging
import traceback
from datetime import datetime
import numpy as np
import sounddevice as sd
from palaver.utils.top_error import get_error_handler
from palaver.scribe.audio_listeners import AudioListener, AudioListenerCCSMixin, create_source_id
from palaver.scribe.audio_events import (AudioEvent,
                                       AudioErrorEvent,
                                       AudioChunkEvent,
                                       AudioStartEvent,
                                       AudioStopEvent
                                       )

logger = logging.getLogger("MicListener")

SAMPLE_RATE=44100
CHANNELS=1
BLOCKSIZE=int((SAMPLE_RATE * CHANNELS) * .03)

class MicListener(AudioListenerCCSMixin, AudioListener):
    """ Implements the Listener interface by pulling audio data
    from the default audio input device on the machine"""
    def __init__(self, chunk_duration: float = 0.03):
        super().__init__(chunk_duration)
        self._running = False
        self._paused = False
        self._reader_task = None
        self._blocksize = BLOCKSIZE
        self._channels = CHANNELS
        self._samplerate = SAMPLE_RATE
        self._dtype = 'float32'
        self._q_out = asyncio.Queue()
        self._stream = None
        self.source_id = create_source_id("default_mic", datetime.utcnow(), 10000)
        self._in_speech = False
        self._stream_start_time = None

    async def set_in_speech(self, value):
        self._in_speech = value

    async def pause_streaming(self) -> None:
        """Pause audio event emission without stopping the stream.

        The audio stream continues running to avoid device issues,
        but events are not emitted to the pipeline.
        """
        if not self._running:
            logger.warning("Cannot pause: streaming not started")
            return
        if self._paused:
            logger.debug("Already paused")
            return

        self._paused = True
        logger.info("Audio streaming paused")

    async def resume_streaming(self) -> None:
        """Resume audio event emission after pause.

        Restarts emitting events to the pipeline. Stream must have been
        started first via start_streaming().
        """
        if not self._running:
            logger.warning("Cannot resume: streaming not started")
            return
        if not self._paused:
            logger.debug("Already running")
            return

        self._paused = False
        logger.info("Audio streaming resumed")

    def is_paused(self) -> bool:
        """Check if streaming is currently paused."""
        return self._paused

    def is_streaming(self) -> bool:
        """Check if streaming is currently active (started and not stopped)."""
        return self._running

    async def start_streaming(self) -> None:
        if self._running:
            return
        if self._reader_task:
            return
        self._running = True
        self._reader_task = get_error_handler().wrap_task(self._reader)
        self._stream_start_time = time.time()

    async def _reader(self):
        if not self._running:
            return

        try:
            loop = asyncio.get_event_loop()

            def callback(indata, outdata, frame_count, time_info, status):
                loop.call_soon_threadsafe(self._q_out.put_nowait, (indata.copy(), status))

            self._stream = sd.Stream(blocksize=self._blocksize, callback=callback, dtype=self._dtype,
                                     channels=self._channels)
            self._blocksize = self._stream.blocksize
            self._samplerate = self._stream.samplerate
            self._channels = self._stream.channels
            self._dtype = self._stream.dtype
            await self.emit_event(AudioStartEvent(source_id=self.source_id,
                                                  stream_start_time=self._stream_start_time,
                                                  sample_rate=int(self._stream.samplerate),
                                                  channels=self._stream.channels[0],
                                                  blocksize=self._stream.blocksize,
                                                  datatype=self._stream.dtype))
            with self._stream:
                while self._running:
                    indata, status = await self._q_out.get()
                    if status:
                        msg = f"Error during record: {status}"
                        event = AudioErrorEvent(source_id=self.source_id,
                                                stream_start_time=self._stream_start_time,
                                                message=msg)
                        await self.emit_event(event)
                        await self.stop()
                        return

                    # Skip emitting events when paused, but keep draining the queue
                    # to prevent buffer overruns
                    if self._paused:
                        continue

                    event = AudioChunkEvent(
                        source_id=self.source_id,
                        stream_start_time=self._stream_start_time,
                        data=indata,
                        duration=len(indata) / self._stream.samplerate,
                        in_speech=self._in_speech,
                        sample_rate=self._stream.samplerate,
                        channels=self._stream.channels,
                        blocksize=self._stream.blocksize,
                        datatype=self._stream.dtype,
                        meta_data={'device': self._stream.device},
                    )
                    await self.emit_event(event)
            self._stream = None
            self._stream_start_time = None
        except asyncio.CancelledError:
            # Normal cancellation during shutdown
            logger.info("MicListener _reader task cancelled")
            self._reader_task = None
            await self._cleanup()
        
    async def stop_streaming(self) -> None:
        if not self._running:
            return

        await self.stop()
        await self.emit_event(AudioStopEvent(source_id=self.source_id,
                                             stream_start_time=self._stream_start_time))
        logger.info("MicListener issued stop event")

    async def stop(self) -> None:
        self._running = False
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
        await self._cleanup()
        
    async def _cleanup(self) -> None:
        if self._stream is not None:
            self._stream.close()
            self._stream = None
        self._running = False

    # ------------------------------------------------------------------
    # Context manager support to ensure open files get closed
    # ------------------------------------------------------------------
    async def __aenter__(self) -> "FileListener":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.stop()       # always runs, even on exception/cancellation
        await self._cleanup()  # in case stopped but not cleaned up yet, race

    # Optional: make it usable in sync `with` too (rare but nice)
    def __enter__(self): raise TypeError("Use 'async with' with FileListener")
    def __exit__(self, *args): ...

