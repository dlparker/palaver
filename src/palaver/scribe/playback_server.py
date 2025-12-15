#!/usr/bin/env python3
"""
File playback server for audio transcription from files.
"""
import asyncio
import logging
import traceback
import time
from pathlib import Path
from pprint import pformat
from typing import Optional, List
from eventemitter import AsyncIOEventEmitter

from palaver.scribe.listener.downsampler import DownSampler
from palaver.scribe.scriven.whisper_thread import WhisperThread
from palaver.scribe.listener.file_listener import FileListener
from palaver.scribe.core import ScribePipeline, PipelineConfig
from palaver.scribe.api import ScribeAPIListener
from palaver.scribe.audio_events import (AudioEvent,
                                         AudioEventListener,
                                         AudioChunkEvent,
                                         AudioStartEvent,
                                         AudioStopEvent,
                                         AudioSpeechStartEvent,
                                         AudioSpeechStopEvent,
                                         )
from palaver.scribe.text_events import TextEvent, TextEventListener
from palaver.scribe.command_events import ScribeCommandEvent, CommandEventListener, ScribeCommand
from palaver.scribe.api import start_note_command, stop_note_command, start_rescan_command, ScribeAPIListener

logger = logging.getLogger("PlaybackServer")

# Default constants for file playback
DEFAULT_CHUNK_DURATION = 0.03


class PlaybackServer:
    def __init__(self,
                 model_path,
                 audio_file: Path,
                 api_listener: ScribeAPIListener,
                 rescan_mode: Optional[bool] = False,
                 use_multiprocessing: bool = False,
                 chunk_duration=DEFAULT_CHUNK_DURATION,
                 simulate_timing=False):

        """
        Run the file playback transcription server.

        Args:
            model_path: Path to the Whisper model file
            audio_file: audio file path to process
            api_listener: listener for core events
            rescan_mode: Rescan of already transcribed block
            chunk_duration: Audio chunk duration in seconds
            use_multiprocessing: Use multiprocessing for Whisper (vs threading)
        """
        self._background_error = None
        self.pipeline = None
        self.rescan_mode = rescan_mode
        logger.info("Starting playback server")
        logger.info(f"Model: {model_path}")
        logger.info(f"Multiprocessing: {use_multiprocessing}")
        logger.info(f"Simulate timing: {simulate_timing}")
        logger.info(f"File: {audio_file}")

        
        # Create pipeline configuration
        self.config = PipelineConfig(
            model_path=model_path,
            target_samplerate=16000,
            target_channels=1,
            use_multiprocessing=use_multiprocessing,
            api_listener=api_listener,
        )

        # Create file listener
        self.file_listener = FileListener(
            audio_file=audio_file,
            chunk_duration=chunk_duration,
            simulate_timing=simulate_timing,
        )
        
    def set_background_error(self, error_dict):
        self._background_error = error_dict
        self.pipeline.set_background_error(error_dict)

    def get_pipeline(self):
        return self.pipeline
    
    async def run(self):
        # Use nested context managers: listener first, then pipeline
        async with self.file_listener:
            self.pipeline = ScribePipeline(self.file_listener, self.config)
            async with self.pipeline:
                if self.rescan_mode:
                    samples_per_scan = 16000 * 8
                    self.pipeline.vadfilter.reset(silence_ms=8000, speech_pad_ms=1000)
                else:
                    samples_per_scan = 16000 * 2
                    #samples_per_scan = 16000 * 8
                    #self.pipeline.vadfilter.reset(silence_ms=8000, speech_pad_ms=1000)
                await self.pipeline.whisper_thread.set_buffer_samples(samples_per_scan)
                
                await self.pipeline.start_listener()
                await self.pipeline.run_until_error_or_interrupt()
                            
        logger.info("Playback server finished.")
        self.pipeline = None

