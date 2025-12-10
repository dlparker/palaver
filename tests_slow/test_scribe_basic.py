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
from palaver.scribe.listener.file_listener import FileListener
from palaver.scribe.listener.downsampler import DownSampler
from palaver.scribe.listener.vad_filter import VADFilter
from palaver.scribe.text_events import TextEvent, TextEventListener
from palaver.scribe.scriven.whisper_thread import WhisperThread
from palaver.scribe.scriven.detect_commands import DetectCommands
from palaver.scribe.command_match import CommandMatch


logger = logging.getLogger("test_code")


class TextPrinter(TextEventListener):

    def __init__(self, on_text_callback, on_command_callback):
        self.full_text = []
        self.on_text_callback = on_text_callback
        self.on_command_callback = on_command_callback
        self.command_matcher = DetectCommands(self.on_command, self.command_error_callback)

    async def on_command(self, command_match):
        await self.on_command_callback(command_match)

    def command_error_callback(self, error):
        print(f"command matcher error {error}")
        
    async def on_text_event(self, event):
        logger.debug("*"*100)
        logger.debug("--------Text received---------")
        for seg in event.segments:
            logger.debug("in TextPrinter: %s", seg.text)
            await self.on_text_callback(seg.text)
            self.full_text.append(seg.text)
        logger.debug("--------END Text received---------")
        logger.debug("*"*100)
        await self.command_matcher.on_text_event(event)

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
    """
    Test processing note1.wav file through VAD recorder.

    Expected behavior:
    - File should be processed without errors
    - Downsampling should prep stream for VAD
    - VAD should find voice
    - Transcription should produce TextEvents
    - Command detection should detect "start a new note"
    """
    # Verify test file exists
    audio_file = Path(__file__).parent / "audio_samples" / "note1.wav"
    assert audio_file.exists()
    model = Path(__file__).parent.parent / "models" / "ggml-base.en.bin"
    assert model.exists()
    logging.info(f"TESTING FILE INPUT: {audio_file}")
    logging.debug(f"Expected: 4 segments with long note mode workflow")
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
