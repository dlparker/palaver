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
from palaver.scribe.text_events import TextEventListener
from palaver.scribe.scriven.wire_commands import CommandEventListener

logger = logging.getLogger("MicServer")

# Default constants for microphone capture
DEFAULT_CHUNK_DURATION = 0.03

class MicServer:

    def __init__(self,
                 model_path, 
                 text_event_listener: TextEventListener,
                 command_event_listener: CommandEventListener,
                 use_multiprocessing: bool = False,
                 chunk_duration=DEFAULT_CHUNK_DURATION,
                 recording_output_dir: Optional[Path] = None):

        """
        Args:
        model_path: Path to the Whisper model file
        text_event_listener: Optional listener for transcribed text events
        chunk_duration: Audio chunk duration in seconds
        use_multiprocessing: Use multiprocessing for Whisper (vs threading)
        recording_output_dir: Optional directory to save WAV recordings and event logs
    """
                 
        logger.info("Starting microphone server")
        logger.info(f"Model: {model_path}")
        logger.info(f"Multiprocessing: {use_multiprocessing}")

        # Create pipeline configuration
        self.config = PipelineConfig(
            model_path=model_path,
            target_samplerate=16000,
            target_channels=1,
            use_vad=True,
            use_multiprocessing=use_multiprocessing,
            text_event_listener=text_event_listener,
            command_event_listener=command_event_listener,
            recording_output_dir=recording_output_dir,
        )

        # Create microphone listener
        # Error callback is captured by the pipeline
        self.mic_listener = MicListener(
            chunk_duration=chunk_duration,
            error_callback=None
        )

    async def run(self):
        # Use nested context managers: listener first, then pipeline
        async with self.mic_listener:
            async with ScribePipeline(self.mic_listener, self.config) as pipeline:
                await pipeline.start_recording()

                try:
                    await pipeline.run_until_error_or_interrupt()
                except (KeyboardInterrupt, asyncio.CancelledError):
                    print("\nControl-C detected. Shutting down...")
                # Pipeline shutdown happens automatically in __aexit__

        logger.warning("Microphone server exiting.")
