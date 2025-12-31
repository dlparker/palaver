##!/usr/bin/env python
"""
tests/test_websocket_servers.py
Test EventNetServer WebSocket communication between servers
"""

import pytest
import asyncio
import logging
import time
import shutil
import threading
from pathlib import Path
from unittest.mock import patch
from dataclasses import dataclass, field
from typing import Optional
import uuid
import numpy as np
import soundfile as sf
import uvicorn
from rapidfuzz import fuzz

from palaver.scribe.draft_events import DraftEvent, DraftStartEvent, DraftEndEvent, Draft
from palaver.scribe.text_events import TextEvent
from palaver.scribe.api import ScribeAPIListener
from palaver.scribe.audio.mic_listener import MicListener
from palaver.scribe.audio.net_listener import NetListener
from palaver.scribe.core import PipelineConfig
from palaver.scribe.recorders.sql_drafts import SQLDraftRecorder, DraftRecord
from palaver.fastapi.event_server import EventNetServer, ServerMode
from sqlmodel import Session, select, create_engine


logger = logging.getLogger("test_websocket_servers")


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
    finalized: Optional[bool] = False


class APIWrapper(ScribeAPIListener):

    def __init__(self, name="APIWrapper"):
        super().__init__()
        self.name = name
        self.drafts = {}
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


