import asyncio
import logging
import time
import signal
from enum import Enum
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Optional
import json

from fastapi import FastAPI
import sounddevice as sd
import soundfile as sf
import websockets

from palaver.scribe.core import PipelineConfig, ScribePipeline
from palaver.scribe.api import ScribeAPIListener
from palaver.scribe.recorders.sql_drafts import SQLDraftRecorder
from palaver.scribe.audio_listeners import AudioListenerCCSMixin
from palaver_shared.top_error import TopErrorHandler, TopLevelCallback, ERROR_HANDLER
from palaver.fastapi.index_router import IndexRouter
from palaver.fastapi.event_router import EventRouter
from palaver.fastapi.draft_router import DraftRouter
from palaver.fastapi.ui_router import UIRouter
from palaver.fastapi.rescan import Rescanner, RescannerLocal

from palaver_shared.audio_events import (
    AudioEvent,
    AudioStartEvent,
    AudioStopEvent,
    AudioChunkEvent,
    AudioSpeechStartEvent,
    AudioSpeechStopEvent,
    AudioRingBuffer,
)
from palaver.scribe.audio_listeners import AudioListener
from palaver_shared.text_events import TextEvent
from palaver_shared.draft_events import DraftEvent, DraftStartEvent, DraftEndEvent, DraftRescanEvent
from palaver_shared.serializers import serialize_value


logger = logging.getLogger('VTTServer')

class NormalListener(ScribeAPIListener):
    """
    Listens to pipeline generated events and emitts them
    to any listeners via EventRouter
    """

    def __init__(self, event_router: EventRouter, ui_router=None, play_signals:bool = True):
        super().__init__()
        self.event_router = event_router
        self.ui_router = ui_router
        self.play_signals = play_signals
        self._current_draft = None
        self.pipeline = None

    async def on_pipeline_ready(self, pipeline):
        self.pipeline = pipeline

    async def on_pipeline_shutdown(self):
        self.pipeline = None

    async def on_audio_event(self, event: AudioEvent):
        await self.event_router.send_event(event)
        if self.ui_router:
            await self.ui_router.broadcast_event(event)

    async def on_draft_event(self, event: DraftEvent):
        if isinstance(event, DraftStartEvent):
            logger.info("New draft")
            self.current_draft = event.draft
            await self.pipeline.play_signal_sound("new_draft")
            await self.pipeline.tts_text_to_speaker(f"Draft start")
        if isinstance(event, DraftEndEvent):
            logger.info("Finished draft")
            self.current_draft = None
            logger.info('-'*100)
            logger.info(event.draft.full_text)
            logger.info('-'*100)
            await self.pipeline.play_signal_sound("end_draft")
            await self.pipeline.tts_text_to_speaker(f"Draft complete on {event.draft.end_text}")
        await self.event_router.send_event(event)
        if self.ui_router:
            await self.ui_router.broadcast_event(event)

    async def on_text_event(self, event: TextEvent):
        logger.info("text event '%s'", event.text)
        await self.event_router.send_event(event)
        if self.ui_router:
            await self.ui_router.broadcast_event(event)

class ServerMode(str, Enum):
    """
    There are three modes of operation
    1. DIRECT: Connected to the actual audio source (microphone) and streaming
               full pipeline events to registered listeners 
    2. REMOTE: Accepting audio events from some source over websockets
               and streaming full pipeline events to registered listeners 
    3. RESCAN: Accepting all pipleline events and rescanning drafts to
               produce revised drafts.
    """
    direct = "DIRECT"
    remote = "REMOTE"
    rescan = "RESCAN"
        

