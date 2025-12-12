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
from palaver.scribe.api import ScribeAPIListener

logger = logging.getLogger("PlaybackServer")

# Default constants for file playback
DEFAULT_CHUNK_DURATION = 0.03


class PlaybackServer:
    def __init__(self,
                 model_path,
                 audio_files: List[Path],
                 api_listener: ScribeAPIListener,
                 use_multiprocessing: bool = False,
                 chunk_duration=DEFAULT_CHUNK_DURATION,
                 simulate_timing=False,
                 recording_output_dir: Optional[Path] = None,
                 mqtt_config: Optional[dict] = None):

        """
        Run the file playback transcription server.

        Args:
            model_path: Path to the Whisper model file
            audio_files: List of audio file paths to process
            api_listener: listener for core events
            chunk_duration: Audio chunk duration in seconds
            use_multiprocessing: Use multiprocessing for Whisper (vs threading)
            recording_output_dir: Optional directory to save WAV recordings and event logs
            mqtt_config: Optional MQTT configuration dict
        """
        logger.info("Starting playback server")
        logger.info(f"Model: {model_path}")
        logger.info(f"Multiprocessing: {use_multiprocessing}")
        logger.info(f"Simulate timing: {simulate_timing}")
        logger.info(f"Files: {audio_files}")

        # Create pipeline configuration
        self.config = PipelineConfig(
            model_path=model_path,
            target_samplerate=16000,
            target_channels=1,
            use_multiprocessing=use_multiprocessing,
            api_listener=api_listener,
            recording_output_dir=recording_output_dir,
            mqtt_config=mqtt_config,
        )

        # Create file listener
        self.file_listener = FileListener(
            chunk_duration=chunk_duration,
            simulate_timing=simulate_timing,
            files=audio_files,
            error_callback=None
        )

    async def run(self):
        # Use nested context managers: listener first, then pipeline
        async with self.file_listener:
            async with ScribePipeline(self.file_listener, self.config) as pipeline:
                await pipeline.start_recording()

                # For file playback, wait until the listener completes
                # (FileListener stops when files are exhausted)
                while self.file_listener._running:
                    await asyncio.sleep(0.1)

                    # Still check for background errors
                    if pipeline.background_error:
                        from pprint import pformat
                        logger.error("Error during playback: %s", pformat(pipeline.background_error))
                        raise Exception(pformat(pipeline.background_error))
                # Pipeline shutdown happens automatically in __aexit__

        logger.info("Playback server finished.")
