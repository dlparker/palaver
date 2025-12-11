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

logger = logging.getLogger("MicServer")

# Default constants for microphone capture
DEFAULT_CHUNK_DURATION = 0.03


async def run_mic_server(
    model_path: Path,
    text_event_listener: Optional[TextEventListener] = None,
    chunk_duration: float = DEFAULT_CHUNK_DURATION,
    use_vad: bool = True,
    use_multiprocessing: bool = False,
    recording_output_dir: Optional[Path] = None,
) -> None:
    """
    Run the microphone transcription server.

    Args:
        model_path: Path to the Whisper model file
        text_event_listener: Optional listener for transcribed text events
        chunk_duration: Audio chunk duration in seconds
        use_vad: Enable Voice Activity Detection
        use_multiprocessing: Use multiprocessing for Whisper (vs threading)
        recording_output_dir: Optional directory to save WAV recordings and event logs
    """
    logger.info("Starting microphone server")
    logger.info(f"Model: {model_path}")
    logger.info(f"VAD: {use_vad}, Multiprocessing: {use_multiprocessing}")

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

    # Create microphone listener
    # Error callback is captured by the pipeline
    mic_listener = MicListener(
        chunk_duration=chunk_duration,
        error_callback=None
    )

    # Use nested context managers: listener first, then pipeline
    async with mic_listener:
        async with ScribePipeline(mic_listener, config) as pipeline:
            await pipeline.start_recording()

            try:
                await pipeline.run_until_error_or_interrupt()
            except (KeyboardInterrupt, asyncio.CancelledError):
                print("\nControl-C detected. Shutting down...")
            # Pipeline shutdown happens automatically in __aexit__

    print("Microphone server finished.")
