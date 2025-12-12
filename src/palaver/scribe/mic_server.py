#!/usr/bin/env python3
"""
Microphone server for real-time audio transcription.
"""
import asyncio
import logging
from pathlib import Path
from typing import Optional

from palaver.scribe.listener.mic_listener import MicListener
from palaver.scribe.core import ScribePipeline, PipelineConfig
from palaver.scribe.api import ScribeAPIListener

logger = logging.getLogger("MicServer")

# Default constants for microphone capture
DEFAULT_CHUNK_DURATION = 0.03

class MicServer:

    def __init__(self,
                 model_path,
                 api_listener: ScribeAPIListener,
                 use_multiprocessing: bool = False,
                 chunk_duration=DEFAULT_CHUNK_DURATION):
        """
        Args:
        model_path: Path to the Whisper model file
        text_event_listener: listener for transcribed text events
        command_event_listener: listener for transcribed text events
        chunk_duration: Audio chunk duration in seconds
        use_multiprocessing: Use multiprocessing for Whisper (vs threading)
    """
        self._background_error = None
                 
        logger.info("Starting microphone server")
        logger.info(f"Model: {model_path}")
        logger.info(f"Multiprocessing: {use_multiprocessing}")

        # Create pipeline configuration
        self.config = PipelineConfig(
            model_path=model_path,
            target_samplerate=16000,
            target_channels=1,
            use_multiprocessing=use_multiprocessing,
            api_listener=api_listener,
        )

        # Create microphone listener
        # Error callback is captured by the pipeline
        self.mic_listener = MicListener(
            chunk_duration=chunk_duration,
            error_callback=self.error_callback
        )

    def error_callback(self, error_data):
        self._background_error = error_data
        
    async def run(self):
        # Use nested context managers: listener first, then pipeline
        async with self.mic_listener:
            async with ScribePipeline(self.mic_listener, self.config, self.error_callback) as pipeline:
                await pipeline.start_listener()

                try:
                    await pipeline.run_until_error_or_interrupt()
                except (KeyboardInterrupt, asyncio.CancelledError):
                    print("\nControl-C detected. Shutting down...")
                # Pipeline shutdown happens automatically in __aexit__
                if self._background_error:
                    logger.error("Error during playback: %s", pformat(pipeline.background_error))
                    raise Exception(pformat(self._background_error))
        logger.info("Microphone server exiting.")
