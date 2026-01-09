#!/usr/bin/env python
"""
tests/test_mic_mock_to_text.py
Test MicListener with mocked sounddevice reading from pre-recorded audio files
"""

import pytest
import asyncio
import sys
import os
import uuid
import threading
import time
from pprint import pprint
import numpy as np
import soundfile as sf
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import patch
import json
import logging
import shutil
from typing import Optional

from palaver_shared.audio_events import (AudioEvent,
                                         AudioErrorEvent,
                                         AudioStartEvent,
                                         AudioStopEvent,
                                         AudioSpeechStartEvent,
                                         AudioSpeechStopEvent,
                                         AudioChunkEvent,
                                         AudioEventListener,
                                         )
from palaver_shared.top_error import TopErrorHandler, TopLevelCallback
from palaver_shared.draft_events import DraftEvent, DraftStartEvent, DraftEndEvent, Draft
from palaver.scribe.recorders.sql_drafts import SQLDraftRecorder
from palaver_shared.text_events import TextEvent
from palaver.scribe.api import ScribeAPIListener
from palaver.scribe.audio.mic_listener import MicListener
from palaver.scribe.core import PipelineConfig, ScribePipeline


logger = logging.getLogger("test_code")


class MockStream:
    """
    Mock sounddevice.Stream that reads from a file.

    Simulates the threading behavior of real sounddevice by running
    a background thread that feeds audio chunks to the callback.
    """

    def __init__(self, blocksize, callback, dtype='float32', channels=None, **kwargs):
        # sd.Stream uses default device properties if not specified
        self.samplerate = kwargs.get('samplerate', 44100)  # Default device samplerate
        # sd.Stream returns channels as a 2-tuple for duplex streams: (input_channels, output_channels)
        # For stream with channels=1, real sd.Stream returns (1, 1)
        if channels is not None and not isinstance(channels, tuple):
            self.channels = (channels, channels)  # Duplex stream with same channel count
        elif channels is None:
            self.channels = (1, 1)  # Default duplex
        else:
            self.channels = channels
        self.device = kwargs.get('device', None)
        self.callback = callback
        self.blocksize = blocksize
        self.dtype = dtype
        self.running = False
        self._thread = None
        self.audio_file = None  # Set this before start()
        self.simulate_timing = False  # Set True to simulate real-time playback

    def start(self):
        """Start feeding audio data to the callback."""
        self.running = True
        self._thread = threading.Thread(target=self._feed_audio, daemon=True)
        self._thread.start()

    def stop(self):
        """Stop feeding audio data."""
        self.running = False
        if self._thread:
            self._thread.join(timeout=1.0)

    def close(self):
        """Close the stream."""
        self.stop()

    def __enter__(self):
        """Context manager entry - start the stream."""
        self.start()
        return self

    def __exit__(self, *args):
        """Context manager exit - stop the stream."""
        self.stop()

    def _feed_audio(self):
        """
        Read from file and call callback with chunks.
        Runs in a separate thread, just like real sounddevice.
        """
        if self.audio_file is None:
            logger.warning("MockInputStream: no audio file set")
            return

        # Read entire file
        data, sr = sf.read(self.audio_file, dtype=self.dtype, always_2d=True)
        logger.info(f"MockInputStream: loaded {len(data)} samples at {sr}Hz from {self.audio_file}")

        # Handle mono/stereo conversion to match requested channels
        if len(data.shape) == 1:
            data = data.reshape(-1, 1)
        target_channels = self.channels[0]  # Input channels from tuple
        if data.shape[1] != target_channels:
            if target_channels == 1:
                # Convert stereo to mono
                data = data.mean(axis=1, keepdims=True)
            else:
                # Convert mono to stereo
                data = np.column_stack([data, data])

        # Resample if file sample rate doesn't match target
        if sr != self.samplerate:
            logger.info(f"MockInputStream: resampling from {sr}Hz to {self.samplerate}Hz")
            # Resample each channel
            import resampy
            resampled_data = np.zeros((int(len(data) * self.samplerate / sr), data.shape[1]), dtype=self.dtype)
            for ch in range(data.shape[1]):
                resampled_data[:, ch] = resampy.resample(data[:, ch], sr, self.samplerate, filter='kaiser_fast')
            data = resampled_data
            logger.info(f"MockInputStream: resampled to {len(data)} samples")

        # Feed chunks to callback (simulating sounddevice behavior)
        chunk_count = 0
        for i in range(0, len(data), self.blocksize):
            if not self.running:
                logger.info(f"MockInputStream: stopped after {chunk_count} chunks")
                break

            chunk = data[i:i+self.blocksize]

            # Pad last chunk if needed
            if len(chunk) < self.blocksize:
                pad_width = ((0, self.blocksize - len(chunk)), (0, 0))
                chunk = np.pad(chunk, pad_width, mode='constant', constant_values=0)

            # Call the callback with (indata, outdata, frames, time_info, status)
            # This is the signature sd.Stream uses (duplex callback)
            self.callback(chunk, None, self.blocksize, None, None)
            chunk_count += 1

            # Optional: simulate real-time timing
            if self.simulate_timing:
                time.sleep(self.blocksize / self.samplerate)

        logger.info(f"MockInputStream: finished feeding {chunk_count} chunks")

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
        self.have_pipeline_ready = False
        self.pipeline = None
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


