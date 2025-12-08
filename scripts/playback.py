#!/usr/bin/env python3
import asyncio
import time
from pathlib import Path
import traceback
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

CHUNK_SEC = 0.03

note1_wave = Path(__file__).parent.parent / "tests_slow" / "audio_samples" / "note1_base.wav"

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
            print("Opened stream")
            print(event)
        elif isinstance(event, AudioChunkEvent):
            audio = event.data
            # to swith from mono to stereo, if desired
            #if audio.shape[1] == 1 and :
            #    audio = np.column_stack((audio[:,0], audio[:,0]))            
            if not self.using_vad or self.in_speech:
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
            self.in_speech = True
            print(event)
            print("---------- SPEECH STARTS ------------------")
        elif isinstance(event, AudioSpeechStopEvent):
            self.in_speech = False
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

async def main(path, simulate_timing, downsample, vad):
    listener = FileListener(chunk_duration=CHUNK_SEC, simulate_timing=simulate_timing, files=[path])
    player = Player(using_vad=vad)

    source = listener

    if downsample:
        downsampler = DownSampler(target_samplerate=16000, target_channels=1)
        listener.add_event_listener(downsampler)
        source = downsampler

    elif vad:
        # it uses a downsampler, but it emits the original
        # audio samples, not the downsampled ones
        vadfilter = VADFilter(source)
        listener.add_event_listener(vadfilter)
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
    parser.add_argument('-s', '--simulate_timing', action='store_true', 
                       help="Plays samples with simulated input timing")
    parser.add_argument('-d', '--downsample', action='store_true', 
                       help="Apply VAD compliant downsample to file")
    parser.add_argument('-v', '--vad', action='store_true', 
                       help="Apply VAD detection (implies --downsample)")
    parser.add_argument('path', type=str, nargs='?', help="Name of file to play", default=note1_wave)
    args = parser.parse_args()
    asyncio.run(main(path=args.path, simulate_timing=args.simulate_timing, downsample=args.downsample, vad=args.vad))
