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
from palaver.scribe.audio_listeners import AudioListener, AudioListenerCCSMixin, create_source_id
from palaver.scribe.audio_events import AudioEvent, AudioEventType
from palaver.scribe.text_events import TextEvent, TextEventListener
from palaver.scribe.draft_events import DraftEvent, DraftEventListener


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
        self._reader_task = None
        self._event_queue = asyncio.Queue()
        self._client = None
        self._audio_url = audio_url
        self._audio_only = audio_only
        self._text_emitter = AsyncIOEventEmitter()
        self._draft_emitter = AsyncIOEventEmitter()

    async def set_in_speech(self, value):
        # only used when 
        self._in_speech = value

    async def start_streaming(self) -> None:
        if self._running:
            return

        self._reader_task = get_error_handler().wrap_task(self._reader)
        self._running = True

    def add_text_event_listener(self, e_listener: TextEventListener) -> None:
        self._text_emitter.on(AudioEvent, e_listener.on_text_event)
        
    def add_draft_event_listener(self, e_listener: DraftEventListener) -> None:
        self._draft_emitter.on(AudioEvent, e_listener.on_draft_event)
        
    async def _reader(self):
        # this gets wraped with get_error_handler so let the errors fly
        if not self._running:
            return
        try:
            async with websockets.connect(self.audio_url) as websocket:
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
                               str(DraftRevisionEvent),
                               ]
                subscription = {"subscribe": events}
                await websocket.send(json.dumps(subscription))
                while self._running:
                    async for message in websocket:
                        event_dict = json.loads(message)
                        event = event_from_dict(event_dict)
                        if "Audio" in event_dict['event_class']:
                            self.emit_event(AudioEvent, event)
                        elif "TextEvent" in event_dict['event_class']:
                            self._text_emitter.emit(TextEvent, event)
                        elif "Draft" in event_dict['event_class']:
                            self._draft_emitter.emit(DraftEvent, event)
                            
        except websockets.exceptions.ConnectionClosed:
            print("\nConnection closed by server")
        except KeyboardInterrupt:
            print("\nShutting down client...")
            await self.emit_event(AudioStopEvent(source_id=self.source_id,
                                                 timestamp=time.time(),
                                                 stream_start_time=time.time()))
        except asyncio.CancelledError:
            pass
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

