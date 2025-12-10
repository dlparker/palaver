import os
import time
import logging
import asyncio
import traceback
from typing import Optional, Dict
from collections.abc import Callable
from queue import Empty, Queue
from dataclasses import dataclass, field
from threading import Event
import numpy as np
from pywhispercpp.model import Model
from eventemitter import AsyncIOEventEmitter
from palaver.scribe.audio_events import (AudioEvent,
                                         AudioStartEvent,
                                         AudioStopEvent,
                                         AudioSpeechStartEvent,
                                         AudioSpeechStopEvent,
                                         AudioChunkEvent,
                                         )

from palaver.scribe.text_events import VTTSegment, TextEvent, TextEventListener


logger = logging.getLogger("WhisperThreadedBatch")
PRINTING = False
class ScriveJob:

    def __init__(self, job_id: int, data: np.ndarray, first_chunk: AudioChunkEvent, last_chunk:AudioChunkEvent):
        self.job_id = job_id
        self.data = data # numpy array
        self.done = False
        self.first_chunk = first_chunk # contains numpy array
        self.last_chunk = last_chunk # contains numpy array in 
        self.duration = None
        self.text_segments = []

class Worker:

    def __init__(self, job_queue: Queue, result_queue: Queue,
                 shutdown_event: Event, model_path):
        self.job_queue = job_queue
        self.result_queue = result_queue
        self.shutdown_event = shutdown_event
        self.model_path = model_path
        self.model = None

            
    def run(self):
        self.model = Model(self.model_path, n_threads=8,
                           print_realtime=False,
                           print_progress=False,
                           )

        while not self.shutdown_event.is_set():
            try:
                job = self.job_queue.get(timeout=0.25)
            except Empty:
                continue
            if job.job_id == -1:
                logger.info("Worker got negative job id, shutting down")
                return
                
            logger.info("Worker starting job %d, %f seconds of sound",
                        job.job_id, job.last_chunk.timestamp-job.first_chunk.timestamp)
            def on_segment(segment):
                job.text_segments.append(segment)
                
            start_time = time.time()
            self.model.transcribe(media=job.data, new_segment_callback=on_segment,  single_segment=False)
            end_time = time.time()
            job.duration = end_time-start_time
            job.done = True
            # might or might not have data based on callback
            if PRINTING:
                print('\n\n---------------\n')
                print("      JOB DONE      ")
                print(f"job_id={job.job_id}, ")
                print(f"first_chunk.timestamp = {job.first_chunk.timestamp}, last_chunk.timestamp = {job.last_chunk.timestamp}")
                print(f"audio duration={job.last_chunk.timestamp-job.first_chunk.timestamp}")
                print(f"datasize={len(job.data)}")
                print(f"segments={job.text_segments}")
                print('\n---------------\n\n')
            
            self.result_queue.put(job)
            logger.info("Worker finished job %d in %f seconds with segment count %d",
                        job.job_id, job.duration, len(job.text_segments))
            

def worker_wrapper(job_queue: Queue, result_queue: Queue,
                   error_queue: Queue, shutdown_event: Event,
                   model_path: os.PathLike[str]):

    try:
        worker = Worker(job_queue, result_queue, shutdown_event, model_path)
        worker.run()
    except Exception as e:
        error_dict = dict(exception=e,
                          traceback=traceback.format_exc())
        logger.error("Whipser thread exiting on error: \n%s", e)
        self.error_queue.put_nowait(error_dict)
        return Error
    logger.info("Worker thread for model %s exiting", model_path)
    return None

BUFFER_SAMPLES = 30000   

