"""Default API wrapper for Palaver scripts."""

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from typing import Optional
import sounddevice as sd

from palaver.scribe.text_events import TextEvent
from palaver.scribe.audio_events import AudioEvent, AudioStopEvent, AudioStartEvent, AudioChunkEvent
from palaver.scribe.command_events import ScribeCommandEvent
from palaver.scribe.api import ScribeAPIListener
from palaver.scribe.api import StartBlockCommand, StopBlockCommand

logger = logging.getLogger("DefaultAPIWrapper")


@dataclass
class BlockTracker:
    """Tracks a text block from start to end."""
    start_event: StartBlockCommand
    text_events: dict[uuid.UUID, TextEvent] = field(default_factory=dict)
    end_event: Optional[StopBlockCommand] = None
    finalized: Optional[bool] = False


class DefaultAPIWrapper(ScribeAPIListener):
    """
    Default API wrapper that handles blocks, text events, and optional audio playback.

    This class provides standard handling for:
    - Block tracking (start/stop commands)
    - Text event accumulation
    - Optional audio playback
    - Optional block recorder integration
    """

    def __init__(self, play_sound: bool = False):
        """
        Initialize the API wrapper.

        Args:
            play_sound: If True, play audio through speakers during processing
        """
        super().__init__()
        self.play_sound = play_sound
        self.full_text = ""
        self.blocks = []
        self.text_events = {}
        self.last_block_name = None
        self.stream = None

    async def on_pipeline_ready(self, pipeline):
        """Called when pipeline is ready."""
        pass

    async def on_pipeline_shutdown(self):
        """Handle pipeline shutdown - finalize any open blocks."""
        await asyncio.sleep(0.1)
        if len(self.blocks) > 0:
            last_block = self.blocks[-1]
            if not last_block.finalized:
                await self.finalize_block(last_block)

    async def on_command_event(self, event: ScribeCommandEvent):
        """Handle command events (start/stop block)."""
        print("")
        if isinstance(event.command, StartBlockCommand):
            self.blocks.append(BlockTracker(start_event=event))
            print("-------------------------------------------")
            print(f"DefaultAPIWrapper starting block {len(self.blocks)}")
            print("-------------------------------------------")
            await self.handle_text_event(event.text_event)
        elif isinstance(event.command, StopBlockCommand):
            await self.handle_text_event(event.text_event)
            if len(self.blocks) > 0:
                last_block = self.blocks[-1]
                if not last_block.finalized:
                    last_block.end_event = event
                    await self.finalize_block(last_block)

    async def finalize_block(self, block):
        """Finalize a block and print its contents."""
        print("-------------------------------------------")
        print(f"DefaultAPIWrapper ending block {len(self.blocks)}")
        print("-------------------------------------------")
        print("++++++++++++++++++++++++++++++++++++++++++")
        print("     Full block:")
        print("++++++++++++++++++++++++++++++++++++++++++")
        for text_event in block.text_events.values():
            print(text_event.text)
        print("++++++=++++++++++++++++++++++++++++++++++++")
        block.finalized = True

    async def handle_text_event(self, event: TextEvent):
        """Handle text events - accumulate text and track in blocks."""
        # Fix bug: was `==` should be `in`
        if event.event_id in self.text_events:
            return

        if len(self.blocks) > 0:
            last_block = self.blocks[-1]
            if not last_block.finalized:
                self.text_events[event.event_id] = event
                last_block.text_events[event.event_id] = event
                logger.info(f"text {event.event_id} added to block")
                if logger.isEnabledFor(logging.INFO):
                    logger.info("-----Adding text to block-----\n%s", event.text)
                else:
                    logger.info("-----Adding text to block-----\n")
                    print(event.text)
                    logger.info("----------\n")
                self.full_text += event.text + " "
            else:
                print(f"ignoring text {event.text}")

    async def on_text_event(self, event: TextEvent):
        """Called when new transcribed text is available."""
        await self.handle_text_event(event)

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
