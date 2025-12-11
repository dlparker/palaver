#!/usr/bin/env python3
"""
Core pipeline setup for the Scribe transcription system.
Provides shared logic for assembling the audio processing pipeline.
"""
import asyncio
import logging
from dataclasses import dataclass, field
from typing import Optional, Callable
from pathlib import Path

from palaver.scribe.listen_api import Listener
from palaver.scribe.listener.downsampler import DownSampler
from palaver.scribe.listener.vad_filter import VADFilter
from palaver.scribe.scriven.whisper_thread import WhisperThread
from palaver.scribe.scriven.wire_commands import CommandDispatch, ScribeCommandEvent, CommandEventListener
from palaver.scribe.text_events import TextEventListener

logger = logging.getLogger("ScribeCore")


@dataclass
class PipelineConfig:
    """Configuration for the Scribe pipeline."""
    model_path: Path
    text_event_listener: TextEventListener
    command_event_listener: CommandEventListener
    target_samplerate: int = 16000
    target_channels: int = 1
    use_multiprocessing: bool = False
    whisper_shutdown_timeout: float = 3.0
    recording_output_dir: Optional[Path] = None


class ScribePipeline:
    """
    Manages the complete audio transcription pipeline.

    Architecture:
        Listener → DownSampler → VADFilter → WhisperThread → TextEventListener

    Usage:
        config = PipelineConfig(model_path=Path("model.bin"))
        listener = MicListener(chunk_duration=0.03, error_callback=error_callback)

        async with ScribePipeline(listener, config) as pipeline:
            await pipeline.run_until_error_or_interrupt()
    """

    def __init__(self, listener: Listener, config: PipelineConfig):
        """
        Initialize the pipeline with a configured listener.

        Args:
            listener: A Listener implementation (MicListener, FileListener, etc.)
                     Must already be configured but not yet started.
            config: Pipeline configuration parameters.
        """
        self.listener = listener
        self.config = config
        self.background_error = None

        # Pipeline components (initialized in setup)
        self.downsampler: Optional[DownSampler] = None
        self.vadfilter: Optional[VADFilter] = None
        self.whisper_thread: Optional[WhisperThread] = None
        self.command_dispatch: Optional[CommandDispatch]  = None
        self.audio_merge = None
        self.wav_recorder = None
        self.text_logger = None
        self._pipeline_setup_complete = False

    def _error_callback(self, error_dict: dict):
        """Internal error callback to track background errors."""
        self.background_error = error_dict
        logger.error("Background error occurred: %s", error_dict)

    async def setup_pipeline(self):
        """
        Assemble and start the processing pipeline.
        Must be called inside the listener's context manager.
        """
        if self._pipeline_setup_complete:
            return

        # Create downsampler
        self.downsampler = DownSampler(
            target_samplerate=self.config.target_samplerate,
            target_channels=self.config.target_channels
        )
        self.listener.add_event_listener(self.downsampler)

        # Create VAD filter if enabled
        self.vadfilter = VADFilter(self.listener)
        self.downsampler.add_event_listener(self.vadfilter)
        audio_source = self.vadfilter

        # Setup recording if output_dir provided
        if self.config.recording_output_dir:
            from palaver.scribe.listener.audio_merge import AudioMerge
            from palaver.scribe.recorders.wav_save import WavSaveRecorder, TextEventLogger

            # Create AudioMerge if VAD enabled (to combine full-rate audio with VAD events)
            self.audio_merge = AudioMerge()
            full, vad = self.audio_merge.get_shims()
            self.listener.add_event_listener(full)
            self.vadfilter.add_event_listener(vad)
            await self.audio_merge.start()

            # Create WAV recorder
            self.wav_recorder = WavSaveRecorder(self.config.recording_output_dir)

            # Connect recorder to appropriate audio source
            if self.audio_merge:
                self.audio_merge.add_event_listener(self.wav_recorder)
            else:
                self.listener.add_event_listener(self.wav_recorder)

            await self.wav_recorder.start()

            # Wrap text event listener to log TextEvents
            if self.config.text_event_listener:
                self.text_logger = TextEventLogger(self.wav_recorder)
                original_listener = self.config.text_event_listener

                class CompositeTextListener:
                    async def on_text_event(slf, event):
                        await original_listener.on_text_event(event)
                        await self.text_logger.on_text_event(event)

                self.config.text_event_listener = CompositeTextListener()

            logger.info(f"Recording enabled: {self.config.recording_output_dir}")

        # Create whisper transcription thread
        self.whisper_thread = WhisperThread(
            self.config.model_path,
            self._error_callback,
            use_mp=self.config.use_multiprocessing
        )
        audio_source.add_event_listener(self.whisper_thread)

        # Attach the command listener 
        self.command_dispatch = CommandDispatch(self._error_callback)
        from palaver.scribe.commands import default_commands
        for patterns, command in default_commands:
            self.command_dispatch.register_command(command, patterns)
        self.whisper_thread.add_text_event_listener(self.command_dispatch)
        self.command_dispatch.add_event_listener(self)
        self.command_dispatch.add_event_listener(self.config.command_event_listener)
        
        self.whisper_thread.add_text_event_listener(self.config.text_event_listener)

        # Start the whisper thread
        await self.whisper_thread.start()

        self._pipeline_setup_complete = True
        logger.info("Pipeline setup complete")

    async def start_recording(self):
        """Start the listener recording."""
        await self.listener.start_recording()
        logger.info("Recording started")

    async def run_until_error_or_interrupt(self):
        """
        Main loop that runs until KeyboardInterrupt, CancelledError, or background error.
        Checks for background errors every 100ms.
        """
        try:
            while True:
                await asyncio.sleep(0.1)
                if self.background_error:
                    from pprint import pformat
                    logger.error("Error callback triggered: %s", pformat(self.background_error))
                    raise Exception(pformat(self.background_error))
        except (KeyboardInterrupt, asyncio.CancelledError):
            logger.info("Shutdown signal received")
            raise

    async def on_command_event(self, event: ScribeCommandEvent):
        pass
        
    async def shutdown(self):
        """
        Gracefully shutdown the pipeline.
        Must be called inside the listener's context manager, before it exits.
        """
        # Shutdown recording first to ensure all audio is saved
        if self.audio_merge:
            await self.audio_merge.flush()

        if self.wav_recorder:
            await self.wav_recorder.stop()

        # Then shutdown whisper and text listener
        if self.whisper_thread:
            await self.whisper_thread.gracefull_shutdown(self.config.whisper_shutdown_timeout)

        if self.config.text_event_listener and hasattr(self.config.text_event_listener, 'finish'):
            self.config.text_event_listener.finish()

        logger.info("Pipeline shutdown complete")

    async def __aenter__(self):
        """Enter the async context manager."""
        await self.setup_pipeline()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Exit the async context manager, ensuring cleanup."""
        await self.shutdown()
        return False  # Don't suppress exceptions
