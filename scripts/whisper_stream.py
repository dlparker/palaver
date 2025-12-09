#!/usr/bin/env python3
import asyncio
import time
from pathlib import Path
import traceback
from queue import Queue
import sounddevice as sd
import numpy as np
from pywhispercpp.model import Model

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

class ScriveJob:

    def __init__(self, scrivener, index, final, data):
        self.scrivener = scrivener
        self.index = index
        self.final = final
        self.data = data
        self.done = False
        self.segment = None

    def on_new_segment(self, segment):
        self.segment = segment
        import ipdb; ipdb.set_trace()
        print(f"Job {self.index} In callback: {segment}")
        self.scrivener.job_done(self)

class Scrivener:
    BUFFER_SAMPLES = 30000   # exactly the size you want to feed whisper.cpp at once

    def __init__(self, model):
        self.buffer = np.zeros(self.BUFFER_SAMPLES, dtype=np.float32)
        self.buffer_pos = 0
        self.in_speech = False
        self.job_in_progress = False
        self.job_queue = Queue()
        self.result_queue = Queue()
        self.job_index = 0
        self.process_task = None
        self.model = Model(
            model,
            n_threads=8,
            print_realtime=False,
            print_progress=False,
        )
       
    def push_buffer_job(self, final=False):
        size = self.buffer_pos
        send_buffer = np.zeros(size, dtype=np.float32)
        send_buffer[:] = self.buffer[:size]
        self.buffer_pos = 0
        job = ScriveJob(self, index=self.job_index, final=final, data=send_buffer)
        self.job_index += 1
        self.job_queue.put_nowait(job)
        print(f"pushed job {job.index} onto queue, data size = {size}")
                        
    def job_done(self, job):
        self.result_queue.put_nowait(job)
                        
    async def on_audio_event(self, event: AudioEvent):
        if isinstance(event, AudioSpeechStartEvent):
            self.in_speech = True
            if not self.process_task:
                self.process_task = asyncio.create_task(asyncio.to_thread(self._process_sound_data))
            print(event)
            print("---------- SPEECH STARTS ------------------")
        elif isinstance(event, AudioSpeechStopEvent):
            self.in_speech = False
            print(event)
            print("---------- SPEECH STOP ------------------")
        elif isinstance(event, AudioChunkEvent) and self.in_speech:
            # event.data is already np.ndarray, shape (N, 1), dtype=float32, 16kHz mono
            chunk = event.data.flatten()                # → shape (N,), makes life easier
            samples_needed = self.BUFFER_SAMPLES - self.buffer_pos
            if len(chunk) <= samples_needed:
                # Whole chunk fits → just copy it in
                self.buffer[self.buffer_pos:self.buffer_pos + len(chunk)] = chunk
                self.buffer_pos += len(chunk)
            else:
                # Chunk is bigger than remaining space → fill what we can, process, start new buffer
                self.buffer[self.buffer_pos:] = chunk[:samples_needed]
                self.push_buffer_job()
                # Put the leftover part into the fresh buffer
                leftover = chunk[samples_needed:]
                self.buffer[:len(leftover)] = leftover
                self.buffer_pos = len(leftover)

            # Every time the buffer becomes full → process immediately
            if self.buffer_pos >= self.BUFFER_SAMPLES:
                self.push_buffer_job()

        elif isinstance(event, AudioStopEvent):
            # End of stream → flush whatever is left in the buffer (even if < 30k)
            if self.buffer_pos > 0:
                print(f"{self.buffer_pos} in buffer at audi stop, pushing")
                self.push_buffer_job(final=True)
            start_time = time.time()
            while self.process_task and (self.job_in_progress or self.job_queue.qsize() > 0):
                await asyncio.sleep(0.1)
            if self.process_task:
                self.process_task.cancel()
            print("Transcription finished.")

    def _process_sound_data(self):
        job = self.job_queue.get()
        while job is not None:
            self.job_in_progress = True
            #print(f'\n{time.time()}:Job{job.index}\n')
            self.model.transcribe(
                media=job.data,
                new_segment_callback=job.on_new_segment,
                single_segment=False,
                print_progress=False,
                print_realtime=False,
            )
            self.job_in_progress = False
            #print(f'\n{time.time()}:Done Job{job.index}\n')
            if job.final:
                return
            job = self.job_queue.get()
        

        
    def start(self):
        self.stopped = False
        
    def stop(self):
        self.stopped = True


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


async def main(path, simulate_timing, model):
    listener = FileListener(chunk_duration=CHUNK_SEC, simulate_timing=simulate_timing, files=[path])
    player = Player(using_vad=False)
    scrivener = Scrivener(model)
    
    source = listener

    downsampler = DownSampler(target_samplerate=16000, target_channels=1)
    listener.add_event_listener(downsampler)
    vadfilter = VADFilter(listener)
    downsampler.add_event_listener(vadfilter)
    # play it
    #vadfilter.add_event_listener(player)
    # transcribe it
    vadfilter.add_event_listener(scrivener)


    player.start()

    async with listener:
        await listener.start_recording()
        while listener._running:
            await asyncio.sleep(0.1)

    player.stop()
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
