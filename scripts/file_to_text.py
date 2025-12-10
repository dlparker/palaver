#!/usr/bin/env python3
import sys
import asyncio
import time
from pathlib import Path
import traceback
import logging
from queue import Queue
import sounddevice as sd
import numpy as np

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


logging.basicConfig(stream=sys.stdout, level=logging.WARNING)
logger = logging.getLogger("CLI")

CHUNK_SEC = 0.03

note1_wave = Path(__file__).parent.parent / "tests_slow" / "audio_samples" / "note1_base.wav"

class TextPrinter(TextEventListener):

    def __init__(self, print_progress=False):
        self.full_text = ""
        self.print_progress = print_progress

    async def on_text_event(self, event):
        logger.info("*"*100)
        logger.info("--------Text received---------")
        for seg in event.segments:
            logger.info(seg.text)
            if self.print_progress:
                print(seg.text+ " ")
            self.full_text += seg.text + " "
        logger.info("--------END Text received---------")
        logger.info("*"*100)

    def finish(self):
        print(self.full_text)

class Player:

    def __init__(self, using_vad):
        self.stream = None
        self.stopped = True
        self.counter = 0
        self.using_vad = using_vad
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
            if not self.using_vad or self.in_speech:
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


async def main(path, simulate_timing, model):
    listener = FileListener(chunk_duration=CHUNK_SEC, simulate_timing=simulate_timing, files=[path])
    player = Player(using_vad=False)
    
    source = listener

    downsampler = DownSampler(target_samplerate=16000, target_channels=1)
    listener.add_event_listener(downsampler)
    vadfilter = VADFilter(listener)
    downsampler.add_event_listener(vadfilter)
    # play it
    vadfilter.add_event_listener(player)
    # transcribe it
    def error_callback(error_dict:dict):
        from pprint import pformat
        raise Exception(pformat(error_dict))
    
    whisper_thread = WhisperThread(model, error_callback)
    vadfilter.add_event_listener(whisper_thread)
    text_printer = TextPrinter(print_progress=True)
    whisper_thread.add_text_event_listener(text_printer)
    await whisper_thread.start()

    player.start()

    async with listener:
        await listener.start_recording()
        while listener._running:
            await asyncio.sleep(0.1)

    player.stop()
    await whisper_thread.gracefull_shutdown(3.0)
    text_printer.finish()
    print("Playback finished.")
    
if __name__ == "__main__":
    import argparse 
    parser = argparse.ArgumentParser(description='transcribe test')
    parser.add_argument('--model', nargs='?', const=1, type=str, default="models/ggml-base.en.bin")
    parser.add_argument('-s', '--simulate_timing', action='store_true', 
                       help="Plays samples with simulated input timing")
    parser.add_argument('path', type=str, nargs='?', help="Name of file to play", default=note1_wave)
    args = parser.parse_args()
    asyncio.run(main(path=args.path, simulate_timing=args.simulate_timing, model=args.model))
