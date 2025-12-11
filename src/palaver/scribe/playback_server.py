#!/usr/bin/env python3
"""
File playback server for audio transcription from files.
"""
import asyncio
import logging
from pathlib import Path
from typing import Optional, List

from palaver.scribe.listener.file_listener import FileListener
from palaver.scribe.core import ScribePipeline, PipelineConfig
from palaver.scribe.text_events import TextEventListener

logger = logging.getLogger("PlaybackServer")

# Default constants for file playback
DEFAULT_CHUNK_DURATION = 0.03


async def run_playback_server(
    model_path: Path,
    audio_files: List[Path],
    text_event_listener: Optional[TextEventListener] = None,
    chunk_duration: float = DEFAULT_CHUNK_DURATION,
    simulate_timing: bool = True,
    use_vad: bool = True,
    use_multiprocessing: bool = False,
    recording_output_dir: Optional[Path] = None,
) -> None:
    """
    Run the file playback transcription server.

    Args:
        model_path: Path to the Whisper model file
        audio_files: List of audio file paths to process
        text_event_listener: Optional listener for transcribed text events
        chunk_duration: Audio chunk duration in seconds
        simulate_timing: Simulate real-time audio timing
        use_vad: Enable Voice Activity Detection
        use_multiprocessing: Use multiprocessing for Whisper (vs threading)
        recording_output_dir: Optional directory to save WAV recordings and event logs
    """
    logger.info("Starting playback server")
    logger.info(f"Model: {model_path}")
    logger.info(f"Files: {audio_files}")
    logger.info(f"VAD: {use_vad}, Simulate timing: {simulate_timing}")

    # Create pipeline configuration
    config = PipelineConfig(
        model_path=model_path,
        target_samplerate=16000,
        target_channels=1,
        use_vad=use_vad,
        use_multiprocessing=use_multiprocessing,
        text_event_listener=text_event_listener,
        recording_output_dir=recording_output_dir,
    )

    # Create file listener
    file_listener = FileListener(
        chunk_duration=chunk_duration,
        simulate_timing=simulate_timing,
        files=audio_files,
        error_callback=None
    )

    # Use nested context managers: listener first, then pipeline
    async with file_listener:
        async with ScribePipeline(file_listener, config) as pipeline:
            await pipeline.start_recording()

            # For file playback, wait until the listener completes
            # (FileListener stops when files are exhausted)
            while file_listener._running:
                await asyncio.sleep(0.1)

                # Still check for background errors
                if pipeline.background_error:
                    from pprint import pformat
                    logger.error("Error during playback: %s", pformat(pipeline.background_error))
                    raise Exception(pformat(pipeline.background_error))
            # Pipeline shutdown happens automatically in __aexit__

    print("Playback server finished.")
