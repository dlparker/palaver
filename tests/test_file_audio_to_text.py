#!/usr/bin/env python
"""
tests/test_vad_recorder_file.py
Test VAD recorder with pre-recorded audio files
"""

import pytest
import asyncio
import sys
import os
import time
import uuid
from pprint import pprint
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
from palaver.scribe.draft_events import DraftEvent, DraftStartEvent, DraftEndEvent, Draft
from palaver.scribe.recorders.draft_recorder import DraftRecorder
from palaver.scribe.text_events import TextEvent
from palaver.scribe.api import ScribeAPIListener
from palaver.scribe.audio.file_listener import FileListener
from palaver.scribe.core import PipelineConfig, ScribePipeline


logger = logging.getLogger("test_code")

@dataclass
class DraftTracker:
    draft: Draft
    start_event: DraftStartEvent
    text_events: dict[uuid.UUID, TextEvent] = field(default_factory=dict)
    end_event: Optional[DraftEndEvent] = None
    finalized: Optional[bool] = False


class APIWrapper(ScribeAPIListener):

    def __init__(self):
        super().__init__()
        self.drafts = {}
        self.pipeline = None
        self.have_pipeline_ready = False
        self.have_pipeline_shutdown = False

    async def on_pipeline_ready(self, pipeline):
        self.have_pipeline_ready = True
        self.pipeline = pipeline
        
    async def on_pipeline_shutdown(self):
        self.have_pipeline_shutdown = True
    
    async def on_text_event(self, event: TextEvent):
        print(event.text)
        
    async def on_draft_event(self, event: DraftEvent):
        if isinstance(event, DraftStartEvent):
            self.drafts[event.draft.draft_id] = DraftTracker(event.draft, start_event=event)
        elif isinstance(event, DraftEndEvent):
            pprint(event.draft)
            if event.draft.draft_id in self.drafts:
                self.drafts[event.draft.draft_id].draft = event.draft
                self.drafts[event.draft.draft_id].end_event = event
            else:
                self.drafts[event.draft.draft_id] = DraftTracker(event.draft, start_event=None, end_event=event)
                


CHUNK_SEC = 0.03

    
async def test_process_note1_file():
    # Verify test file exists
    audio_file = Path(__file__).parent / "audio_samples" / "note1.wav"
    assert audio_file.exists()
    model = Path(__file__).parent.parent / "models" / "ggml-base.en.bin"
    assert model.exists()
    logger.info(f"TESTING FILE INPUT: {audio_file}")
    api_wrapper = APIWrapper()
    recorder_dir = Path(__file__).parent / "recorder_output"
    # clean it up before running
    if recorder_dir.exists():
        shutil.rmtree(recorder_dir)
        
    draft_recorder = DraftRecorder(recorder_dir)
    logger.info(f"Draft recorder enabled: {recorder_dir}")
    
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
            vad_silence_ms=3000,
            vad_speech_pad_ms=1000,
            seconds_per_scan=2,
        )

        # Run pipeline with automatic context management
        async with file_listener:
            async with ScribePipeline(file_listener, config) as pipeline:
                pipeline.add_api_listener(draft_recorder)
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
    start_time = time.time()
    while len(api_wrapper.drafts) < 1 and time.time() - start_time < 5:
        await asyncio.sleep(0.1)
    assert len(api_wrapper.drafts)  == 1
    assert api_wrapper.have_pipeline_ready
    assert api_wrapper.have_pipeline_shutdown
    out_dir = list(recorder_dir.glob("draft-*"))[0]
    with open(out_dir / "first_draft.txt") as f:
        file_draft = f.read()
    dt = next(iter(api_wrapper.drafts.values()))
    draft = dt.draft
    assert draft.full_text != ""
    assert draft.full_text == file_draft
    shutil.rmtree(recorder_dir)

async def test_process_open_draft_file():
    # Verify test file exists
    audio_file = Path(__file__).parent / "audio_samples" / "open_draft.wav"
    assert audio_file.exists()
    model = Path(__file__).parent.parent / "models" / "ggml-base.en.bin"
    assert model.exists()
    logger.info(f"TESTING FILE INPUT: {audio_file}")
    api_wrapper = APIWrapper()
    recorder_dir = Path(__file__).parent / "recorder_output"
    # clean it up before running
    if recorder_dir.exists():
        shutil.rmtree(recorder_dir)
        
    draft_recorder = DraftRecorder(recorder_dir)
    logger.info(f"Draft recorder enabled: {recorder_dir}")
    
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
            vad_silence_ms=3000,
            vad_speech_pad_ms=1000,
            seconds_per_scan=2,
        )

        # Run pipeline with automatic context management
        async with file_listener:
            async with ScribePipeline(file_listener, config) as pipeline:
                pipeline.add_api_listener(draft_recorder)
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
    start_time = time.time()
    while len(api_wrapper.drafts) < 1 and time.time() - start_time < 5:
        await asyncio.sleep(0.1)
    assert len(api_wrapper.drafts)  == 1
    assert api_wrapper.have_pipeline_ready
    assert api_wrapper.have_pipeline_shutdown
    out_dir = list(recorder_dir.glob("draft-*"))[0]
    with open(out_dir / "first_draft.txt") as f:
        file_draft = f.read()
    dt = next(iter(api_wrapper.drafts.values()))
    draft = dt.draft
    assert draft.full_text != ""
    assert draft.full_text == file_draft
    shutil.rmtree(recorder_dir)
    
