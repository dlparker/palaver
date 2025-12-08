#!/usr/bin/env python3
import asyncio
import time
from pathlib import Path
import traceback
import sounddevice as sd
import numpy as np

from palaver.scribe.audio_events import (
    AudioEvent,
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

CHUNK_SEC = 0.03

note1_wave = Path(__file__).parent.parent / "tests_slow" / "audio_samples" / "note1_base.wav"

class Player:

    def __init__(self):
        self.stream = None
        self.stopped = True
        self.counter = 0
        
    async def on_event(self, event):
        if isinstance(event, AudioStartEvent):
            self.stream = sd.OutputStream(
                samplerate=event.sample_rate,
                channels=event.channels,
                blocksize=event.blocksize,
                dtype=event.datatype,
            )
            self.stream.start()
            print("Opened stream")
            print(event)
        elif isinstance(event, AudioChunkEvent):
            audio = event.data
            # to swith from mono to stereo, if desired
            #if audio.shape[1] == 1 and :
            #    audio = np.column_stack((audio[:,0], audio[:,0]))            
            try:
                self.stream.write(audio)
            except:
                print(f"Got error processing \n{event}\n{traceback.format_exc()}")
                self.stop()
            if self.counter % 10 == 0:
                print(f"{time.time()} {event}")
            self.counter += 1
        elif isinstance(event, AudioStopEvent):
            print(event)
            self.stop()
        elif isinstance(event, AudioErrorEvent):
            print(f"got error event\n {event.message}")
            self.stop()
        elif isinstance(event, AudioSpeechStartEvent):
            print(event)
            print("---------- SPEECH STARTS ------------------")
        elif isinstance(event, AudioSpeechStopEvent):
            print(event)
            print("---------- SPEECH STOP ------------------")
        else:
            print(f"got unknown event {event}")
            self.stop()
        

    def start(self):
        self.stopped = False
        
    def stop(self):
        if self.stream:
            self.stream.stop()
            self.stream.close()
        self.stopped = True

async def main(path, downsample, vad):
    listener = FileListener(files=[path], chunk_duration=CHUNK_SEC)
    player = Player()

    source = listener

    if downsample or vad:
        downsampler = DownSampler(source, target_samplerate=16000, target_channels=1)
        listener.add_event_listener(downsampler)
        source = downsampler

        if vad:
            vadfilter = VADFilter(source)
            downsampler.add_event_listener(vadfilter)
            source = vadfilter

    # Only ONE connection to player
    source.add_event_listener(player)

    player.start()

    async with listener:
        await listener.start_recording()
        while listener._running:
            await asyncio.sleep(0.1)

    player.stop()
    print("Playback finished.")
    
if __name__ == "__main__":
    import argparse 
    parser = argparse.ArgumentParser(description='Playback demo for sound files')
    parser.add_argument('-d', '--downsample', action='store_true', 
                       help="Apply VAD compliant downsample to file")
    parser.add_argument('-v', '--vad', action='store_true', 
                       help="Apply VAD detection (implies --downsample)")
    parser.add_argument('path', type=str, nargs='?', help="Name of file to play", default=note1_wave)
    args = parser.parse_args()
    asyncio.run(main(path=args.path,  downsample=args.downsample, vad=args.vad))