async def test_process_note1_mic_mock():
    # Verify test file exists
    audio_file = Path(__file__).parent / "audio_samples" / "note1.wav"
    assert audio_file.exists()
    model = Path(__file__).parent.parent / "models" / "ggml-base.en.bin"
    assert model.exists()
    logging.info(f"TESTING MIC MOCK INPUT: {audio_file}")
    api_wrapper = APIWrapper()
    recorder_dir = Path(__file__).parent / "recorder_output_mic_mock"
    # clean it up before running
    if recorder_dir.exists():
        shutil.rmtree(recorder_dir)


    async def main_task(model):
        # Store reference to mock instance for monitoring
        mock_instance = None

        # Factory function to create mock with proper parameters from MicListener
        def create_mock_stream(*args, **kwargs):
            """Create MockStream with actual parameters from MicListener."""
            nonlocal mock_instance
            mock_instance = MockStream(*args, **kwargs)
            mock_instance.audio_file = audio_file
            mock_instance.simulate_timing = False  # Fast playback for tests
            logger.info(f"MockStream created with samplerate={mock_instance.samplerate}, "
                       f"channels={mock_instance.channels}, blocksize={mock_instance.blocksize}")
            return mock_instance

        # Patch sounddevice.Stream directly at source
        with patch('sounddevice.Stream', side_effect=create_mock_stream):
            # Create MicListener - it will use our mock factory
            mic_listener = MicListener(chunk_duration=0.03)

            # Create pipeline config with same settings as FileListener test
            config = PipelineConfig(
                model_path=model,
                api_listener=api_wrapper,
                target_samplerate=16000,
                target_channels=1,
                use_multiprocessing=False,
                vad_silence_ms=3000,
                vad_speech_pad_ms=1000,
                seconds_per_scan=2,
            )

            draft_recorder = SQLDraftRecorder(recorder_dir, enable_file_storage=True)
            logger.info(f"Draft recorder enabled: {recorder_dir}")
            # Run pipeline with automatic context management
            async with mic_listener:
                async with ScribePipeline(mic_listener, config) as pipeline:
                    await pipeline.add_api_listener(draft_recorder)
                    await pipeline.start_listener()

                    # Monitor mock and stop listener when done feeding data
                    async def monitor_and_stop():
                        """Wait for mock to finish, then stop the listener."""
                        # Wait for mock to be created
                        while mock_instance is None:
                            await asyncio.sleep(0.01)

                        # Wait for mock to finish feeding all chunks
                        while mock_instance.running:
                            await asyncio.sleep(0.1)

                        # Give extra time for transcription to complete,
                        # on laptop it takes a few seconds

                        def check_done():
                            if len(api_wrapper.drafts) == 0:
                                return False
                            dt = next(iter(api_wrapper.drafts.values()))
                            if dt.draft.end_text:
                                return True
                            return False
                        start_time = time.time()
                        while time.time() - start_time < 7 and not check_done():
                            await asyncio.sleep(0.1)
                        if not check_done():
                            await mic_listener.stop_streaming()
                            raise Exception('never got block')
                        logger.info("Mock finished feeding data, stopping listener")
                        await mic_listener.stop_streaming()

                    # Run monitoring task and pipeline concurrently
                    monitor_task = asyncio.create_task(monitor_and_stop())
                    try:
                        await pipeline.run_until_error_or_interrupt()
                    finally:
                        # Clean up monitor task if it's still running
                        if not monitor_task.done():
                            monitor_task.cancel()
                            try:
                                await monitor_task
                            except asyncio.CancelledError:
                                pass

    background_error_dict = None
    class ErrorCallback(TopLevelCallback):
        async def on_error(self, error_dict: dict):
            nonlocal background_error_dict
            background_error_dict = error_dict

    # Run with standard error handling
    handler = TopErrorHandler(top_level_callback=ErrorCallback(), logger=logger)
    await handler.async_run(main_task, model)

    assert background_error_dict is None
    assert len(api_wrapper.drafts) == 1
    assert api_wrapper.have_pipeline_ready
    assert api_wrapper.have_pipeline_shutdown
    out_dir = list(recorder_dir.glob("draft-*"))[0]
    with open(out_dir / "first_draft.txt") as f:
        file_text = f.read()

    dt = next(iter(api_wrapper.drafts.values()))
    draft = dt.draft
    assert draft.full_text.strip() == file_text.strip()
    shutil.rmtree(recorder_dir)
