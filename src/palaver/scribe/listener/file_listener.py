import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional
import wave
import os
import time
import logging
import aiofiles
import numpy as np
from palaver.scribe.listen_api import (Listener,
                                       ListenerCCSMixin,
                                       AudioEvent,
                                       AudioChunkEvent,
                                       AudioStartEvent,
                                       AudioStopEvent
                                       )

logger = logging.getLogger("FileListener")

class FileListener(ListenerCCSMixin, Listener):
    """ Implements the Listener interface by pulling audi data
    from one or more wav files, good for testing, may have some
    realword application for refining transcription via playback.
    """

    def __init__(self, samplerate: int, channels: int, blocksize: int, files: Optional[list[os.PathLike[str]]]):
        super().__init__(samplerate, channels, blocksize)
        self.files = files
        self.file_index = -1
        self.current_file_path = None
        self._wave_file: wave.Wave_read | None = None
        self._running = False
        self._queue = asyncio.Queue()
        self._reader_task = None
        self._emitter_task = None

    async def add_file(self, filepath: os.PathLike[str]) -> None:
        self.files.append(filepath)

    async def start_recording(self) -> None:
        if self._running:
            return

        if len(self.files) > self.file_index:
            self.file_index += 1
        if len(self.files) > self.file_index - 1:
            self.current_file_path = self.files[self.file_index]
            self._running = True
            self._reader_task = asyncio.create_task(self._reader())
            self._emitter_task = asyncio.create_task(self._emitter())

        # Emit a "started" event if you want
        await self.emit_event(AudioStartEvent())

    async def _emitter(self):
        while True:
            event = await self._queue.get()
            if event is None:  # end of current file
                break
            # if no listener, nothing to do with data, so why did someone ask for it?
            await self.event_listener.on_event(event)
                
    async def _reader(self):
        if not self._running or not self.current_file_path:
            return

        def _stream_frames():
            """Synchronous generator that yields raw frames exactly like your original code"""
            with wave.open(str(self.current_file_path), "rb") as wav:
                while True:
                    frames = wav.readframes(4096)  # your original 4K block size
                    if not frames:
                        return
                    yield frames, wav.getparams()

        # This runs the blocking wave.open + readframes loop in a thread
        # but yields one small block at a time
        for raw_frames, params in await asyncio.to_thread(_stream_frames):
            nchannels, sampwidth, framerate, nframes, comptype, compname = params

            # ────── Your original conversion code, unchanged ──────
            if sampwidth == 2:
                audio = np.frombuffer(raw_frames, dtype=np.int16).astype(np.float32) / 32768.0
            elif sampwidth == 4:
                audio = np.frombuffer(raw_frames, dtype=np.int32).astype(np.float32) / 2147483648.0
            else:
                raise ValueError(f"Unsupported sample width: {sampwidth}")

            if nchannels == 1:
                audio = np.column_stack((audio, audio))
            elif nchannels == 2:
                audio = audio.reshape(-1, 2)
            else:
                raise ValueError(f"Unsupported channels: {nchannels}")

            if framerate != self.samplerate:
                from scipy.signal import resample_poly
                audio = resample_poly(audio, self.samplerate, framerate, axis=0)

            chunk_duration = len(audio) / self.samplerate
            event = AudioChunkEvent(
                data=audio,
                duration=chunk_duration,
                in_speech=False,
                params=params,
            )
            await self._queue.put(event)
            # Real-time pacing
            await asyncio.sleep(self.blocksize / self.samplerate)


        await self.emit_event(AudioStopEvent())
        await self._queue.put(None)  # signal EOF
        
    async def stop_recording(self) -> None:
        if not self._running:
            return

        self.current_file_path = None
        await self._cleanup()
        await self.emit_event(AudioStopEvent())

    async def _cleanup(self) -> None:
        if self._wave_file is not None:
            try:
                self._wave_file.close()
            finally:
                self._wave_file = None
        self._running = False

    # ------------------------------------------------------------------
    # Context manager support to ensure open files get closed
    # ------------------------------------------------------------------
    async def __aenter__(self) -> "FileListener":
        await self.start_recording()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.stop_recording()  # always runs, even on exception/cancellation

    # Optional: make it usable in sync `with` too (rare but nice)
    def __enter__(self): raise TypeError("Use 'async with' with FileListener")
    def __exit__(self, *args): ...

    # ------------------------------------------------------------------
    # Receive audio fragments from the audio pipeline
    # ------------------------------------------------------------------
    async def on_audio_fragment(self, audio_bytes: bytes) -> None:
        """Called by your audio source whenever a new chunk is ready."""
        if self._running and self._wave_file:
            self._wave_file.writeframes(audio_bytes)
