import asyncio
import logging
import time
import json

from palaver.scribe.api import ScribeAPIListener
from palaver.scribe.audio_listeners import AudioListenerCCSMixin
from palaver.fastapi.event_router import EventRouter
from palaver.scribe.audio_events import (
    AudioEvent,
    AudioChunkEvent,
    AudioSpeechStartEvent,
    AudioSpeechStopEvent,
    AudioRingBuffer,
)
from palaver.scribe.text_events import TextEvent
from palaver.scribe.draft_events import DraftEvent, DraftStartEvent, DraftEndEvent
from palaver.utils.serializers import serialize_value


logger = logging.getLogger("Rescanner")


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

    def __init__(self, server, event_router: EventRouter, audio_listener, draft_recorder):
        super().__init__(chunk_duration=0.03)
        self.server = server
        self.event_router = event_router
        self.audio_listener = audio_listener
        self.draft_recorder = draft_recorder
        self.pre_draft_buffer = AudioRingBuffer(max_seconds=30)
        self.current_draft = None
        self.current_local_draft = None
        self.current_revision = None
        self.last_chunk = None
        self.texts = []
        self.pipeline = None
        self.logger = logging.getLogger("Rescanner")
        self._heartbeat_task = None
        self.audio_listener.add_audio_event_listener(self)
        self.audio_listener.add_text_event_listener(self)
        self.audio_listener.add_draft_event_listener(self)

    async def on_pipeline_ready(self, pipeline):
        self.pipeline = pipeline
        self.pipeline.whisper_tool.set_fast_mode(False)
        # Start heartbeat to maintain registration
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        self.logger.info("Started rescanner heartbeat task")

    async def _heartbeat_loop(self):
        """Background task that sends registration heartbeat every second."""
        try:
            while True:
                try:
                    await self.server.draft_router.register_rescanner()
                except Exception as e:
                    self.logger.warning(f"Heartbeat registration failed: {e}")
                await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            self.logger.info("Heartbeat task cancelled")
            raise

    async def clean_shutdown(self):
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
        await self.audio_listener.stop_streaming()

    def get_audio_url(self):
        return self.audio_listener.get_audio_url()
    
    async def on_pipeline_shutdown(self):
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
        await self.audio_listener.stop_streaming()
        
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
            # we want the draft start signal to get processed right away
            await self.pipeline.whisper_tool.flush_pending()
            self.logger.debug("Emitted buffered events from  %f to %f", first.timestamp, last.timestamp)
            self.pre_draft_buffer.clear()

        if isinstance(event, DraftEndEvent):
            await self.pipeline.whisper_tool.flush_pending()
            #self.pipeline.whisper_tool.set_fast_mode(True)
            if not self.current_local_draft:
                self.logger.debug("Remote says draft done, but local has no draft, flusing and pushing")
                start_time = time.time()
                while not self.current_local_draft and time.time() - start_time < 5:
                    await self.pipeline.whisper_tool.flush_pending()
                    await asyncio.sleep(0.01)
            if self.current_local_draft:
                if self.current_local_draft.end_text:
                    # we already have completed local draft,
                    # unlikely, but possible
                    await self.save_rescan(event.draft, self.current_local_draft)
                    return
                start_time = time.time()
                async def bump():
                    # Whisper might be waiting to fill buffer, if so bump it
                    if self.pipeline.whisper_tool.sound_pending():
                        await self.pipeline.whisper_tool.flush_pending()
                    
                self.logger.debug("Remote says draft done, but local hasing finished, flusing and pushing")
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
                if self.current_draft:
                    logger.warning("Rescan of draft %s failed to create a local draft from %s",
                                   self.current_draft.draft_id, self.texts)
                self.texts = []
                self.current_draft = None
                self.last_chunk = None
                self.texts = []
                self.current_local_draft = None

    async def save_rescan(self, orig, new):
        new.parent_draft_id = orig.draft_id
        await self.server.draft_router.send_new_draft(new)
        logger.info("Rescan result original id %s new id %s '%s' ", new.parent_draft_id, new.draft_id, new.full_text)
        self.current_draft = None
        self.last_chunk = None
        self.texts = []
        self.current_local_draft = None

    async def on_text_event(self, event: TextEvent):
        logger.info("incomming text event '%s'", event.text)

    async def on_audio_event(self, event: AudioEvent):
        if not self.current_draft:
            if isinstance(event, AudioChunkEvent):
                self.pre_draft_buffer.add(event)
                self.last_chunk = event
            else:
                logger.info("Blocked audio event %s", event)
            return
        if isinstance(event, AudioSpeechStopEvent) or isinstance(event, AudioSpeechStartEvent):
            return
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
