#!/usr/bin/env python3
"""
File playback server for audio transcription from files.
"""
import asyncio
import logging
import traceback
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
from palaver.scribe.api import start_note_command, stop_note_command, ScribeAPIListener

logger = logging.getLogger("PlaybackServer")

# Default constants for file playback
DEFAULT_CHUNK_DURATION = 0.03


class PlaybackServer:
    def __init__(self,
                 model_path,
                 audio_files: List[Path],
                 api_listener: ScribeAPIListener,
                 rescan_mode: Optional[bool] = False,
                 use_multiprocessing: bool = False,
                 chunk_duration=DEFAULT_CHUNK_DURATION,
                 simulate_timing=False):

        """
        Run the file playback transcription server.

        Args:
            model_path: Path to the Whisper model file
            audio_files: List of audio file paths to process
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
        logger.info(f"Files: {audio_files}")

        
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
            files=audio_files,
            chunk_duration=chunk_duration,
            simulate_timing=simulate_timing,
        )

        
    def set_background_error(self, error_dict):
        self._background_error = error_dict
        self.pipeline.set_background_error(error_dict)
        
    async def run(self):
        # Use nested context managers: listener first, then pipeline
        async with self.file_listener:
            if self.rescan_mode:
                self.config.whisper_shutdown_timeout = 20.0
                self.pipeline = RescanPipeline(self.file_listener, self.config)
            else:
                self.pipeline = ScribePipeline(self.file_listener, self.config)
            async with self.pipeline:
                await self.pipeline.start_listener()

                if self.rescan_mode:
                    samples_per_scan = 16000 * 5
                    await self.pipeline.whisper_thread.set_rescan_mode(samples_per_scan)
                    await self.pipeline.whisper_thread.start()
                    await self.pipeline.whisper_thread.set_in_speech(True)
                    
                # For file playback, wait until the listener completes
                # (FileListener stops when files are exhausted)
                while self.file_listener._running:
                    await asyncio.sleep(0.1)

                    # Still check for background errors
                    if self._background_error:
                        logger.error("Error during playback: %s", pformat(self.pipeline.background_error))
                        raise Exception(pformat(self._background_error))
                
                if self.rescan_mode:
                    await self.pipeline.whisper_thread.set_in_speech(False)
                # Pipeline shutdown happens automatically in __aexit__

        logger.info("Playback server finished.")
        self.pipeline = None

    def get_pipeline(self):
        return self.pipeline

class APIShim(AudioEventListener):


    def __init__(self, real_api_listener, pipeline):
        self.real_api_listener = real_api_listener
        self.pipeline = pipeline
        self.audio_emitter = AsyncIOEventEmitter()
        self.command_emitter = AsyncIOEventEmitter()
        self.text_emitter = AsyncIOEventEmitter()
        self.first_audio_event = None
        self.first_text_event = None
        self.last_text_event = None
        self.buff = ""

    async def on_pipeline_ready(self, pipeline):
        await self.real_api_listener.on_pipeline_ready(pipeline)
    
    async def on_pipeline_shutdown(self):
        await self.real_api_listener.on_pipeline_shutdown()
        print()
        print("------Shim--------")
        print(self.buff)
        print("------Shim End--------")
        print()

    def add_audio_listener(self, e_listener: AudioEventListener) -> None:
        self.audio_emitter.on(AudioEvent, e_listener.on_audio_event)
        
    def add_text_listener(self, e_listener: TextEvent) -> None:
        self.text_emitter.on(TextEvent, e_listener.on_text_event)
        
    def add_command_listener(self, e_listener: ScribeCommandEvent) -> None:
        self.command_emitter.on(ScribeCommandEvent, e_listener.on_command_event)
        
    async def on_audio_event(self, event):
        if self.first_audio_event is None:
            logger.debug(f"shim first catch {event}")
            self.first_audio_event = event
            speech_event = AudioSpeechStartEvent(timestamp=event.timestamp,
                                                 silence_period_ms=1000,
                                                 vad_threshold=0.5,
                                                 sampling_rate=16000.0,
                                                 speech_pad_ms=1.5,
                                                 source_id=event.source_id,
                                                 )
            logger.debug(f"shim gen {speech_event}")
            await self.audio_emitter.emit(AudioEvent, event)
            await self.audio_emitter.emit(AudioEvent, speech_event)
            return
        if isinstance(event, AudioStopEvent):
            speech_event = AudioSpeechStopEvent(timestamp=event.timestamp,
                                                source_id=event.source_id,
                                                )
            logger.debug(f"shim gen {speech_event}")
            await self.audio_emitter.emit(AudioEvent, speech_event)
            command_event = ScribeCommandEvent(text_event=self.last_text_event,
                                               command=stop_note_command,
                                               pattern="break break break",
                                               segment_number=1)
            logger.debug(f"shim gen {command_event}")
            await self.command_emitter.emit(ScribeCommandEvent, command_event)
        if not isinstance(event, AudioChunkEvent):
            logger.debug(event)
        await self.audio_emitter.emit(AudioEvent, event)

    async def on_text_event(self, event):
        if self.first_text_event is None:
            self.first_text_event  = event
            command_event = ScribeCommandEvent(text_event=event,
                                               command=start_note_command,
                                               pattern="start new note",
                                               segment_number=1)
            logger.debug(f"shim gen {command_event}")
            await self.command_emitter.emit(ScribeCommandEvent, command_event)
        logger.debug(event)
        for seg in event.segments:
            self.buff += seg.text + " "
        await self.text_emitter.emit(TextEvent, event)
        self.last_text_event = event
        
    async def on_command_event(self, event):
        logger.debug(f"shim catch {event}")
        await self.command_emitter.emit(ScribeCommandEvent, event)
        
class RescanPipeline(ScribePipeline):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.shim = APIShim(self.config.api_listener, self)
        self.orig_api_listener = self.config.api_listener
        self.config.api_listener = self.shim
        self.shim.add_audio_listener(self.orig_api_listener)
        self.shim.add_text_listener(self.orig_api_listener)
        self.shim.add_command_listener(self.orig_api_listener)

        
    def add_api_listener(self, api_listener:ScribeAPIListener,
                               to_source: bool=False, to_VAD: bool=False, to_merge: bool=False):
        self.listener.add_event_listener(api_listener)
        self.whisper_thread.add_text_event_listener(api_listener)
        
    async def setup_pipeline(self):
        if self._pipeline_setup_complete:
            return
        
        # Create downsampler
        self.downsampler = DownSampler(
            target_samplerate=self.config.target_samplerate,
            target_channels=self.config.target_channels
        )
        self.listener.add_event_listener(self.downsampler)
        self.listener.add_event_listener(self.shim)
  
        # Create whisper transcription thread
        self.whisper_thread = WhisperThread(
            self.config.model_path,
            use_mp=self.config.use_multiprocessing
        )
        self.downsampler.add_event_listener(self.whisper_thread)
        self.whisper_thread.add_text_event_listener(self.shim)

        self._pipeline_setup_complete = True
        logger.info("Pipeline setup complete")
        try:
            await self.shim.on_pipeline_ready(self)
        except:
            logger.error("pipeline callback to api_listener on startup got error\n%s",
                         traceback.format_exc())


