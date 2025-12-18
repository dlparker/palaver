#!/usr/bin/env python
"""
tests/test_vad_recorder_file.py
Test VAD recorder with pre-recorded audio files
"""

import pytest
import asyncio
import sys
import os
from pathlib import Path
import json
import logging

from palaver.scribe.audio_events import (AudioEvent,
                                         AudioErrorEvent,
                                         AudioStartEvent,
                                         AudioStopEvent,
                                         AudioSpeechStartEvent,
                                         AudioSpeechStopEvent,
                                         AudioChunkEvent,
                                         AudioEventListener,
                                         )
from palaver.utils.top_error import run_with_error_handler
from palaver.scribe.core import PipelineConfig
from palaver.scribe.listener.file_listener import FileListener
from palaver.scribe.script_utils import validate_model_path, scribe_pipeline_context


logger = logging.getLogger("test_code")

@dataclass
class BlockTracker:
    """Tracks a text block from start to end."""
    start_event: StartBlockCommand
    text_events: dict[uuid.UUID, TextEvent] = field(default_factory=dict)
    end_event: Optional[StopBlockCommand] = None
    finalized: Optional[bool] = False


class APIWrapper(ScribeAPIListener):

    def __init__(self, play_sound: bool = False):
        """
        Initialize the API wrapper.

        Args:
            play_sound: If True, play audio through speakers during processing
        """
        super().__init__()
        self.play_sound = play_sound
        self.full_text = ""
        self.blocks = []
        self.text_events = {}
        self.last_block_name = None
        self.stream = None
        self.have_pipeline_ready = False
        self.pipeline = None
        self.have_pipeline_shutdown = False

    async def on_pipeline_ready(self, pipeline):
        self.have_pipeline_ready = True
        self.pipeline = pipeline
        
    async def on_pipeline_shutdown(self):
        """Handle pipeline shutdown - finalize any open blocks."""
        self.have_pipeline_shutdown = True
        await asyncio.sleep(0.01)
        if len(self.blocks) > 0:
            last_block = self.blocks[-1]
            if not last_block.finalized:
                await self.finalize_block(last_block)

    async def on_command_event(self, event: ScribeCommandEvent):
        """Handle command events (start/stop block)."""
        if isinstance(event.command, StartBlockCommand):
            self.blocks.append(BlockTracker(start_event=event))
            await self.handle_text_event(event.text_event)
        elif isinstance(event.command, StopBlockCommand):
            if len(self.blocks) > 0:
                last_block = self.blocks[-1]
                if not last_block.finalized:
                    last_block.end_event = event
                    await self.finalize_block(last_block)

    async def finalize_block(self, block):
        block.finalized = True

    async def handle_text_event(self, event: TextEvent):
        """Handle text events - accumulate text and track in blocks."""
        # Fix bug: was `==` should be `in`
        if event.event_id in self.text_events:
            return
        self.text_events[event.event_id] = event

        if len(self.blocks) > 0:
            last_block = self.blocks[-1]
            if not last_block.finalized:
                last_block.text_events[event.event_id] = event
                logger.info(f"text {event.event_id} added to block")
                for seg in event.segments:
                    self.full_text += seg.text + " "
            else:
                logger.info(f"ignoring text {event.segments}")

    async def on_text_event(self, event: TextEvent):
        """Called when new transcribed text is available."""
        await self.handle_text_event(event)

    async def on_audio_event(self, event: AudioEvent):
        """Handle audio events - optionally play sound and finalize blocks."""
        if isinstance(event, AudioStartEvent):
            pass
        elif isinstance(event, AudioStopEvent):
            logger.info("Got audio stop event %s", event)
            if len(self.blocks) > 0:
                last_block = self.blocks[-1]
                if not last_block.finalized:
                    await self.finalize_block(last_block)
        elif isinstance(event, AudioChunkEvent):
            if self.play_sound:
                if not self.stream:
                    self.stream = sd.OutputStream(
                        samplerate=event.sample_rate,
                        channels=event.channels,
                        blocksize=event.blocksize,
                        dtype=event.datatype,
                    )
                    self.stream.start()
                audio = event.data
                self.stream.write(audio)

