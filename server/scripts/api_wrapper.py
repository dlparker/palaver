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

    def __init__(self, draft_recorder = None, play_sound: bool = False, play_signals:bool = True):
        super().__init__()
        self.play_sound = play_sound
        self.play_signals = play_signals
        self.full_text = ""
        self.blocks = []
        self.text_events = {}
        self.last_block_name = None
        self.stream = None
        self.start_time = time.time()
        self.draft_recorder = draft_recorder

    async def on_pipeline_ready(self, pipeline):
        if self.draft_recorder:
            pipeline.add_api_listener(self.draft_recorder)

    async def on_pipeline_shutdown(self):
        pass

    async def on_draft_event(self, event: DraftEvent):
        now = time.time() 
        et = now - self.start_time
        if isinstance(event, DraftStartEvent):
            print(f"\n\n{now} - {et:7.4}: New draft\n\n")
            self.current_draft = event.draft
            await self.play_draft_signal("new draft")
        if isinstance(event, DraftEndEvent):
            print(f"\n\n{now} - {et:7.4} Finished draft\n\n")
            self.current_draft = None
            print('-'*100)
            print(event.draft.full_text)
            print('-'*100)
            await self.play_draft_signal("end draft")
            
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

    async def play_draft_signal(self, kind: str):
        if kind == "new draft":
            file_path = Path(__file__).parent.parent / "signal_sounds" / "tos-computer-06.mp3"
        else:
            file_path = Path(__file__).parent.parent / "signal_sounds" / "tos-computer-03.mp3"
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
