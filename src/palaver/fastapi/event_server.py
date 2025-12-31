import asyncio
import logging
import time
from enum import Enum
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI
import sounddevice as sd
import soundfile as sf

from palaver.scribe.core import PipelineConfig, ScribePipeline
from palaver.scribe.api import ScribeAPIListener
from palaver.scribe.recorders.sql_drafts import SQLDraftRecorder
from palaver.scribe.audio_listeners import AudioListenerCCSMixin
from palaver.utils.top_error import TopErrorHandler, TopLevelCallback, ERROR_HANDLER
from palaver.fastapi.event_sender import EventSender
from palaver.fastapi.catalog import WebCatalog

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


logger = logging.getLogger("EventNetServer")

class NormalListener(ScribeAPIListener):
    """
    Listens to pipeline generated events and emitts them
    to any listeners via EventSender
    """

    def __init__(self, event_sender: EventSender, play_signals:bool = True):
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

class RescannerLocal(ScribeAPIListener):
    """
    Provide a target for the Rescanner for local events, since the Rescanner
    listens to NetListener events. This makes it possible to keep them straight.
    """

    def __init__(self, rescanner):
        self.rescanner = rescanner
        
    async def on_pipeline_ready(self, pipeline):
        await self.rescanner.on_pipeline_ready(pipeline)
    
    async def on_pipeline_shutdown(self):
        await self.rescanner.on_pipeline_shutdown()
    
    async def on_draft_event(self, event:DraftEvent):
        await self.rescanner.on_local_draft_event(event)

    async def on_text_event(self, event: TextEvent):
        await self.rescanner.on_local_text_event(event)

    async def on_audio_event(self, event):
        await self.rescanner.on_local_audio_event(event)
    
class Rescanner(AudioListenerCCSMixin, ScribeAPIListener):

    def __init__(self, event_sender: EventSender, audio_listener, draft_recorder):
        super().__init__(chunk_duration=0.03)
        self.event_sender = event_sender
        self.audio_listener = audio_listener
        self.draft_recorder = draft_recorder
        self.pre_draft_buffer = AudioRingBuffer(max_seconds=30)
        self.current_draft = None
        self.current_local_draft = None
        self.current_revision = None
        self.last_chunk = None
        self.last_speech_stop = None
        self.texts = []
        self.pipeline = None
        self.logger = logging.getLogger("Rescanner")
        self.audio_listener.add_audio_event_listener(self)
        self.audio_listener.add_text_event_listener(self)
        self.audio_listener.add_draft_event_listener(self)

    async def on_pipeline_ready(self, pipeline):
        self.pipeline = pipeline
        
    async def on_draft_event(self, event: DraftEvent):
        self.logger.info("Got draft event from remote %s", event)
        if isinstance(event, DraftStartEvent):
            self.current_draft = event.draft
            min_time = self.current_draft.audio_start_time
            first = last = None
            for buffered_event in self.pre_draft_buffer.get_from(min_time):
                # emitter in CCSMixin
                if first is None:
                    first = buffered_event
                last = buffered_event
                await self.emit_event(buffered_event)
            self.logger.debug("Emitted buffered events from  %s to %s", first, last)
            self.pre_draft_buffer.clear()
            
        if isinstance(event, DraftEndEvent):
            if self.current_local_draft:
                if self.last_speech_stop:
                    # emitter in CCSMix
                    await self.emit_event(self.last_speech_stop)
                    self.last_speech_stop = None
                if self.current_local_draft.end_text:
                    # we already have completed local draft,
                    # unlikely, but possible
                    await self.save_rescan(event.draft, self.current_local_draft)
                    return
                start_time = time.time()
                async def bump():
                    # Whisper might be waiting to fill buffer, if so bump it
                    if (self.last_chunk.timestamp >= event.draft.audio_end_time and
                        self.pipeline.whisper_tool.sound_pending):
                        await self.pipeline.whisper_tool.flush_pending(timeout=0.1)
                        await bump()
                while not self.current_local_draft.end_text and time.time() - start_time < 15:
                    await asyncio.sleep(0.01)
                    await bump()
                    
                if self.current_local_draft.end_text:
                    await self.save_rescan(event.draft, self.current_local_draft)
                    return
                # We failed to find an end but failed, so force it
                await self.pipeline.draft_maker.force_end()
                if not self.current_local_draft.end_text:
                    raise Exception("logic error, force end of local draft failed")
                
                await self.save_rescan(event.draft, self.current_local_draft)
                return
            else:
                logger.warning("Rescan of draft %s failed to create a local draft from %s",
                               self.current_draft.draft_id, self.texts)
                self.texts = []
                self.current_draft = None
                self.last_chunk = None
                self.last_speech_stop = None
                self.texts = []
                self.current_local_draft = None

    async def save_rescan(self, orig, new):
        event = DraftRescanEvent(original_draft_id=orig.draft_id, draft=new)
        logger.info("Rescan result '%s'", event)
        self.current_draft = None
        self.last_chunk = None
        self.last_speech_stop = None
        self.texts = []
        self.current_local_draft = None

    async def on_text_event(self, event: TextEvent):
        logger.info("incomming text event '%s'", event.text)

    async def on_audio_event(self, event: AudioEvent):
        if not self.current_draft:
            self.pre_draft_buffer.add(event)
            if isinstance(event, AudioChunkEvent):
                self.last_chunk = event
            else:
                logger.info("Blocked audio event %s", event)
            return
        if isinstance(event, AudioSpeechStopEvent):
            # block it so that we don't push whisper buffer
            self.last_speech_stop = event
            logger.info("Got audio speech stop event, blocking %s", event)
        else:
            # emitter in CCSMix
            await self.emit_event(event)
                
    async def on_local_draft_event(self, event:DraftEvent):
        logger.info("Got local draft event %s", event)
        self.current_local_draft = event.draft

    async def on_local_text_event(self, event: TextEvent):
        logger.info("local text event '%s'", event.text)
        self.texts.append(event)

    async def on_local_audio_event(self, event):
        pass

    # AudioListener required:

    async def set_in_speech(self, value):
        pass

    async def start_streaming(self):
        await self.audio_listener.start_streaming()
        
    async def stop_streaming(self):
        await self.audio_listener.stop_streaming()

    
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
        self.event_sender = EventSender(self.port, self)
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
