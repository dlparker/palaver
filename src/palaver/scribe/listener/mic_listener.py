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
from palaver.scribe.listen_api import Listener, ListenerCCSMixin, create_source_id
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

class MicListener(ListenerCCSMixin, Listener):
    """ Implements the Listener interface by pulling audio data
    from the default audio input device on the machine"""
    def __init__(self, chunk_duration: float = 0.03):
        super().__init__(chunk_duration)
        self._running = False
        self._reader_task = None
        self._blocksize = BLOCKSIZE
        self._channels = CHANNELS
        self._samplerate = SAMPLE_RATE
        self._dtype = 'float32'
        self._pre_fill_blocks = 10
        self._q_out = asyncio.Queue()
        self._stream = None
        self.source_id = create_source_id("default_mic", datetime.utcnow(), 10000)

    async def start_recording(self) -> None:
        if self._running:
            return
        if self._reader_task:
            return
        self._reader_task = asyncio.create_task(self._reader())
        self._running = True

    async def _reader(self):
        if not self._running:
            return

        loop = asyncio.get_event_loop()

        def callback(indata, outdata, frame_count, time_info, status):
            loop.call_soon_threadsafe(self._q_out.put_nowait, (indata.copy(), status))

        # pre-fill output queue
        for _ in range(self._pre_fill_blocks):
            self._q_out.put(np.zeros((self._blocksize, self._channels), dtype=self._dtype))

        self._stream = sd.Stream(blocksize=self._blocksize, callback=callback, dtype=self._dtype,
                           channels=self._channels)
        self._blocksize = self._stream.blocksize
        self._samplerate = self._stream.samplerate
        self._channels = self._stream.channels
        self._dtype = self._stream.dtype
        await self.emit_event(AudioStartEvent(source_id=self.source_id,
                                              sample_rate=int(self._stream.samplerate),
                                              channels=self._stream.channels[0],
                                              blocksize=self._stream.blocksize,
                                              datatype=self._stream.dtype))
        with self._stream:
            while self._running:
                indata, status = await self._q_out.get()
                if status:
                    msg = f"Error during record: {status}"
                    event = AudioErrorEvent(source_id=self.source_id, message=msg)
                    await self.emit_event(event)                    
                    await self.stop()
                    return
                event = AudioChunkEvent(
                    source_id=self.source_id,
                    data=indata,
                    duration=len(indata) / self._stream.samplerate,
                    in_speech=False,
                    sample_rate=self._stream.samplerate,
                    channels=self._stream.channels,
                    blocksize=self._stream.blocksize,
                    datatype=self._stream.dtype,         
                    meta_data={'device': self._stream.device},
                )
                await self.emit_event(event)                    
                #wait_time = (self._stream.blocksize / self._stream.samplerate) 
                #await asyncio.sleep(wait_time) 
        await self.emit_event(AudioStopEvent(source_id=self.source_id))
        await self._queue.put(None)  # signal EOF
        self._stream = None
        
    async def stop_recording(self) -> None:
        if not self._running:
            return

        await self._cleanup()
        await self.emit_event(AudioStopEvent(source_id=self.source_id))

    async def stop_recording(self) -> None:
        if not self._running:
            return
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
        await self.stop_recording()  # always runs, even on exception/cancellation
        await self._cleanup()  # in case stopped but not cleaned up yet, race

    # Optional: make it usable in sync `with` too (rare but nice)
    def __enter__(self): raise TypeError("Use 'async with' with FileListener")
    def __exit__(self, *args): ...