class WhisperThread:

    default_config = {'buffer_samples': BUFFER_SAMPLES,
                      'require_speech': True,
                      'model_path': None,
                      'pre_buffer_samples': 0,
                      'error_callback': None
                      }
    config_help = {'buffer_samples': "The number of samples that will be collected before sending to speech transcriber, max",
                   'require_speech': "If true, then transcription will be turned on and off by audio events for speech start and stop",
                   'model_path': "Required path to the whispercpp compatible speech transcription model, e.g. ggml-basic.en.bin",
                   'pre_buffer_samples': "If require_speech is true, how many extra samples will be pulled from pre-start history",
                   'error_callback': "Callable that accepts a dictionary of error info when a background error occurs",
                   }
    
    def __init__(self, model_path: os.PathLike[str], error_callback: Callable[[dict], None]):
        self.model_path = model_path
        self.error_callback = error_callback
        self.config = dict(self.default_config)
        self.config['model_path'] = self.model_path
        self.config['error_callback'] = self.error_callback
        self.buffer = np.zeros(self.config['buffer_samples'], dtype=np.float32)
        self.buffer_pos = 0
        self.in_speech = False
        self.first_chunk = None
        self.last_chunk = None
        self.next_job_id = 0
        self.job_queue = Queue()
        self.result_queue = Queue()
        self.error_queue = Queue()
        self.shutdown_event = Event()
        self.worker_task = None
        self.sender_task = None
        self.error_task = None
        self.emitter = AsyncIOEventEmitter()

    def get_config(self):
        return self.config
    
    async def update_config(self, new_config):
        if new_config['model_path'] != self.model_path:
            raise Exception("dynmaic model change not yet implemented")
        if new_config['buffer_samples'] != self.config['buffer_samples']:
            raise Exception("dynmaic buffer size change yet implemented")
        if new_config['pre_buffer_samples'] != self.config['pre_buffer_samples']:
            raise Exception("dynmaic pre_buffer size change yet implemented")
        if new_config['require_speech'] != self.config['require_speech']:
            self.config['require_speech'] = new_config['require_speech']
            await self.set_in_speech(new_config['require_speech'])
        if new_config['error_callback'] != self.config['error_callaback']:
            self.config['error_callback'] = new_config['error_callback']
            self.error_callback = new_config['error_callback']
        
    async def start(self):
        coro = asyncio.to_thread(worker_wrapper,
                               self.job_queue,
                               self.result_queue,
                               self.error_queue,
                               self.shutdown_event,
                               self.model_path)
                           
        self.worker_task = asyncio.create_task(coro)
        self.sender_task = asyncio.create_task(self.sender())
        self.error_task = asyncio.create_task(self.error_watcher())

    async def gracefull_shutdown(self, timeout=3.0):
        job =  ScriveJob(job_id=-1,
                         data=None,
                         first_chunk=None,
                         last_chunk=None)
        self.job_queue.put_nowait(job)
        start_time = time.time()
        try:
            await asyncio.wait_for(self.worker_task, timeout=timeout)
        except asyncio.TimeoutError:
            msg = f"Whisper worker did not shutdown within requested timeout {timeout}s"
            logger.error(msg)
            raise Exception(msg)
        sender_time = time.time()
        wait_time = timeout - (sender_time - start_time)
        while True:
            await asyncio.sleep(0.001)
            if self.result_queue.qsize() == 0:
                break
            if time.time() - sender_time >= wait_time:
                msg = f"Whisper sender task did not collect last result within requested timeout {timeout}s"
                logger.error(msg)
                raise Exception(msg)
            
        await self.stop()
        return

    # This is called by audio event code when "require_speech" is true,
    # or can be managed manually to turn transcription on and off
    async def set_in_speech(self, value):
        if self.in_speech != value:
            self.in_speech = value
            if not value and self.buffer_pos > 1000:
                await self.push_buffer_job()
            else:
                self.buffer = np.zeros(self.config['buffer_samples'], dtype=np.float32)
                self.buffer_pos = 0
                self.first_chunk = None
                self.last_chunk = None
            self.first_chunk = None
            self.last_chunk = None
                
    async def on_audio_event(self, event):
        if not self.worker_task:
            return
        if isinstance(event, AudioSpeechStartEvent):
            await self.set_in_speech(True)
        elif isinstance(event, AudioSpeechStopEvent):
            await self.set_in_speech(False)
        elif isinstance(event, AudioChunkEvent) and self.in_speech:
            if self.first_chunk is None:
                self.first_chunk = event
            self.last_chunk = event
            # event.data is already np.ndarray, shape (N, 1), dtype=float32, 16kHz mono
            chunk = event.data.flatten()                # → shape (N,), makes life easier
            samples_needed = self.config['buffer_samples'] - self.buffer_pos
            if len(chunk) <= samples_needed:
                # Whole chunk fits → just copy it in
                self.buffer[self.buffer_pos:self.buffer_pos + len(chunk)] = chunk
                self.buffer_pos += len(chunk)
            else:
                # Chunk is bigger than remaining space → fill what we can, process, start new buffer
                self.buffer[self.buffer_pos:] = chunk[:samples_needed]
                await self.push_buffer_job()
                # Put the leftover part into the fresh buffer
                leftover = chunk[samples_needed:]
                self.buffer[:len(leftover)] = leftover
                self.buffer_pos = len(leftover)
            # Every time the buffer becomes full → process immediately
            if self.buffer_pos >= self.config['buffer_samples']:
                await self.push_buffer_job()
        elif isinstance(event, AudioStopEvent):
            await self.set_in_speech(False)
        
    async def push_buffer_job(self):
        if self.buffer_pos == 0:
            return
        size = self.buffer_pos
        job =  ScriveJob(job_id=self.next_job_id,
                         data=np.zeros(size, dtype=np.float32),
                         first_chunk = self.first_chunk,
                         last_chunk = self.last_chunk)
        job.data[:] = self.buffer[:size]
        self.next_job_id += 1
        self.buffer_pos = 0
        self.first_chunk = None
        self.last_chunk = None
        self.job_queue.put_nowait(job)
        if PRINTING:
            print('\n\n---------------\n')
            print(f"job_id={job.job_id}, ")
            print(f"first_chunk.timestamp = {job.first_chunk.timestamp}, last_chunk.timestamp = {job.last_chunk.timestamp}")
            print(f"audio duration={job.last_chunk.timestamp-job.first_chunk.timestamp}")
            print(f"datasize={len(job.data)}")
            print('\n---------------\n\n')
        
    async def sender(self):
        try:
            while self.worker_task:
                while self.result_queue.qsize() == 0:
                    try:
                        await asyncio.sleep(0.001)
                    except asyncio.exceptions.CancelledError:
                        break
                if self.result_queue.qsize() > 0:                
                    job = self.result_queue.get()
                    logger.info("Dequeued finished job %d in %f seconds with segment count %d",
                                job.job_id, job.duration, len(job.text_segments))
                    if len(job.text_segments) == 1 and job.text_segments[0].text == "[BLANK_AUDIO]":
                        logger.info("\n-- blank segment ---\n")
                    elif len(job.text_segments) > 0:
                        segments = []
                        for segment in job.text_segments:
                            segments.append(VTTSegment(start_ms=segment.t0,
                                                       end_ms=segment.t1,
                                                       text=segment.text))
                        event = TextEvent(segments=segments,
                                          audio_source_id=job.first_chunk.source_id,
                                          audio_start_time=job.first_chunk.timestamp,
                                          audio_end_time=job.last_chunk.timestamp)
                        logger.info("Emitting event %s", event)
                        await self.emitter.emit(TextEvent, event)
        except:
            logger.error("sender task got error: \n%s", traceback.format_exc())
        finally:
            self.sender_task = None

    async def error_watcher(self):
        # Raise exception in main thread when an error block arrives from woker thread
        try:
            while self.worker_task:
                while self.error_queue.qsize() == 0:
                    try:
                        await asyncio.sleep(0.01)
                    except asyncio.exceptions.CancelledError:
                        break
                if self.error_queue.qsize() > 0:
                    error_dict = self.error_queue.get()
                    self.error_callaback(error_dict)
        except Exception as e:
            error_dict = dict(exception=e,
                              traceback=traceback.format_exc())
            logger.error("error_watcher task got error: \n%s", traceback.format_exc())
        finally:
            self.error_task = None
            
    async def stop(self):
        self.shutdown_event.set()
        res = await self.worker_task
        if res:
            logger.error("Worker task returned error %s", res)
        self.worker_task = None
        if self.sender_task:
            try:
                self.sender_task.cancel()
            finally:
                self.sender_task = None
        if self.error_task:
            try:
                self.error_task.cancel()
            finally:
                self.error_task = None
            
    def add_text_event_listener(self, e_listener: TextEventListener) -> None:
        self.emitter.on(TextEvent, e_listener.on_text_event)

        