async def test_direct_to_remote_websocket():
    """Test EventNetServer direct mode sending events to remote mode via WebSocket."""

    # Setup test audio
    audio_file = Path(__file__).parent / "audio_samples" / "note1.wav"
    assert audio_file.exists(), f"Test audio file not found: {audio_file}"
    model = Path(__file__).parent.parent / "models" / "ggml-base.en.bin"
    assert model.exists(), f"Model file not found: {model}"

    logger.info(f"TESTING WebSocket Communication: Direct → Remote")

    # Setup recorder directories
    source_dir = Path(__file__).parent / "recorder_output_source"
    consumer_dir = Path(__file__).parent / "recorder_output_consumer"
    if source_dir.exists():
        shutil.rmtree(source_dir)
    if consumer_dir.exists():
        shutil.rmtree(consumer_dir)

    # Track state
    source_api = APIWrapper(name="SOURCE")
    consumer_api = APIWrapper(name="CONSUMER")
    mock_instance = None

    def create_mock_stream(*args, **kwargs):
        nonlocal mock_instance
        mock_instance = MockStream(*args, **kwargs)
        mock_instance.audio_file = audio_file
        mock_instance.simulate_timing = False
        logger.info(f"MockStream created for source server")
        return mock_instance

    with patch('sounddevice.Stream', side_effect=create_mock_stream):
        # Create source server (direct mode)
        source_listener = MicListener(chunk_duration=0.03)
        source_config = PipelineConfig(
            model_path=model,
            api_listener=source_api,
            target_samplerate=16000,
            target_channels=1,
            use_multiprocessing=True,
            vad_silence_ms=3000,
            vad_speech_pad_ms=1000,
            seconds_per_scan=2,
            whisper_shutdown_timeout=1.0,
        )
        source_recorder = SQLDraftRecorder(source_dir, enable_file_storage=False)
        source_server = EventNetServer(
            audio_listener=source_listener,
            pipeline_config=source_config,
            draft_recorder=source_recorder,
            port=9090,
            mode=ServerMode.direct
        )
        logger.info("Source server created (port 8000, direct mode)")

        # Create consumer server (remote mode)
        consumer_listener = NetListener(
            audio_url="ws://localhost:9090",
            audio_only=False,  # Subscribe to text and draft events too
            chunk_duration=0.03
        )
        consumer_config = PipelineConfig(
            model_path=model,
            api_listener=consumer_api,
            target_samplerate=16000,
            target_channels=1,
            use_multiprocessing=True,
            vad_silence_ms=3000,
            vad_speech_pad_ms=1000,
            seconds_per_scan=2,
            whisper_shutdown_timeout=1.0,
        )
        consumer_recorder = SQLDraftRecorder(consumer_dir, enable_file_storage=False)
        consumer_server = EventNetServer(
            audio_listener=consumer_listener,
            pipeline_config=consumer_config,
            draft_recorder=consumer_recorder,
            port=9091,
            mode=ServerMode.remote
        )
        logger.info("Consumer server created (port 9091, remote mode)")

        # Create uvicorn servers
        source_uvicorn = uvicorn.Server(
            uvicorn.Config(
                app=source_server.app,
                host="127.0.0.1",
                port=9090,
                log_level="warning"
            )
        )
        consumer_uvicorn = uvicorn.Server(
            uvicorn.Config(
                app=consumer_server.app,
                host="127.0.0.1",
                port=9091,
                log_level="warning"
            )
        )

        # Run servers concurrently
        async def run_servers():
            logger.info("Starting source server...")
            source_task = asyncio.create_task(source_uvicorn.serve())
            await asyncio.sleep(0.5)  # Let source start first

            logger.info("Starting consumer server...")
            consumer_task = asyncio.create_task(consumer_uvicorn.serve())
            await asyncio.sleep(0.5)  # Let consumer connect

            logger.info("Servers started, waiting for mock to be created...")

            # Monitor for completion
            while mock_instance is None:
                await asyncio.sleep(0.01)

            logger.info("MockStream created, waiting for audio feed to complete...")
            while mock_instance.running:
                await asyncio.sleep(0.1)

            logger.info("Audio feed complete, waiting for drafts to be processed...")

            # Wait for both to process draft
            start_time = time.time()
            while time.time() - start_time < 8:
                source_done = len(source_api.drafts) > 0 and \
                             any(dt.draft.end_text for dt in source_api.drafts.values())
                consumer_done = len(consumer_api.drafts) > 0 and \
                               any(dt.draft.end_text for dt in consumer_api.drafts.values())

                if source_done and consumer_done:
                    logger.info("Both servers have completed drafts!")
                    break

                await asyncio.sleep(0.1)

            if not source_done:
                logger.warning("Source server did not complete draft")
            if not consumer_done:
                logger.warning("Consumer server did not complete draft")

            # Shutdown: consumer first, then source
            logger.info("Shutting down consumer server...")
            await consumer_server.shutdown()  # Stop pipeline
            consumer_uvicorn.should_exit = True  # Signal uvicorn to exit
            await asyncio.sleep(0.2)

            logger.info("Shutting down source server...")
            await source_server.shutdown()  # Stop pipeline
            source_uvicorn.should_exit = True  # Signal uvicorn to exit

            # Wait for servers to stop (with timeout)
            logger.info("Waiting for uvicorn servers to stop...")
            try:
                await asyncio.wait_for(
                    asyncio.gather(source_task, consumer_task, return_exceptions=True),
                    timeout=2.0  # 2 second timeout
                )
                logger.info("Servers stopped cleanly")
            except asyncio.TimeoutError:
                logger.warning("Uvicorn servers didn't stop within timeout, cancelling tasks")
                source_task.cancel()
                consumer_task.cancel()
                await asyncio.gather(source_task, consumer_task, return_exceptions=True)
                logger.info("Server tasks cancelled")

        await run_servers()

    # Verify both servers received and processed drafts
    assert len(source_api.drafts) == 1, f"Expected 1 source draft, got {len(source_api.drafts)}"
    assert len(consumer_api.drafts) == 1, f"Expected 1 consumer draft, got {len(consumer_api.drafts)}"

    source_draft = next(iter(source_api.drafts.values())).draft
    consumer_draft = next(iter(consumer_api.drafts.values())).draft

    logger.info(f"Source draft: '{source_draft.full_text}'")
    logger.info(f"Consumer draft: '{consumer_draft.full_text}'")

    # Consumer should have received similar transcription (fuzzy match due to whisper variance)
    similarity = fuzz.ratio(source_draft.full_text, consumer_draft.full_text)
    logger.info(f"Draft similarity: {similarity}%")
    assert similarity >= 75, \
        f"Drafts too different (similarity={similarity}%): source='{source_draft.full_text}' consumer='{consumer_draft.full_text}'"

    # Verify source saved to database
    source_db = source_dir / "drafts.db"
    assert source_db.exists(), f"Source database not found at {source_db}"
    engine = create_engine(f"sqlite:///{source_db}")
    with Session(engine) as session:
        statement = select(DraftRecord).where(DraftRecord.draft_id == str(source_draft.draft_id))
        source_record = session.exec(statement).first()
        assert source_record is not None, f"Source draft {source_draft.draft_id} not found in database"
        assert source_record.full_text == source_draft.full_text
        logger.info(f"Source database verified: '{source_record.full_text}'")

    # Verify consumer saved to database
    consumer_db = consumer_dir / "drafts.db"
    assert consumer_db.exists(), f"Consumer database not found at {consumer_db}"
    engine = create_engine(f"sqlite:///{consumer_db}")
    with Session(engine) as session:
        statement = select(DraftRecord).where(DraftRecord.draft_id == str(consumer_draft.draft_id))
        consumer_record = session.exec(statement).first()
        assert consumer_record is not None, f"Consumer draft {consumer_draft.draft_id} not found in database"
        assert consumer_record.full_text == consumer_draft.full_text
        logger.info(f"Consumer database verified: '{consumer_record.full_text}'")

    # Verify API listeners received lifecycle events
    assert source_api.have_pipeline_ready, "Source pipeline never reported ready"
    assert source_api.have_pipeline_shutdown, "Source pipeline never reported shutdown"
    assert consumer_api.have_pipeline_ready, "Consumer pipeline never reported ready"
    assert consumer_api.have_pipeline_shutdown, "Consumer pipeline never reported shutdown"

    logger.info("✅ WebSocket communication test passed!")

    # Cleanup
    shutil.rmtree(source_dir)
    shutil.rmtree(consumer_dir)
