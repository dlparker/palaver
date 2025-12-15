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
from palaver.scribe.listener.vad_filter import VADFilter, VADShim
from palaver.scribe.scriven.whisper_thread import WhisperThread
from palaver.scribe.scriven.wire_commands import CommandDispatch, CommandShim
from palaver.scribe.command_events import ScribeCommandEvent, CommandEventListener
from palaver.scribe.text_events import TextEventListener, TextEvent
from palaver.scribe.audio_events import AudioEvent, AudioStopEvent, AudioSpeechStartEvent, AudioSpeechStopEvent 
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
    rescan_mode: bool = False


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

        if self.config.rescan_mode:
            self.vadfilter = VADShim(self.listener)
        else:
            self.vadfilter = VADFilter(self.listener)
        self.downsampler.add_event_listener(self.vadfilter)
        self.audio_merge = AudioMerge()
        await self.audio_merge.start()
        if not self.config.rescan_mode:
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
        if self.config.rescan_mode:
            self.command_dispatch = CommandShim()
            # needs audo start and stop 
            self.vadfilter.add_event_listener(self.command_dispatch)
        else:
            self.command_dispatch = CommandDispatch()
        self.whisper_thread.add_text_event_listener(self.command_dispatch)
        for patterns, command in default_commands:
            self.command_dispatch.register_command(command, patterns)

        self.add_api_listener(self.config.api_listener, to_merge=not self.config.rescan_mode)
        self._stream_monitor = StreamMonitor(self)
        self.add_api_listener(self._stream_monitor, to_merge=True)

        # Start the whisper thread
        if self.config.rescan_mode:
            samples_per_scan = 16000 * 10
            await self.whisper_thread.set_rescan_mode(samples_per_scan)
        await self.whisper_thread.start()
        
        self._pipeline_setup_complete = True
        logger.info("Pipeline setup complete")
        try:
            await self.config.api_listener.on_pipeline_ready(self)
        except:
            logger.error("pipeline callback to api_listener on startup got error\n%s",
                         traceback.format_exc())

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
        
        
    def set_background_error(self, error_dict):
        self.background_error = error_dict
        
    async def start_listener(self):
        """Start the listener streaming audo."""
        await self.listener.start_streaming()
        logger.info("Listener started")

    async def listener_done(self):
        if self.config.rescan_mode:
            pass
        return not self.listener._running
        
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

class StreamMonitor(ScribeAPIListener):

    def __init__(self, core):
        super().__init__()
        self.core = core
        self.speech_stop = None
        self.audio_stop = None
        self.speech_start = None
        self.last_text = None
        self.all_done = False
        self.last_chunk = None
        self.in_block_event = None

    def is_all_done(self):
        return self.all_done
    
    async def on_pipeline_ready(self, pipeline):
        pass
    
    async def on_pipeline_shutdown(self):
        pass

    def check_done(self, why):
        from pprint import pformat
        print("----- DUMP DUMP DUMP DUMP DUMP ---------------")
        print(f"reason: {why}")
        # everything good case is
        if self.audio_stop and self.in_block_event is None:
            self.all_done = True
        if self.audio_stop and self.speech_stop:
            # this should always happen since the VAD (or shim)
            # issues a speech stop on audio stop if end of
            # speech has not been detected.
            diff = self.audio_stop.timestamp - self.speech_stop.last_in_speech_chunk_time
            print(f"sound between speech_stop and audio_stop = {diff}")
            if self.last_text:
                diff = self.speech_stop.last_in_speech_chunk_time - self.last_text.audio_end_time 
                print(f"sound between last text and speech_stop = {diff}")
                if diff < 0.5:
                    # this is not going to be precise. The VAD does buffering andpadding,
                    # it will never report the exact last block
                    self.all_done = True
            else:
                print(f"Never saw text and audio is stopped, need to check whisper for pending")
            
        print(f"all_done: {self.all_done}")
        print("********")
        print("audio_stop:")
        print(pformat(self.audio_stop))
        print("********")
        print("speech_start:")
        print(pformat(self.speech_start))
        print("********")
        print("speech_stop:")
        print(pformat(self.speech_stop))
        print("********")
        print("last_text:")
        print(pformat(self.last_text))
        print("********")
        print("last_chunk:")
        if self.last_chunk:
            print(f"timestamp = {self.last_chunk.timestamp}")
        else:
            print("")
        print("********")
        print("in_block_event:")
        print(pformat(self.in_block_event))
            
        print("----- END END END END DUMP ---------------")
        
    async def on_audio_event(self, event):
        if isinstance(event, AudioSpeechStopEvent):
            self.speech_stop = event
            self.speech_start = None
            self.check_done("speech stop")
        if isinstance(event, AudioSpeechStartEvent):
            self.speech_start = event
            self.speech_stop = None
            self.check_done("speech start")
        if isinstance(event, AudioStopEvent):
            # stream is shutdown, check to see if whisper
            # had done last chunk
            self.audio_stop = event
            self.check_done("audio stop")
        
    async def on_command_event(self, event:ScribeCommandEvent):
        from palaver.scribe.api import StartNoteCommand, StopNoteCommand, StartRescanCommand
        if isinstance(event.command, StartNoteCommand):
            self.in_block_event = event
            self.check_done("note start")
        elif isinstance(event.command, StopNoteCommand):
            self.in_block_event = None
            self.check_done("note stop")

    async def on_text_event(self, event: TextEvent):
        self.last_text = event
        self.check_done("text")
