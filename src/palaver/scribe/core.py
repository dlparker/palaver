import asyncio
import logging
import uuid
import time
from dataclasses import dataclass, field
from typing import Optional, Callable
from pathlib import Path
import traceback

from palaver.scribe.audio_listeners import AudioListener
from palaver.scribe.audio.downsampler import DownSampler
from palaver.scribe.audio.vad_filter import VADFilter
from palaver.scribe.audio.audio_merge import AudioMerge
from palaver.scribe.audio_events import AudioEvent, AudioStopEvent, AudioSpeechStartEvent, AudioSpeechStopEvent 
from palaver.scribe.draft_events import DraftEvent, DraftStartEvent, DraftEndEvent
from palaver.scribe.scriven.whisper import WhisperWrapper
from palaver.scribe.scriven.drafts import DraftMaker
from palaver.scribe.text_events import TextEventListener, TextEvent
from palaver.scribe.api import ScribeAPIListener

logger = logging.getLogger("ScribeCore")


@dataclass
class PipelineConfig:
    """Configuration for the Scribe pipeline."""
    model_path: Path
    api_listener:ScribeAPIListener
    target_samplerate: int = 16000
    target_channels: int = 1
    use_multiprocessing: bool = False
    whisper_shutdown_timeout: float = 10.0

    # VAD configuration
    vad_silence_ms: int = 800           # Default from VADFilter
    vad_speech_pad_ms: int = 1500        # Default from VADFilter
    vad_threshold: float = 0.5           # Default from VADFilter

    # Whisper buffer configuration
    whisper_buffer_samples: Optional[int] = None
    seconds_per_scan: Optional[float] = None  # Alternative to buffer_samples

    def __post_init__(self):
        """Validate configuration."""
        if self.whisper_buffer_samples is not None and self.seconds_per_scan is not None:
            raise ValueError(
                "Cannot set both whisper_buffer_samples and seconds_per_scan. "
                "Use one or the other."
            )


class ScribePipeline:

    def __init__(self, listener: AudioListener, config: PipelineConfig):
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
        self.whisper_tool: Optional[WhisperWrapper] = None
        self.draft_maker: Optional[DraftMaker]  = None
        self.audio_merge = None
        self.wav_recorder = None
        self.text_logger = None
        self._pipeline_setup_complete = False
        self._api_listeners = []

    def get_pipeline_parts(self):
        return dict(audio_source=self.listener,
                    downsampler=self.downsampler,
                    vadfilter=self.vadfilter,
                    transcription=self.whisper_tool,
                    audio_merge=self.audio_merge)
    
    def add_api_listener(self, api_listener:ScribeAPIListener,
                               to_source: bool=False, to_VAD: bool=False, to_merge: bool=True):
        if sum((to_source, to_VAD, to_merge)) > 1:
            raise Exception('You can supply at most one value for audio event attachement')
        if to_merge:
            self.audio_merge.add_event_listener(api_listener)
        elif to_VAD:
            self.vadfilter.add_event_listener(api_listener)
        else:
            self.listener.add_event_listener(api_listener)
        self.whisper_tool.add_text_event_listener(api_listener)
        self.draft_maker.add_event_listener(api_listener)
        self._api_listeners.append(api_listener)
        
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

        self.vadfilter = VADFilter(self.listener)
        self.downsampler.add_event_listener(self.vadfilter)
        # setup the merge layer to emit VAD signals
        # but to send all original signals from listerner
        # for other audio_event types
        self.audio_merge = AudioMerge()
        full_shim, vad_shim = self.audio_merge.get_shims()
        self.listener.add_event_listener(full_shim)
        self.vadfilter.add_event_listener(vad_shim)
        # Create whisper transcription tool thread or process
        self.whisper_tool = WhisperWrapper(
            self.config.model_path,
            use_mp=self.config.use_multiprocessing
        )
        self.vadfilter.add_event_listener(self.whisper_tool)

        self.draft_maker = DraftMaker()
        # Attach to the text listener
        self.whisper_tool.add_text_event_listener(self.draft_maker)
        # Attach to the audio listener
        self.listener.add_event_listener(self.draft_maker)
        
        # Apply VAD configuration from PipelineConfig
        self.vadfilter.reset(
            silence_ms=self.config.vad_silence_ms,
            speech_pad_ms=self.config.vad_speech_pad_ms,
            threshold=self.config.vad_threshold
        )

        # Apply Whisper buffer configuration from PipelineConfig
        if self.config.whisper_buffer_samples is not None:
            await self.whisper_tool.set_buffer_samples(self.config.whisper_buffer_samples)
        elif self.config.seconds_per_scan is not None:
            samples = int(self.config.target_samplerate * self.config.seconds_per_scan)
            await self.whisper_tool.set_buffer_samples(samples)

        self._stream_monitor = StreamMonitor(self)
        self.add_api_listener(self._stream_monitor, to_merge=True)
        self.add_api_listener(self.config.api_listener, to_merge=True)


        self._pipeline_setup_complete = True
        logger.info("Pipeline setup complete")
        try:
            for api_listener in self._api_listeners:
                await api_listener.on_pipeline_ready(self)
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
                await asyncio.sleep(0.01)
                if await self._stream_monitor.check_done():
                    print(f'\n!!!!!!!!!!!!!!!!!!!! {time.time()}: input done !!!!!!!!!!!!!!!!!!!\n')
                    await asyncio.sleep(0.01)
                    busy = self.whisper_tool.sound_pending()
                    if busy:
                        print(f'\n!!!!!!!!!!!!!!!!!!!! {time.time()}: Whisper NOT done, waiting !!!!!!!!!!!!!!!!!!!\n')
                    max_wait = 60
                    start_time = time.time()
                    await self.whisper_tool.flush_pending()
                    busy = self.whisper_tool.sound_pending()
                    while busy and time.time() - start_time < max_wait:
                        await asyncio.sleep(0.01)
                        busy = self.whisper_tool.sound_pending()
                    if busy:
                        logger.error(f"Whisper failed to complete pending audio in {max_wait} seconds")
                        raise Exception(f"Whisper failed to complete pending audio in {max_wait} seconds")
                    await asyncio.sleep(0.1)
                    print(f'\n!!!!!!!!!!!!!!!!!!!! {time.time()}: Starting shutdown !!!!!!!!!!!!!!!!!!!\n')
                    await self.shutdown()
                    break
                if self.background_error:
                    from pprint import pformat
                    logger.error("Error callback triggered: %s", pformat(self.background_error))
                    raise Exception(pformat(self.background_error))
        except (KeyboardInterrupt, asyncio.CancelledError):
            logger.info("Shutdown signal received")
            raise

    def prime_pump(self, initial_prompt):
        self.whisper_tool.set_initial_prompt(initial_prompt)
        
    def set_background_error(self, error_dict):
        self.background_error = error_dict
        
    async def start_listener(self):
        """Start the listener streaming audo."""
        await self.whisper_tool.start()
        await self.audio_merge.start()
        await self.listener.start_streaming()
        logger.info("Listener started")

    async def shutdown(self):
        """
        Gracefully shutdown the pipeline.
        Must be called inside the listener's context manager, before it exits.
        """
        # Then shutdown whisper and text listener
        if self.whisper_tool:
            await self.whisper_tool.gracefull_shutdown(self.config.whisper_shutdown_timeout)
            self.whisper_tool = None
                
        try:
            for api_listener in self._api_listeners:
                await api_listener.on_pipeline_shutdown()
        except Exception as e:
            logger.error("pipleline shutdown callback to api_listener error\n%s",
                         traceback.format_exc())
            logger.error(traceback.format_exc())
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

