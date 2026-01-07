import asyncio
from contextlib import asynccontextmanager
from typing import Optional, List
from pathlib import Path
import os
import time
import logging
import traceback
import json
from datetime import datetime
import websockets
import numpy as np
import soundfile as sf
from eventemitter import AsyncIOEventEmitter

from palaver.utils.top_error import get_error_handler
from palaver.scribe.audio_listeners import AudioListener, AudioListenerCCSMixin, create_source_id
from palaver.scribe.audio_events import AudioStartEvent, AudioChunkEvent, AudioStopEvent, AudioErrorEvent
from palaver.scribe.audio_events import AudioEvent, AudioEventType, AudioSpeechStartEvent, AudioSpeechStopEvent
from palaver.scribe.text_events import TextEvent, TextEventListener
from palaver.scribe.draft_events import DraftEvent, DraftEventListener, DraftStartEvent, DraftEndEvent
from palaver.utils.serializers import event_from_dict


logger = logging.getLogger("NetListener")

class NetListener(AudioListenerCCSMixin, AudioListener):
    """ Implements the Listener interface by receiving
    audio data from some network source that sends
    fully formed AudioEvent data from some external source
    """

    def __init__(self,  audio_url, chunk_duration: float = 0.03, audio_only: bool = True):
        super().__init__(chunk_duration)
        self._in_speech = False
        self.source_id = create_source_id("net", datetime.utcnow(), 10000)
        self._buffer_size = 44,000 * self.chunk_duration
        self._running = False
        self._paused = False
        self._reader_task = None
        self._event_queue = asyncio.Queue()
        self._client = None
        self._audio_url = audio_url
        self._audio_only = audio_only
        self._text_emitter = AsyncIOEventEmitter()
        self._draft_emitter = AsyncIOEventEmitter()
        self._websocket = None

    async def set_in_speech(self, value):
        # only used when
        self._in_speech = value

    def get_audio_url(self):
        return self._audio_url

    async def pause_streaming(self) -> None:
        """Pause event emission without closing the connection.

        The WebSocket connection remains open and continues receiving events,
        but they are not emitted to the pipeline.
        """
        if not self._running:
            logger.warning("Cannot pause: streaming not started")
            return
        if self._paused:
            logger.debug("Already paused")
            return

        self._paused = True
        logger.info("Network streaming paused")

    async def resume_streaming(self) -> None:
        """Resume event emission after pause.

        Restarts emitting events to the pipeline. Connection must have been
        started first via start_streaming().
        """
        if not self._running:
            logger.warning("Cannot resume: streaming not started")
            return
        if not self._paused:
            logger.debug("Already running")
            return

        self._paused = False
        logger.info("Network streaming resumed")

    def is_paused(self) -> bool:
        """Check if streaming is currently paused."""
        return self._paused

    def is_streaming(self) -> bool:
        """Check if streaming is currently active (started and not stopped)."""
        return self._running

    async def start_streaming(self) -> None:
        if self._running:
            return

        self._reader_task = get_error_handler().wrap_task(self._reader)
        self._running = True

    def add_text_event_listener(self, e_listener: TextEventListener) -> None:
        logger.info("Registered text listener %s", e_listener)
        self._text_emitter.on(TextEvent, e_listener.on_text_event)
        
    def add_draft_event_listener(self, e_listener: DraftEventListener) -> None:
        logger.info("Registered draft listener %s", e_listener)
        self._draft_emitter.on(DraftEvent, e_listener.on_draft_event)
        
    async def _reader(self):
        # this gets wraped with get_error_handler so let the errors fly
        if not self._running:
            return
        try:
            async with websockets.connect(f"{self._audio_url}/events") as websocket:
                self._websocket = websocket
                events = [str(AudioStartEvent),
                          str(AudioStopEvent),
                          str(AudioChunkEvent),
                          str(AudioSpeechStartEvent),
                          str(AudioSpeechStopEvent),
                          str(AudioErrorEvent)]
                if not self._audio_only:
                    events += [str(TextEvent),
                               str(DraftStartEvent),
                               str(DraftEndEvent),
                               ]
                subscription = {"subscribe": events}
                await websocket.send(json.dumps(subscription))
                regy_reply = None
                chunk_count = 0
                while self._running:
                    async for message in websocket:
                        if regy_reply is None:
                            regy_reply = message
                            continue
                        event_dict = json.loads(message)
                        event = event_from_dict(event_dict)

                        # Skip emitting events when paused, but keep receiving
                        # to maintain the connection
                        if self._paused:
                            continue

                        if "Audio" in event_dict['event_class']:
                            if isinstance(event, AudioChunkEvent):
                                if chunk_count % 1000 == 0:
                                    logger.debug(event)
                                chunk_count += 1
                            else:
                                logger.debug(event)
                            await self.emit_event(event)
                        elif "TextEvent" in event_dict['event_class']:
                            logger.debug(event)
                            await self._text_emitter.emit(TextEvent, event)
                        elif "Draft" in event_dict['event_class']:
                            logger.debug(event)
                            await self._draft_emitter.emit(DraftEvent, event)

        except websockets.exceptions.ConnectionClosed:
            logger.info("Connection closed by server")
        except KeyboardInterrupt:
            logger.info("Shutting down client...")
            await self.emit_event(AudioStopEvent(source_id=self.source_id,
                                                 timestamp=time.time(),
                                                 stream_start_time=time.time()))
        except asyncio.CancelledError:
            logger.debug("NetListener reader task cancelled")
        finally:
            self._websocket = None
            self._reader_task = None

    async def stop_streaming(self) -> None:
        if not self._running:
            return
        self._running = False

        # Close the websocket to break out of the async for loop
        if self._websocket:
            await self._websocket.close()

        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass

    # ------------------------------------------------------------------
    # Context manager support to ensure open files get closed
    # ------------------------------------------------------------------
    async def __aenter__(self) -> "NetListener":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.stop_streaming()  # always runs, even on exception/cancellation

    # Optional: make it usable in sync `with` too (rare but nice)
    def __enter__(self): raise TypeError("Use 'async with' with NetListener")
    def __exit__(self, *args): ...

