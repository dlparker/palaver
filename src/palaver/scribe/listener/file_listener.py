import asyncio
from contextlib import asynccontextmanager
from typing import Optional, List
from pathlib import Path
import os
import time
import logging
import traceback
from datetime import datetime
import numpy as np
import soundfile as sf

from palaver.utils.top_error import get_error_handler
from palaver.scribe.listen_api import Listener, ListenerCCSMixin, create_source_id
from palaver.scribe.audio_events import (AudioEvent,
                                       AudioErrorEvent,
                                       AudioChunkEvent,
                                       AudioStartEvent,
                                       AudioStopEvent
                                       )

logger = logging.getLogger("FileListener")

class FileListener(ListenerCCSMixin, Listener):
    """ Implements the Listener interface by pulling audio data
    from one or more wav files, good for testing, may have some
    realword application for refining transcription via playback.
    """

    def __init__(self, 
                 audio_file: Path,
                 chunk_duration: float = 0.03, 
                 simulate_timing: bool = True):
        super().__init__(chunk_duration)
        self._current_file = audio_file
        self._simulate_timing = simulate_timing
        self._sound_file: Optional[sf.SoundFile] = None
        self._running = False
        self._reader_task = None
        self._fake_stream_start_time = None
        self._read_start_time = None
        self._duration_cursor = 0.0
        self._in_speech = False
        self.source_id = create_source_id("file", datetime.utcnow(), 10000)

    async def set_in_speech(self, value):
        self._in_speech = value
        
    async def start_streaming(self) -> None:
        if self._running:
            return

        self._sound_file = sf.SoundFile(self._current_file)
        self._reader_task = get_error_handler().wrap_task(self._reader)
        self._running = True
        # calculate the start time so that it appears that
        # the audio stream terminated at the current time,
        # so the event timestamps will be adjusted accorindingly

        self._read_start_time = time.time()
        self._fake_stream_start_time = self._read_start_time -  self._sound_file.frames /self._sound_file.samplerate
        self._duration_cursor = self._fake_stream_start_time

    async def _reader(self):
        # this gets wraped with get_error_handler so let the errors fly
        if not self._running:
            return

        while self._running:
            sr = self._sound_file.samplerate
            channels = self._sound_file.channels
            frames_per_chunk = max(1, int(round(self.chunk_duration * sr)))
            await self.emit_event(AudioStartEvent(source_id=self.source_id,
                                                  timestamp=self._duration_cursor,
                                                  stream_start_time=self._fake_stream_start_time,
                                                  sample_rate=sr,
                                                  channels=channels,
                                                  blocksize=frames_per_chunk,
                                                  datatype='float32'))

            # Play current file until EOF
            while True:
                data = self._sound_file.read(frames=frames_per_chunk, dtype="float32", always_2d=True)
                if data.shape[0] == 0:
                    break

                duration = data.shape[0] / sr
                await self.emit_event(AudioChunkEvent(
                    source_id=self.source_id,
                    timestamp=self._duration_cursor,
                    stream_start_time=self._fake_stream_start_time,
                    in_speech=self._in_speech,
                    data=data,
                    duration=duration,
                    sample_rate=sr,
                    channels=channels,
                    blocksize=frames_per_chunk,
                    datatype='float32',
                    meta_data={'file': str(self._current_file)},
                ))
                self._duration_cursor += duration
                if self._simulate_timing:
                    # We want to simulate the timing of actual audio input
                    # because things downstream care about it, such as the ring buffer
                    await asyncio.sleep(self.chunk_duration)
            break
        await self.emit_event(AudioStopEvent(source_id=self.source_id,
                                             timestamp=self._duration_cursor,
                                             stream_start_time=self._fake_stream_start_time))
        self._fake_stream_start_time = None
        self._read_start_time = None
        await self._cleanup()
        self._reader_task = None

    async def stop_streaming(self) -> None:
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
        if self._sound_file is not None:
            self._sound_file.close()
            self._sound_file = None
        self._current_file = None
        self._running = False

    # ------------------------------------------------------------------
    # Context manager support to ensure open files get closed
    # ------------------------------------------------------------------
    async def __aenter__(self) -> "FileListener":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.stop_streaming()  # always runs, even on exception/cancellation
        await self._cleanup()  # in case stopped but not cleaned up yet, race

    # Optional: make it usable in sync `with` too (rare but nice)
    def __enter__(self): raise TypeError("Use 'async with' with FileListener")
    def __exit__(self, *args): ...

