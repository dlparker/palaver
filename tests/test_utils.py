#!/usr/bin/env python
"""
tests/test_utils.py
Common test utilities for EventNetServer tests
"""

import logging
import time
import threading
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional
import uuid
import numpy as np
import soundfile as sf

from palaver.scribe.draft_events import DraftEvent, DraftStartEvent, DraftEndEvent, DraftRescanEvent, Draft
from palaver.scribe.text_events import TextEvent
from palaver.scribe.api import ScribeAPIListener


logger = logging.getLogger("test_utils")


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
        self.running = False


@dataclass
class DraftTracker:
    draft: Draft
    start_event: DraftStartEvent
    text_events: dict[uuid.UUID, TextEvent] = field(default_factory=dict)
    end_event: Optional[DraftEndEvent] = None
    rescan_event: Optional[DraftRescanEvent] = None
    finalized: Optional[bool] = False


class APIWrapper(ScribeAPIListener):

    def __init__(self, name="APIWrapper"):
        super().__init__()
        self.name = name
        self.drafts = {}
        self.rescanned_drafts = {}  # Track rescanned drafts separately
        self.have_pipeline_ready = False
        self.pipeline = None
        self.have_pipeline_shutdown = False

    async def on_pipeline_ready(self, pipeline):
        self.have_pipeline_ready = True
        self.pipeline = pipeline
        logger.info(f"{self.name}: Pipeline ready")

    async def on_pipeline_shutdown(self):
        self.have_pipeline_shutdown = True
        logger.info(f"{self.name}: Pipeline shutdown")

    async def on_text_event(self, event: TextEvent):
        logger.info(f"{self.name}: Text event: {event.text}")

    async def on_draft_event(self, event: DraftEvent):
        if isinstance(event, DraftStartEvent):
            logger.info(f"{self.name}: Draft start")
            self.drafts[event.draft.draft_id] = DraftTracker(event.draft, start_event=event)
        elif isinstance(event, DraftEndEvent):
            logger.info(f"{self.name}: Draft end: {event.draft.full_text}")
            if event.draft.draft_id in self.drafts:
                self.drafts[event.draft.draft_id].draft = event.draft
                self.drafts[event.draft.draft_id].end_event = event
            else:
                self.drafts[event.draft.draft_id] = DraftTracker(event.draft, start_event=None, end_event=event)
        elif isinstance(event, DraftRescanEvent):
            logger.info(f"{self.name}: Draft rescan: parent={event.draft.parent_draft_id}, text={event.draft.full_text}")
            # Track rescanned drafts by parent_draft_id
            if event.draft.parent_draft_id:
                self.rescanned_drafts[event.draft.parent_draft_id] = event.draft
            # Also track as a regular draft
            if event.draft.draft_id in self.drafts:
                self.drafts[event.draft.draft_id].draft = event.draft
                self.drafts[event.draft.draft_id].rescan_event = event
            else:
                self.drafts[event.draft.draft_id] = DraftTracker(
                    event.draft,
                    start_event=None,
                    end_event=None,
                    rescan_event=event
                )
