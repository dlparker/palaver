#!/usr/bin/env python
"""
tests/test_vad_recorder_file.py
Test VAD recorder with pre-recorded audio files
"""

import pytest
import asyncio
import sys
import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path
import json
import logging
import shutil
from typing import Optional

from palaver.scribe.audio_events import (AudioEvent,
                                         AudioErrorEvent,
                                         AudioStartEvent,
                                         AudioStopEvent,
                                         AudioSpeechStartEvent,
                                         AudioSpeechStopEvent,
                                         AudioChunkEvent,
                                         AudioEventListener,
                                         )
from palaver.utils.top_error import TopErrorHandler, TopLevelCallback
from palaver.scribe.command_events import ScribeCommandEvent
from palaver.scribe.text_events import TextEvent
from palaver.scribe.api import ScribeAPIListener
from palaver.scribe.api import StartBlockCommand, StopBlockCommand
from palaver.scribe.audio.file_listener import FileListener
from palaver.scribe.recorders.block_audio import BlockAudioRecorder
from palaver.scribe.core import PipelineConfig, ScribePipeline


logger = logging.getLogger("test_code")

@dataclass
class BlockTracker:
    """Tracks a text block from start to end."""
    start_event: StartBlockCommand
    text_events: dict[uuid.UUID, TextEvent] = field(default_factory=dict)
    end_event: Optional[StopBlockCommand] = None
    finalized: Optional[bool] = False


class APIWrapper(ScribeAPIListener):

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
        self.have_pipeline_ready = False
        self.pipeline = None
        self.have_pipeline_shutdown = False

    async def on_pipeline_ready(self, pipeline):
        self.have_pipeline_ready = True
        self.pipeline = pipeline
        
    async def on_pipeline_shutdown(self):
        """Handle pipeline shutdown - finalize any open blocks."""
        self.have_pipeline_shutdown = True
        await asyncio.sleep(0.01)
        if len(self.blocks) > 0:
            last_block = self.blocks[-1]
            if not last_block.finalized:
                await self.finalize_block(last_block)

    async def on_command_event(self, event: ScribeCommandEvent):
        """Handle command events (start/stop block)."""
        if isinstance(event.command, StartBlockCommand):
            self.blocks.append(BlockTracker(start_event=event))
            await self.handle_text_event(event.text_event)
        elif isinstance(event.command, StopBlockCommand):
            if len(self.blocks) > 0:
                last_block = self.blocks[-1]
                if not last_block.finalized:
                    last_block.end_event = event
                    await self.finalize_block(last_block)

    async def finalize_block(self, block):
        block.finalized = True

    async def handle_text_event(self, event: TextEvent):
        """Handle text events - accumulate text and track in blocks."""
        # Fix bug: was `==` should be `in`
        if event.event_id in self.text_events:
            return
        self.text_events[event.event_id] = event

        if len(self.blocks) > 0:
            last_block = self.blocks[-1]
            if not last_block.finalized:
                last_block.text_events[event.event_id] = event
                logger.info(f"text {event.event_id} added to block")
                self.full_text += event.text + " "
            else:
                logger.info(f"ignoring text {event.text}")

    async def on_text_event(self, event: TextEvent):
        """Called when new transcribed text is available."""
        await self.handle_text_event(event)

    async def on_audio_event(self, event: AudioEvent):
        """Handle audio events - optionally play sound and finalize blocks."""
        if isinstance(event, AudioStartEvent):
            pass
        elif isinstance(event, AudioStopEvent):
            logger.info("Got audio stop event %s", event)
            if len(self.blocks) > 0:
                last_block = self.blocks[-1]
                if not last_block.finalized:
                    await self.finalize_block(last_block)
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
                audio = event.data
                self.stream.write(audio)

CHUNK_SEC = 0.03

    
async def test_process_note1_file():
    # Verify test file exists
    audio_file = Path(__file__).parent / "audio_samples" / "note1.wav"
    assert audio_file.exists()
    model = Path(__file__).parent.parent / "models" / "ggml-base.en.bin"
    assert model.exists()
    logging.info(f"TESTING FILE INPUT: {audio_file}")
    api_wrapper = APIWrapper()
    recorder_dir = Path(__file__).parent / "recorder_output"
    # clean it up before running
    if recorder_dir.exists():
        shutil.rmtree(recorder_dir)
        
    chunk_ring_seconds = 12
    block_recorder = BlockAudioRecorder(recorder_dir, chunk_ring_seconds)
    
    async def main_task(model, file_path):
        # Create listener
        file_listener = FileListener(
            audio_file=file_path,
            chunk_duration=0.03,
            simulate_timing=False,
        )

        # Create pipeline config with playback-specific settings
        config = PipelineConfig(
            model_path=model,
            api_listener=api_wrapper,
            target_samplerate=16000,
            target_channels=1,
            use_multiprocessing=True,
            require_command_alerts=False,
            vad_silence_ms=3000,
            vad_speech_pad_ms=1000,
            seconds_per_scan=2,
            block_recorder=block_recorder,
        )

        # Run pipeline with automatic context management
        async with file_listener:
            async with ScribePipeline(file_listener, config) as pipeline:
                await pipeline.start_listener()
                await pipeline.run_until_error_or_interrupt()

    background_error_dict = None
    class ErrorCallback(TopLevelCallback):
        async def on_error(self, error_dict: dict):
            nonlocal background_error_dict
            background_error_dict = error_dict

    # Run with standard error handling
    handler = TopErrorHandler(top_level_callback=ErrorCallback(), logger=logger)
    await handler.async_run(main_task, model, audio_file)

    assert background_error_dict is None
    assert len(api_wrapper.blocks) == 1
    assert api_wrapper.have_pipeline_ready
    assert api_wrapper.have_pipeline_shutdown
    assert api_wrapper.full_text != ""
    out_dir = list(recorder_dir.glob("block-*"))[0]
    with open(out_dir / "first_draft.txt") as f:
        draft = f.read()

    assert draft.strip().startswith(api_wrapper.full_text.strip())
    shutil.rmtree(recorder_dir)
