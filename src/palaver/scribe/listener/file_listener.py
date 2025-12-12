import asyncio
from contextlib import asynccontextmanager
from typing import Optional, Callable, List
from pathlib import Path
import os
import time
import logging
import traceback
from datetime import datetime
import numpy as np
import soundfile as sf

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
                 error_callback: Callable[[dict], None],
                 files: list[Path | str],
                 chunk_duration: float = 0.03, 
                 simulate_timing: bool = True):
        super().__init__(chunk_duration, error_callback)
        self.files: List[Path] = [Path(p) for p in (files or [])]
        self.error_callback = error_callback
        self.current_file = None
        self._simulate_timing = simulate_timing
        self._sound_file: Optional[sf.SoundFile] = None
        self._running = False
        self._reader_task = None
        self.source_id = create_source_id("file", datetime.utcnow(), 10000)

    async def add_file(self, filepath: os.PathLike[str]) -> None:
        self.files.append(filepath)

    async def start_recording(self) -> None:
        if self._running:
            return

        if len(self.files) > 0:
            self._reader_task = asyncio.create_task(self._reader())
            self._running = True

    async def _load_next_file(self) -> bool:
        """Internal: close current and open next file. Returns True if a file was opened."""
        if self._sound_file is not None:
            self._sound_file.close()
            self._sound_file = None

        if self.files:
            self._current_file = self.files.pop(0)
        else:
            self._current_file = None

        if not self._current_file:
            return False

        # might blow up, let it
        self._sound_file = sf.SoundFile(self._current_file)
        return True
        
    async def _reader(self):
        try:
            await self._reader_inner()
        except asyncio.CancelledError:
            # Normal cancellation during shutdown
            logger.info("FileListener _reader task cancelled")
        except Exception as e:
            try:
                error_dict = dict(
                    exception=e,
                    traceback=traceback.format_exc(),
                    source=self,
                )
                self.error_callback(error_dict)
            except:
                pass
        finally:
            self._reader_task = None
            await self._cleanup()
            
            
    async def _reader_inner(self):
        if not self._running:
            return

        await self._load_next_file()
        if self._sound_file is None:
            return

        while self._running:
            sr = self._sound_file.samplerate
            channels = self._sound_file.channels
            frames_per_chunk = max(1, int(round(self.chunk_duration * sr)))
            await self.emit_event(AudioStartEvent(source_id=self.source_id,
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
                    data=data,
                    duration=duration,
                    sample_rate=sr,
                    channels=channels,
                    blocksize=frames_per_chunk,
                    datatype='float32',
                    in_speech=False,
                    meta_data={'file': self._current_file},
                ))
                if self._simulate_timing:
                    # We want to simulate the timing of actual audio input
                    # because things downstream care about it, such as the ring buffer
                    await asyncio.sleep(self.chunk_duration)
            # File finished — move to next one automatically
            await self._load_next_file()

            if not self._current_file:
                break

        # All done
        await self.emit_event(AudioStopEvent(source_id=self.source_id))
        await self._cleanup()
        self._reader_task = None
        
    async def stop_recording(self) -> None:
        if not self._running:
            return

        self.current_file_path = None
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
        await self.stop_recording()  # always runs, even on exception/cancellation
        await self._cleanup()  # in case stopped but not cleaned up yet, race

    # Optional: make it usable in sync `with` too (rare but nice)
    def __enter__(self): raise TypeError("Use 'async with' with FileListener")
    def __exit__(self, *args): ...