class EventNetServer:

    def __init__(self,
                 audio_listener:AudioListener,
                 pipeline_config:PipelineConfig,
                 draft_recorder: SQLDraftRecorder,
                 port: Optional[int] = 8000,
                 mode: Optional[ServerMode] = ServerMode.direct):
        self.audio_listener = audio_listener
        self.pipeline_config = pipeline_config
        self.pipeline = None
        self.draft_recorder = draft_recorder
        self.port = port
        self.mode = mode
        self.app = FastAPI(lifespan=self.lifespan)
        self.web_catalog = None
        self._shutdown_event = asyncio.Event()
        self.index_router = None
        self.event_router = None
        self.draft_router = None

    def add_router(self, router):
        self.app.include_router(router)

    async def shutdown(self):
        """
        Trigger clean shutdown of the server.
        Can be called from tests to initiate shutdown sequence.

        Follows the same pattern as rescan mode's clean_shutdown():
        stops the listener before context managers exit.
        """
        logger.info("Shutdown requested")
        self._shutdown_event.set()

        # Stop the listener (like rescan mode does)
        if hasattr(self.audio_listener, 'stop_streaming'):
            await self.audio_listener.stop_streaming()

        # Give pipeline brief time to notice and wind down
        await asyncio.sleep(0.1)

    async def wait_for_shutdown(self) -> bool:
        """
        Wait for shutdown to be triggered.
        Returns True when shutdown event is set.
        """
        await self._shutdown_event.wait()
        return True

    @asynccontextmanager
    async def lifespan(self, app: FastAPI):
        # Setup error handler for pipeline context
        class ErrorCallback(TopLevelCallback):
            async def on_error(self, error_dict: dict):
                logger.error(f"Pipeline error: {error_dict}")

        error_handler = TopErrorHandler(top_level_callback=ErrorCallback(), logger=logger)
        token = ERROR_HANDLER.set(error_handler)
        try:
            self.index_router = IndexRouter(self)
            self.event_router = EventRouter(self)
            self.draft_router = DraftRouter(self)
            self.ui_router = UIRouter(self)
            # if mode is remote, then audio_listerner is NetListener
            if self.mode in (ServerMode.direct, ServerMode.remote):
                api_listener = NormalListener(self.event_router, self.ui_router)
            else:
                # We attach the audio listener to the Rescanner,
                # and then plug that into the pipeline as the audio_listener.
                # That way it delivers audio samples only when it is rescanning
                # a draft, which means it can work even if something goes wrong
                # with draft boundary detection and it has to reconstruct the
                # actual draft from TextEvents.
                rescanner = Rescanner(self, self.event_router, self.audio_listener, self.draft_recorder)
                self.audio_listener = rescanner
                local_api_listener = RescannerLocal(rescanner, self.ui_router)
                self.pipeline_config.api_listener = local_api_listener 
            # Start pipeline with nested context managers
            logger.info("Starting audio pipeline...")
            async with self.audio_listener:
                async with ScribePipeline(self.audio_listener, self.pipeline_config) as pipeline:
                    self.pipeline = pipeline
                    self.app.include_router(await self.index_router.become_router())
                    self.app.include_router(await self.event_router.become_router())
                    self.app.include_router(await self.draft_router.become_router())
                    self.app.include_router(await self.ui_router.become_router())

                    if self.mode == ServerMode.rescan:
                        my_url = self.index_router.ws_url_base
                        audio_url = self.audio_listener.get_audio_url()
                        logger.info("Registering as rescanner")
                        await self.draft_router.register_rescanner()
                    else:
                        # to_VAD=True for 16kHz downsampled audio
                        await pipeline.add_api_listener(api_listener,  to_VAD=True)
                        await pipeline.add_api_listener(self.draft_recorder)

                    # Start listening
                    await pipeline.start_listener()
                    logger.info("Audio pipeline started")

                    error_handler.wrap_task(pipeline.run_until_error_or_interrupt)

                    # Add monitoring for shutdown event
                    async def monitor_shutdown():
                        """Watch for shutdown event and stop listener when triggered."""
                        await self._shutdown_event.wait()
                        logger.info("Shutdown event detected, stopping listener")
                        await self.audio_listener.stop_streaming()

                    error_handler.wrap_task(monitor_shutdown)

                    # Yield to run the app
                    yield

                    # Shutdown handled by context manager exit
                    logger.info("Shutting down audio pipeline...")
                    if self.mode == ServerMode.rescan:
                        await rescanner.clean_shutdown()
                    
        finally:
            ERROR_HANDLER.reset(token)


    def get_ws_base_url(self):
        return self.index_router.ws_url_base

    def get_audio_url(self):
        if hasattr(self.audio_listener, 'get_audio_url'):
            return self.audio_listener.get_audio_url()
        return None
    
