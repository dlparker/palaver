import asyncio
import logging
import time
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
from palaver.utils.top_error import TopErrorHandler, TopLevelCallback, ERROR_HANDLER
from palaver.fastapi.event_router import EventRouter
from palaver.fastapi.catalog import WebCatalog
from palaver.fastapi.rescan import Rescanner, RescannerLocal

from palaver.scribe.audio_events import (
    AudioEvent,
    AudioStartEvent,
    AudioStopEvent,
    AudioChunkEvent,
    AudioSpeechStartEvent,
    AudioSpeechStopEvent,
    AudioRingBuffer,
)
from palaver.scribe.audio_listeners import AudioListener
from palaver.scribe.text_events import TextEvent
from palaver.scribe.draft_events import DraftEvent, DraftStartEvent, DraftEndEvent, DraftRescanEvent
from palaver.utils.serializers import serialize_value


logger = logging.getLogger("EventNetServer")

class NormalListener(ScribeAPIListener):
    """
    Listens to pipeline generated events and emitts them
    to any listeners via EventRouter
    """

    def __init__(self, event_sender: EventRouter, play_signals:bool = True):
        super().__init__()
        self.event_sender = event_sender
        self.play_signals = play_signals
        self._current_draft = None

    async def on_pipeline_ready(self, pipeline):
        pass

    async def on_pipeline_shutdown(self):
        pass

    async def on_audio_event(self, event: AudioEvent):
        await self.event_sender.send_event(event)
        
    async def on_draft_event(self, event: DraftEvent):
        if isinstance(event, DraftStartEvent):
            logger.info("New draft")
            self.current_draft = event.draft
            await self.play_draft_signal("new draft")
        if isinstance(event, DraftEndEvent):
            logger.info("Finished draft")
            self.current_draft = None
            logger.info('-'*100)
            logger.info(event.draft.full_text)
            logger.info('-'*100)
            await self.play_draft_signal("end draft")
        await self.event_sender.send_event(event)
            
    async def on_text_event(self, event: TextEvent):
        logger.info("text event '%s'", event.text)
        await self.event_sender.send_event(event)

    async def play_draft_signal(self, kind: str):
        if not self.play_signals:
            return
        if kind == "new draft":
            file_path = Path(__file__).parent.parent.parent.parent / "signal_sounds" / "tos-computer-06.mp3"
        else:
            file_path = Path(__file__).parent.parent.parent.parent / "signal_sounds" / "tos-computer-03.mp3"
        await self.play_signal_sound(file_path)
            
    async def play_signal_sound(self, file_path):
        sound_file = sf.SoundFile(file_path)
        sr = sound_file.samplerate
        channels = sound_file.channels
        chunk_duration  = 0.03
        frames_per_chunk = max(1, int(round(chunk_duration * sr)))
        out_stream = sd.OutputStream(
            samplerate=sr,
            channels=channels,
            blocksize=frames_per_chunk,
            dtype="float32",
        )
        out_stream.start()

        while True:
            data = sound_file.read(frames=frames_per_chunk, dtype="float32", always_2d=True)
            if data.shape[0] == 0:
                break
            out_stream.write(data)
        out_stream.close()
        sound_file.close()


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
        self.event_sender = EventRouter(self.port, self)
        self.web_catalog = WebCatalog(self.port, self)

    def add_router(self, router):
        self.app.include_router(router)

    @asynccontextmanager
    async def lifespan(self, app: FastAPI):
        # Setup error handler for pipeline context
        class ErrorCallback(TopLevelCallback):
            async def on_error(self, error_dict: dict):
                logger.error(f"Pipeline error: {error_dict}")

        error_handler = TopErrorHandler(top_level_callback=ErrorCallback(), logger=logger)
        token = ERROR_HANDLER.set(error_handler)
        event_router = await self.event_sender.become_router()
        self.app.include_router(event_router)
        index_router = await self.web_catalog.become_router()
        self.app.include_router(index_router)
        try:
            logger.info("Starting audio pipeline...")
            # if mode is remote, then audio_listerner is NetListener
            if self.mode in (ServerMode.direct, ServerMode.remote):
                api_listener = NormalListener(self.event_sender)
            else:
                # We attach the audio listener to the Rescanner,
                # and then plug that into the pipeline as the audio_listener.
                # That way it delivers audio samples only when it is rescanning
                # a draft, which means it can work even if something goes wrong
                # with draft boundary detection and it has to reconstruct the
                # actual draft from TextEvents.
                rescanner = Rescanner(self.event_sender, self.audio_listener, self.draft_recorder)
                self.audio_listener = rescanner
                local_api_listener = RescannerLocal(rescanner)
                self.pipeline_config.api_listener = local_api_listener 
            # Start pipeline with nested context managers
            async with self.audio_listener:
                async with ScribePipeline(self.audio_listener, self.pipeline_config) as pipeline:
                    self.pipeline = pipeline

                    if self.mode != ServerMode.rescan:
                        # to_VAD=True for 16kHz downsampled audio
                        pipeline.add_api_listener(api_listener,  to_VAD=True)
                        pipeline.add_api_listener(self.draft_recorder)

                    # Start listening
                    await pipeline.start_listener()
                    logger.info("Audio pipeline started")

                    error_handler.wrap_task(pipeline.run_until_error_or_interrupt)
                    
                    # Yield to run the app
                    yield

                    # Shutdown handled by context manager exit
                    logger.info("Shutting down audio pipeline...")
        finally:
            ERROR_HANDLER.reset(token)
