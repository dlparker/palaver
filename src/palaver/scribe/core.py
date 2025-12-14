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
import traceback

from palaver.scribe.listen_api import Listener
from palaver.scribe.listener.downsampler import DownSampler
from palaver.scribe.listener.vad_filter import VADFilter
from palaver.scribe.scriven.whisper_thread import WhisperThread
from palaver.scribe.scriven.wire_commands import CommandDispatch
from palaver.scribe.command_events import ScribeCommandEvent, CommandEventListener
from palaver.scribe.text_events import TextEventListener
from palaver.scribe.listener.audio_merge import AudioMerge
from palaver.scribe.api import ScribeAPIListener
from palaver.scribe.api import default_commands

logger = logging.getLogger("ScribeCore")


@dataclass
class PipelineConfig:
    """Configuration for the Scribe pipeline."""
    model_path: Path
    api_listener:ScribeAPIListener
    target_samplerate: int = 16000
    target_channels: int = 1
    use_multiprocessing: bool = False
    whisper_shutdown_timeout: float = 3.0


class ScribePipeline:

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

    def get_pipeline_parts(self):
        return dict(audio_source=self.listener,
                    downsampler=self.downsampler,
                    vadfilter=self.vadfilter,
                    transcription=self.whisper_thread,
                    audio_merge=self.audio_merge,
                    command_dispatch=self.command_dispatch)
    
    def add_api_listener(self, api_listener:ScribeAPIListener,
                               to_source: bool=False, to_VAD: bool=False, to_merge: bool=False):
        if sum((to_source, to_VAD, to_merge)) > 1:
            raise Exception('You can supply at most one value for audio event attachement')
        if to_merge:
            self.audio_merge.add_event_listener(api_listener)
        elif to_VAD:
            self.vadfilter.add_event_listener(api_listener)
        else:
            self.listener.add_event_listener(api_listener)
        self.whisper_thread.add_text_event_listener(api_listener)
        self.command_dispatch.add_event_listener(api_listener)
        
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
        self.audio_merge = AudioMerge()
        await self.audio_merge.start()
        full, vad = self.audio_merge.get_shims()
        self.listener.add_event_listener(full)
        self.vadfilter.add_event_listener(vad)

        # Create whisper transcription thread
        self.whisper_thread = WhisperThread(
            self.config.model_path,
            use_mp=self.config.use_multiprocessing
        )
        self.vadfilter.add_event_listener(self.whisper_thread)

        # Attach the command listener
        self.command_dispatch = CommandDispatch()
        self.whisper_thread.add_text_event_listener(self.command_dispatch)
        for patterns, command in default_commands:
            self.command_dispatch.register_command(command, patterns)
        self.command_dispatch.add_event_listener(self)

        self.add_api_listener(self.config.api_listener)

        # Start the whisper thread
        await self.whisper_thread.start()

        self._pipeline_setup_complete = True
        logger.info("Pipeline setup complete")
        try:
            await self.config.api_listener.on_pipeline_ready(self)
        except:
            logger.error("pipeline callback to api_listener on startup got error\n%s",
                         traceback.format_exc())

    def set_background_error(self, error_dict):
        self.background_error = error_dict
        
    async def start_listener(self):
        """Start the listener streaming audo."""
        await self.listener.start_recording()
        logger.info("Listener started")

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
        # Then shutdown whisper and text listener
        if self.whisper_thread:
            await self.whisper_thread.gracefull_shutdown(self.config.whisper_shutdown_timeout)
            self.whisper_thread = None
                
        try:
            await self.config.api_listener.on_pipeline_shutdown()
        except:
            logger.error("pipleline shutdown callback to api_listener error\n%s",
                         traceback.format_exc())

        finally:
            logger.info("Pipeline shutdown complete")

    async def __aenter__(self):
        """Enter the async context manager."""
        await self.setup_pipeline()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Exit the async context manager, ensuring cleanup."""
        await self.shutdown()
        return False  # Don't suppress exceptions
