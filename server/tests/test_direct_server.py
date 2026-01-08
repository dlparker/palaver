#!/usr/bin/env python
"""
tests/test_direct_server.py
Test EventNetServer in direct mode with mocked audio input
"""

import pytest
import asyncio
import logging
import time
import shutil
from pathlib import Path
from unittest.mock import patch

from palaver.scribe.audio.mic_listener import MicListener
from palaver.scribe.core import PipelineConfig, ScribePipeline
from palaver.scribe.recorders.sql_drafts import SQLDraftRecorder, DraftRecord
from palaver.fastapi.event_server import EventNetServer, ServerMode
from palaver_shared.top_error import TopErrorHandler, TopLevelCallback
from sqlmodel import Session, select
from tests.test_utils import MockStream, APIWrapper


logger = logging.getLogger("test_direct_server")


async def test_event_server_with_mock_audio():
    """Test EventNetServer with mocked audio input using MockStream."""
    # Verify test file exists
    audio_file = Path(__file__).parent / "audio_samples" / "note1.wav"
    assert audio_file.exists()
    model = Path(__file__).parent.parent / "models" / "ggml-base.en.bin"
    assert model.exists()

    logger.info(f"TESTING EventNetServer WITH MOCK INPUT: {audio_file}")

    recorder_dir = Path(__file__).parent / "recorder_output_event_server"
    # Clean it up before running
    if recorder_dir.exists():
        shutil.rmtree(recorder_dir)

    async def main_task(model):
        # Store reference to mock instance for monitoring
        mock_instance = None
        api_wrapper = APIWrapper()

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
                api_listener=None,  # Will be set by EventNetServer
                target_samplerate=16000,
                target_channels=1,
                use_multiprocessing=False,
                vad_silence_ms=3000,
                vad_speech_pad_ms=1000,
                seconds_per_scan=2,
                whisper_shutdown_timeout=1.0,
            )

            draft_recorder = SQLDraftRecorder(recorder_dir, enable_file_storage=False)
            logger.info(f"Draft recorder enabled: {recorder_dir}")

            # Create EventNetServer in direct mode
            server = EventNetServer(
                audio_listener=mic_listener,
                pipeline_config=config,
                draft_recorder=draft_recorder,
                port=8000,
                mode=ServerMode.direct
            )

            # Manually run the pipeline setup (without FastAPI)
            # This mimics what lifespan does
            config.api_listener = api_wrapper

            async with mic_listener:
                async with ScribePipeline(mic_listener, config) as pipeline:
                    pipeline.add_api_listener(draft_recorder)
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

                        # Give extra time for transcription to complete
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
                            raise Exception('never got draft end')
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

        # Return api_wrapper so we can check it in the outer scope
        return api_wrapper

    background_error_dict = None

    class ErrorCallback(TopLevelCallback):
        async def on_error(self, error_dict: dict):
            nonlocal background_error_dict
            background_error_dict = error_dict

    # Run with standard error handling
    handler = TopErrorHandler(top_level_callback=ErrorCallback(), logger=logger)
    api_wrapper = await handler.async_run(main_task, model)

    assert background_error_dict is None, f"Background error occurred: {background_error_dict}"
    assert len(api_wrapper.drafts) == 1, f"Expected 1 draft, got {len(api_wrapper.drafts)}"
    assert api_wrapper.have_pipeline_ready
    assert api_wrapper.have_pipeline_shutdown

    # Verify draft was saved to database
    dt = next(iter(api_wrapper.drafts.values()))
    draft = dt.draft

    # Query database directly using SQLModel
    db_path = recorder_dir / "drafts.db"
    assert db_path.exists(), f"Database file not found at {db_path}"

    from sqlmodel import create_engine
    engine = create_engine(f"sqlite:///{db_path}")

    with Session(engine) as session:
        # Find the draft record by draft_id
        statement = select(DraftRecord).where(DraftRecord.draft_id == str(draft.draft_id))
        db_record = session.exec(statement).first()

        assert db_record is not None, f"Draft {draft.draft_id} not found in database"
        assert db_record.full_text == draft.full_text, f"Expected '{draft.full_text}' but got '{db_record.full_text}'"
        assert db_record.timestamp == draft.timestamp
        assert db_record.parent_draft_id is None  # First draft should have no parent
        assert db_record.classname == str(draft.__class__)

        logger.info(f"Verified draft in database: '{db_record.full_text}'")

    # Cleanup
    shutil.rmtree(recorder_dir)
