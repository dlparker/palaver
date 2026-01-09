"""Default API wrapper for Palaver scripts."""

import asyncio
import logging
import uuid
import time
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional
import sounddevice as sd
import soundfile as sf

from palaver_shared.text_events import TextEvent
from palaver_shared.audio_events import AudioEvent, AudioStopEvent, AudioStartEvent, AudioChunkEvent
from palaver.scribe.api import ScribeAPIListener
from palaver_shared.draft_events import DraftEvent, DraftStartEvent, DraftEndEvent

logger = logging.getLogger("DefaultAPIWrapper")


class DefaultAPIWrapper(ScribeAPIListener):

    def __init__(self, draft_recorder = None, play_sound: bool = False):
        super().__init__()
        self.play_sound = play_sound
        self.full_text = ""
        self.blocks = []
        self.text_events = {}
        self.last_block_name = None
        self.stream = None
        self.start_time = time.time()
        self.draft_recorder = draft_recorder

    async def on_pipeline_ready(self, pipeline):
        if self.draft_recorder:
            await pipeline.add_api_listener(self.draft_recorder)

    async def on_pipeline_shutdown(self):
        pass

    async def on_draft_event(self, event: DraftEvent):
        now = time.time() 
        et = now - self.start_time
        if isinstance(event, DraftStartEvent):
            print(f"\n\n{now} - {et:7.4}: New draft\n\n")
            self.current_draft = event.draft
        if isinstance(event, DraftEndEvent):
            print(f"\n\n{now} - {et:7.4} Finished draft\n\n")
            self.current_draft = None
            print('-'*100)
            print(event.draft.full_text)
            print('-'*100)
            
    async def on_text_event(self, event: TextEvent):
        """Called when new transcribed text is available."""
        now = time.time() 
        et = now - self.start_time
        print(f"-------- Text at {et:7.4f} - {now} -------")
        print(f"{now} - {et:7.4f}: {event.text}")

    async def on_audio_event(self, event: AudioEvent):
        """Handle audio events - optionally play sound and finalize blocks."""
        if isinstance(event, AudioStartEvent):
            pass
        elif isinstance(event, AudioStopEvent):
            logger.info("Got audio stop event %s", event)
        elif isinstance(event, AudioChunkEvent):
            if self.play_sound:
                if not self.stream:
                    self.stream = sd.OutputStream(
                        samplerate=event.sample_rate,
                        channels=event.channels,
                        blocksize=event.blocksize,
                        dtype=event.datatype,
                    )
                    self.stream.start()
                    print("Opened audio playback stream")
                audio = event.data
                self.stream.write(audio)