@dataclass
class BlockTracker:
    start_event: DraftStartEvent
    text_events: dict[uuid, TextEvent] = field(default_factory=dict[uuid, TextEvent])
    end_event: Optional[DraftEndEvent] = None
    finalized: Optional[bool] = False

    
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
        self.in_draft_event = None
        self.auto_dump = False
        self.blocks = []

    def is_all_done(self):
        return self.all_done
    
    async def on_pipeline_ready(self, pipeline):
        pass
    
    async def on_pipeline_shutdown(self):
        for block in self.blocks:
            if isinstance(block.start_event, DraftStartEvent) and not block.finalized:
                await self.core.draft_maker.force_end()

    async def check_done(self, dump=False, why="check"): # pragma: no cover
        if dump:
            from pprint import pformat
            print("----- DUMP DUMP DUMP DUMP DUMP ---------------")
            print(f"reason: {why}")
        # everything good case is
        if self.audio_stop and self.in_draft_event is None:
            self.all_done = True
        if self.audio_stop and self.speech_stop:
            # this should always happen since the VAD (or shim)
            # issues a speech stop on audio stop if end of
            # speech has not been detected.
            if dump:
                diff = self.audio_stop.timestamp - self.speech_stop.last_in_speech_chunk_time
                print(f"sound between speech_stop and audio_stop = {diff}")
            if self.last_text:
                diff = self.speech_stop.last_in_speech_chunk_time - self.last_text.audio_end_time 
                if dump:
                    print(f"sound between last text and speech_stop = {diff}")
                    print(f"last_chunk = {self.speech_stop.last_in_speech_chunk_time}")
                    print(f"last_text  = {self.last_text.audio_end_time}")
                if diff > 0.5:
                    # this is not going to be precise. The VAD does buffering andpadding,
                    # it will never report the exact last block
                    await self.core.whisper_tool.flush_pending()
                    self.all_done = False
            else:
                if dump:
                    print(f"Never saw text and audio is stopped, checking whisper for pending")
                    self.all_done = True
        if not dump:
            return self.all_done
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
        print("in_draft_event:")
        print(pformat(self.in_draft_event))
            
        print("----- END END END END DUMP ---------------")
        return self.all_done
        
    async def on_audio_event(self, event):
        if isinstance(event, AudioSpeechStopEvent):
            self.speech_stop = event
            self.speech_start = None
            await self.check_done(dump=self.auto_dump, why="speech stop")
        if isinstance(event, AudioSpeechStartEvent):
            self.speech_start = event
            self.speech_stop = None
            await self.check_done(dump=self.auto_dump, why="speech start")
        if isinstance(event, AudioStopEvent):
            # stream is shutdown, check to see if whisper
            # had done last chunk
            self.audio_stop = event
        
    async def on_draft_event(self, event:DraftEvent):
        if isinstance(event, DraftStartEvent):
            self.in_draft_event = event
            await self.check_done(dump=self.auto_dump, why="DraftStartEvent")
            self.blocks.append(BlockTracker(start_event=event))
        elif isinstance(event, DraftEndEvent):
            self.in_draft_event = None
            await self.check_done(dump=self.auto_dump, why="block stop")
            if len(self.blocks) > 0:
                last_block = self.blocks[-1]
                if last_block.end_event is None:
                    last_block.end_event = event
                    last_block.finalized = True

    async def on_text_event(self, event: TextEvent):
        self.last_text = event
        await self.check_done(dump=self.auto_dump, why="text")