class Player:

    def __init__(self):
        self.stream = None
        self.stopped = True
        self.counter = 0
        self.in_speech = False
        
    async def on_audio_event(self, event):
        if isinstance(event, AudioStartEvent):
            self.stream = sd.OutputStream(
                samplerate=event.sample_rate,
                channels=event.channels,
                blocksize=event.blocksize,
                dtype=event.datatype,
            )
            self.stream.start()
            logger.info("Opened stream")
            logger.info(event)
        elif isinstance(event, AudioChunkEvent):
            audio = event.data
            # to swith from mono to stereo, if desired
            #if audio.shape[1] == 1 and :
            #    audio = np.column_stack((audio[:,0], audio[:,0]))            
            if not self.in_speech:
                try:
                    self.stream.write(audio)
                except:
                    logger.info(f"Got error processing \n{event}\n{traceback.format_exc()}")
                    self.stop()
            if self.counter % 1000 == 0:
                logger.info(f"{time.time()} {event}")
            self.counter += 1
        elif isinstance(event, AudioStopEvent):
            logger.info(event)
            self.stop()
        elif isinstance(event, AudioErrorEvent):
            logger.info(f"got error event\n {event.message}")
            self.stop()
        elif isinstance(event, AudioSpeechStartEvent):
            self.in_speech = True
            logger.info(event)
            logger.info("---------- SPEECH STARTS ------------------")
        elif isinstance(event, AudioSpeechStopEvent):
            self.in_speech = False
            logger.info(event)
            logger.info("---------- SPEECH STOP ------------------")
        else:
            logger.info(f"got unknown event {event}")
            self.stop()
        

    def start(self):
        self.stopped = False
        
    def stop(self):
        if self.stream:
            self.stream.stop()
            self.stream.close()
        self.stopped = True

CHUNK_SEC = 0.03

    
async def test_process_note1_file():
    # Verify test file exists
    audio_file = Path(__file__).parent / "audio_samples" / "note1.wav"
    assert audio_file.exists()
    model = Path(__file__).parent.parent / "models" / "ggml-base.en.bin"
    assert model.exists()
    logging.info(f"TESTING FILE INPUT: {audio_file}")




            # Create listener
            file_listener = FileListener(
                audio_file=args.file,
                chunk_duration=0.03,
                simulate_timing=False,
            )

            # Create pipeline config with playback-specific settings
            config = PipelineConfig(
                model_path=args.model,
                api_listener=api_wrapper,
                target_samplerate=16000,
                target_channels=1,
                use_multiprocessing=True,
                require_command_alerts=False,
                vad_silence_ms=3000,
                vad_speech_pad_ms=1000,
                seconds_per_scan=2,
                block_recorder=block_recorder,
            )

            # Run pipeline with automatic context management
            async with scribe_pipeline_context(file_listener, config) as pipeline:
                await pipeline.start_listener()
                await pipeline.run_until_error_or_interrupt()

        # Run with standard error handling
        run_with_error_handler(main_task, logger)
        print("File playback complete")

    
    listener = FileListener(chunk_duration=CHUNK_SEC, simulate_timing=False, files=[audio_file])
    play_sound = os.environ.get("PLAYBACK_DURING_TESTS", False)
    if play_sound:
        player = Player()

    source = listener

    downsampler = DownSampler(target_samplerate=16000, target_channels=1)
    listener.add_event_listener(downsampler)
    vadfilter = VADFilter(listener)
    downsampler.add_event_listener(vadfilter)
    if play_sound:
        # play it
        vadfilter.add_event_listener(player)
    # transcribe it
    def error_callback(error_dict:dict):
        from pprint import pformat
        raise Exception(pformat(error_dict))

    whisper_thread = WhisperThread(model, error_callback, use_mp=True)
    vadfilter.add_event_listener(whisper_thread)

    async def on_command_callback(command_match):
        logger.info(command_match)

    async def on_text_callback(text_event):
        logger.info("in test on_text_callback %s", text_event)
        
    text_printer = TextPrinter(on_text_callback, on_command_callback)
    whisper_thread.add_text_event_listener(text_printer)
    await whisper_thread.start()

    if play_sound:
        player.start()

    async with listener:
        await listener.start_recording()
        while listener._running:
            await asyncio.sleep(0.1)

    if play_sound:
        player.stop()
    await whisper_thread.gracefull_shutdown(3.0)
    logger.info("Playback finished.")
