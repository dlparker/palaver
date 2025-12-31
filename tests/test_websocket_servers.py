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
from pathlib import Path
from unittest.mock import patch
import uvicorn
from rapidfuzz import fuzz

from palaver.scribe.audio.mic_listener import MicListener
from palaver.scribe.audio.net_listener import NetListener
from palaver.scribe.core import PipelineConfig
from palaver.scribe.recorders.sql_drafts import SQLDraftRecorder, DraftRecord
from palaver.fastapi.event_server import EventNetServer, ServerMode
from sqlmodel import Session, select, create_engine
from tests.test_utils import MockStream, APIWrapper


logger = logging.getLogger("test_websocket_servers")


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


async def test_direct_to_rescan_websocket():
    """Test EventNetServer direct mode with rescan mode server."""

    # Setup test audio
    audio_file = Path(__file__).parent / "audio_samples" / "note1.wav"
    assert audio_file.exists(), f"Test audio file not found: {audio_file}"
    model = Path(__file__).parent.parent / "models" / "ggml-base.en.bin"
    assert model.exists(), f"Model file not found: {model}"

    logger.info(f"TESTING WebSocket Communication: Direct → Rescan")

    # Setup recorder directory (only for source in direct mode)
    source_dir = Path(__file__).parent / "recorder_output_source_rescan"
    if source_dir.exists():
        shutil.rmtree(source_dir)

    # Track state
    source_api = APIWrapper(name="SOURCE")
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
            seconds_per_scan=2,  # Normal window for source
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
        logger.info("Source server created (port 9090, direct mode)")

        # Create rescan server (rescan mode)
        # Note: rescan server uses NetListener to receive events from source
        rescan_listener = NetListener(
            audio_url="ws://localhost:9090",
            audio_only=False,  # Subscribe to all events
            chunk_duration=0.03
        )
        rescan_config = PipelineConfig(
            model_path=model,
            api_listener=None,  # Rescan mode uses RescannerLocal internally
            target_samplerate=16000,
            target_channels=1,
            use_multiprocessing=True,
            vad_silence_ms=3000,
            vad_speech_pad_ms=1000,
            seconds_per_scan=15,  # Larger window for rescanning
            whisper_shutdown_timeout=1.0,
        )
        # Rescan mode doesn't use draft_recorder (drafts sent back to source)
        rescan_recorder = SQLDraftRecorder(Path("/tmp/unused"), enable_file_storage=False)
        rescan_server = EventNetServer(
            audio_listener=rescan_listener,
            pipeline_config=rescan_config,
            draft_recorder=rescan_recorder,
            port=9092,
            mode=ServerMode.rescan
        )
        logger.info("Rescan server created (port 9092, rescan mode)")

        # Create uvicorn servers
        source_uvicorn = uvicorn.Server(
            uvicorn.Config(
                app=source_server.app,
                host="127.0.0.1",
                port=9090,
                log_level="warning"
            )
        )
        rescan_uvicorn = uvicorn.Server(
            uvicorn.Config(
                app=rescan_server.app,
                host="127.0.0.1",
                port=9092,
                log_level="warning"
            )
        )

        # Run servers concurrently
        async def run_servers():
            logger.info("Starting source server...")
            source_task = asyncio.create_task(source_uvicorn.serve())
            await asyncio.sleep(0.5)  # Let source start first

            logger.info("Starting rescan server...")
            rescan_task = asyncio.create_task(rescan_uvicorn.serve())
            await asyncio.sleep(0.5)  # Let rescan connect

            logger.info("Servers started, waiting for mock to be created...")

            # Monitor for completion
            while mock_instance is None:
                await asyncio.sleep(0.01)

            logger.info("MockStream created, waiting for audio feed to complete...")
            while mock_instance.running:
                await asyncio.sleep(0.1)

            logger.info("Audio feed complete, waiting for drafts to be processed...")

            # Wait for source to receive rescanned draft
            # This may take longer due to 15-second window in rescan mode
            start_time = time.time()
            while time.time() - start_time < 20:  # Longer timeout for rescan
                source_has_original = len(source_api.drafts) > 0 and \
                                    any(dt.draft.end_text for dt in source_api.drafts.values())
                source_has_rescan = len(source_api.rescanned_drafts) > 0

                if source_has_original and source_has_rescan:
                    logger.info("Source has both original and rescanned drafts!")
                    break

                await asyncio.sleep(0.1)

            if not source_has_original:
                logger.warning("Source server did not complete original draft")
            if not source_has_rescan:
                logger.warning("Source server did not receive rescanned draft")

            # Shutdown: rescan first, then source
            logger.info("Shutting down rescan server...")
            await rescan_server.shutdown()  # Stop pipeline
            rescan_uvicorn.should_exit = True  # Signal uvicorn to exit
            await asyncio.sleep(0.2)

            logger.info("Shutting down source server...")
            await source_server.shutdown()  # Stop pipeline
            source_uvicorn.should_exit = True  # Signal uvicorn to exit

            # Wait for servers to stop (with timeout)
            logger.info("Waiting for uvicorn servers to stop...")
            try:
                await asyncio.wait_for(
                    asyncio.gather(source_task, rescan_task, return_exceptions=True),
                    timeout=2.0  # 2 second timeout
                )
                logger.info("Servers stopped cleanly")
            except asyncio.TimeoutError:
                logger.warning("Uvicorn servers didn't stop within timeout, cancelling tasks")
                source_task.cancel()
                rescan_task.cancel()
                await asyncio.gather(source_task, rescan_task, return_exceptions=True)
                logger.info("Server tasks cancelled")

        await run_servers()

    # Verify source received original and rescanned drafts
    assert len(source_api.drafts) == 2, f"Expected 2 source draft, got {len(source_api.drafts)}"

    # Get original draft
    original_draft = next(iter(source_api.drafts.values())).draft
    logger.info(f"Original draft: '{original_draft.full_text}'")

    # Verify rescanned draft was received
    assert len(source_api.rescanned_drafts) > 0, "Source did not receive rescanned draft"

    # Get rescanned draft (keyed by parent_draft_id which is the original draft_id)
    rescanned_draft = source_api.rescanned_drafts.get(original_draft.draft_id)
    assert rescanned_draft is not None, f"No rescanned draft found for original {original_draft.draft_id}"
    logger.info(f"Rescanned draft: '{rescanned_draft.full_text}'")

    # not going to compare final text, too much likelihood of major difference
    # Verify parent relationship
    assert rescanned_draft.parent_draft_id == original_draft.draft_id, \
        f"Parent draft ID mismatch: {rescanned_draft.parent_draft_id} != {original_draft.draft_id}"

    # Verify source saved original to database (rescanned drafts come via WebSocket)
    source_db = source_dir / "drafts.db"
    assert source_db.exists(), f"Source database not found at {source_db}"
    engine = create_engine(f"sqlite:///{source_db}")
    with Session(engine) as session:
        statement = select(DraftRecord).where(DraftRecord.draft_id == str(original_draft.draft_id))
        source_record = session.exec(statement).first()
        assert source_record is not None, f"Original draft {original_draft.draft_id} not found in database"
        assert source_record.full_text == original_draft.full_text
        logger.info(f"Source database verified: '{source_record.full_text}'")

    # Verify API listeners received lifecycle events
    assert source_api.have_pipeline_ready, "Source pipeline never reported ready"
    assert source_api.have_pipeline_shutdown, "Source pipeline never reported shutdown"

    logger.info("✅ Rescan mode communication test passed!")

    # Cleanup
    shutil.rmtree(source_dir)
