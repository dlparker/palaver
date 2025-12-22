"""Default API wrapper for Palaver scripts."""

import asyncio
import logging
import uuid
import time
from dataclasses import dataclass, field
from typing import Optional
import sounddevice as sd

from palaver.scribe.text_events import TextEvent
from palaver.scribe.audio_events import AudioEvent, AudioStopEvent, AudioStartEvent, AudioChunkEvent
from palaver.scribe.api import ScribeAPIListener
from palaver.scribe.draft_events import DraftEvent, DraftStartEvent, DraftEndEvent

logger = logging.getLogger("DefaultAPIWrapper")


class DefaultAPIWrapper(ScribeAPIListener):

    def __init__(self, play_sound: bool = False):
        super().__init__()
        self.play_sound = play_sound
        self.full_text = ""
        self.blocks = []
        self.text_events = {}
        self.last_block_name = None
        self.stream = None
        self.start_time = time.time()

    async def on_pipeline_ready(self, pipeline):
        pass

    async def on_pipeline_shutdown(self):
        pass

    async def on_draft_event(self, event: DraftEvent):
        et = time.time() - self.start_time
        if isinstance(event, DraftStartEvent):
            print(f"\n\n{et:7.4}: New draft\n\n")
            self.current_draft = event.draft
        if isinstance(event, DraftEndEvent):
            print(f"\n\n{et:7.4}Finihsed draft\n\n")
            self.current_draft = None
            print('-'*100)
            print(event.draft.full_text)
            print('-'*100)
            
    async def on_text_event(self, event: TextEvent):
        """Called when new transcribed text is available."""
        et = time.time() - self.start_time
        print(f"-------- Text at {et:7.4f} -------")
        print(f"{et:7.4f}: {event.text}")

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
