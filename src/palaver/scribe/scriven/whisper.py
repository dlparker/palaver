import os
import time
import logging
import asyncio
import traceback
from typing import Optional, Dict
from collections.abc import Callable
from queue import Empty, Queue
from dataclasses import dataclass, field
from threading import Event as TEvent
import multiprocessing as mp
from multiprocessing import Process, Queue as MPQueue, Event as MPEvent
import subprocess
import numpy as np
from pywhispercpp.model import Model
from eventemitter import AsyncIOEventEmitter
from palaver.utils.top_error import get_error_handler
from palaver.scribe.audio_events import (AudioEvent,
                                         AudioStartEvent,
                                         AudioStopEvent,
                                         AudioSpeechStartEvent,
                                         AudioSpeechStopEvent,
                                         AudioChunkEvent,
                                         AudioRingBuffer,
                                         )

from palaver.scribe.text_events import TextEvent, TextEventListener

def check_if_nvidia():
    # Run vulkaninfo and capture its output
    result = subprocess.check_output(['vulkaninfo', '--summary'], stderr=subprocess.STDOUT, text=True)
    # Look for specific GPU names in the output
    if "NVIDIA" in result:
        return True

    import os
    if os.environ.get("OVERRIDE_CUDA"):
        return True
    return False

INITIAL_PROMPT = "Rupert, Freddy, Bubba, Babbage, draft, close, break"

logger = logging.getLogger("WhisperWrapper")
PRINTING = False
class ScriveJob:

    def __init__(self, job_id: int, data: np.ndarray, first_chunk: AudioChunkEvent, last_chunk:AudioChunkEvent, initial_prompt:str = None):
        self.job_id = job_id
        self.data = data # numpy array
        self.done = False
        self.first_chunk = first_chunk # contains numpy array
        self.last_chunk = last_chunk # contains numpy array in 
        self.initial_prompt = initial_prompt
        self.duration = None
        self.text_segments = []

class Worker:

    def __init__(self, job_queue: Queue, result_queue: Queue,
                 shutdown_event, model_path):
        self.job_queue = job_queue
        self.result_queue = result_queue
        self.shutdown_event = shutdown_event
        self.model_path = model_path
        self.model = None
        self.have_nvidia = check_if_nvidia()
        print(f"\nHave nvidia check\n {self.have_nvidia}\n\n")
        self.initial_prompt = INITIAL_PROMPT

    def run(self):
        self.model = Model(str(self.model_path),
                           n_threads=8,
                           print_realtime=False,
                           print_progress=False,
                           )

        while not self.shutdown_event.is_set():
            try:
                job = self.job_queue.get(timeout=0.25)
            except Empty:
                #logger.debug("Worker got empty job_queue")
                continue
            if job.job_id == -1:
                logger.info("Worker got negative job id, shutting down")
                return
                
            def on_segment(segment):
                job.text_segments.append(segment)
                
            logger.info("Worker starting job %d, %f seconds of sound",
                        job.job_id, job.last_chunk.timestamp-job.first_chunk.timestamp)
            start_time = time.time()
            if job.initial_prompt is not None:
                prompt = job.initial_prompt
            else:
                prompt = self.initial_prompt
            logger.info('using initial prompt %s', self.initial_prompt)
            self.model.transcribe(media=job.data,
                                  new_segment_callback=on_segment,
                                  single_segment=False,
                                  initial_prompt=prompt,
                                  )
            end_time = time.time()
            job.duration = end_time-start_time
            job.done = True
            # might or might not have data based on callback
            
            self.result_queue.put(job)
            logger.info("Worker finished job %d in %f seconds with segment count %d",
                        job.job_id, job.duration, len(job.text_segments))
            

def thread_worker_wrapper(job_queue: Queue, result_queue: Queue,
                   error_queue: Queue, shutdown_event: TEvent,
                   model_path: os.PathLike[str]):

    try:
        worker = Worker(job_queue, result_queue, shutdown_event, model_path)
        worker.run()
    except Exception as e:
        error_dict = dict(exception=e,
                          traceback=traceback.format_exc())
        logger.error("Whipser thread exiting on error: \n%s", e)
        self._error_queue.put_nowait(error_dict)
        return Error
    logger.info("Worker thread for model %s exiting", model_path)
    return None

def process_worker_wrapper(job_queue: MPQueue, result_queue: MPQueue,
                           error_queue: MPQueue, shutdown_event: MPEvent,
                           model_path: os.PathLike[str]):

    try:
        logger.info(f"Worker process %d for model %s starting", os.getpid(), model_path)
        worker = Worker(job_queue, result_queue, shutdown_event, model_path)
        worker.run()
    except Exception as e:
        error_dict = dict(exception=e,
                          traceback=traceback.format_exc())
        logger.error("Whipser thread exiting on error: \n%s", e)
        error_queue.put_nowait(error_dict)
        return error_dict
    logger.info(f"Worker process %i for model %s exiting", os.getpid(), model_path)
    return None

BUFFER_SAMPLES = 30000   

class WhisperWrapper:

    default_config = {'buffer_samples': BUFFER_SAMPLES,
                      'require_speech': True,
                      'model_path': None,
                      'pre_buffer_seconds': 1.0,
                      }
    config_help = {'buffer_samples': "The number of samples that will be collected before sending to speech transcriber, max",
                   'require_speech': "If true, then transcription will be turned on and off by audio events for speech start and stop",
                   'model_path': "Required path to the whispercpp compatible speech transcription model, e.g. ggml-basic.en.bin",
                   'pre_buffer_seconds': "If require_speech is true, extra samples will be pulled from pre-start history this far back in time",
                   }
    
    def __init__(self, model_path: os.PathLike[str], use_mp=False):
        self._model_path = model_path
        self._config = dict(self.default_config)
        self._config['model_path'] = self._model_path
        self._buffer = np.zeros(self._config['buffer_samples'], dtype=np.float32)
        self._buffer_pos = 0
        self._pre_buffer = None
        self._in_speech = False
        self._first_chunk = None
        self._last_chunk = None
        self._job_id_counter = 0
        self._last_result_id = 0
        self._process = None
        self._initial_prompt = INITIAL_PROMPT
        self._use_mp = use_mp
        if self._use_mp:
            self._job_queue = MPQueue()
            self._result_queue = MPQueue()
            self._error_queue = MPQueue()
            self._shutdown_event = MPEvent()
        else:
            self._job_queue = Queue()
            self._result_queue = Queue()
            self._error_queue = Queue()
            self._shutdown_event = TEvent()
        self._worker_running = False
        self._worker_task = None
        self._sender_task = None
        self._error_task = None
        self._audio_stop_event = None
        self._emitter = AsyncIOEventEmitter()

    def get_config(self):
        return dict(self._config)

    def set_initial_prompt(self, prompt):
        self._initial_prompt = prompt

    def sound_pending(self):
        if self._last_result_id < self._job_id_counter or self._buffer_pos > 0:
            return True
        return False

    async def flush_pending(self, wait_for_result=True, timeout=10.0):
        if self._buffer_pos == 0:
            return False
        last_result = self._last_result_id
        last_job = self._job_id_counter
        logger.info("Flushing buffer on command")
        await self._push_buffer_job()
        if self._job_id_counter == last_job:
            logger.error("Flush failed!")
            raise Exception('flush failed')
        needed_id = self._job_id_counter
        if wait_for_result:
            start_time = time.time()
            while self._last_result_id != needed_id:
                await asyncio.sleep(0.01)
                if time.time() - start_time > timeout:
                    logger.error("Timeout waiting for flushed job")
                    raise Exception("Timeout waiting for flushed job")
            
    async def set_buffer_samples(self, new_samples):
        if self._worker_running:
            raise Exception("cannot do that when worker running")
        self._config['buffer_samples'] = new_samples
        self._buffer = np.zeros(new_samples, dtype=np.float32)
        
    async def start(self):
        if self._config['pre_buffer_seconds']  > 0:
            self._pre_buffer = AudioRingBuffer(max_seconds=self._config['pre_buffer_seconds'])
        else:
            self._pre_buffer = None

        if self._use_mp:
            if self._process:
                raise Exception('double start call')
            args = [self._job_queue,
                    self._result_queue,
                    self._error_queue,
                    self._shutdown_event,
                    self._model_path,
                    ]
            logger.info("Using process")
            self._process = Process(target=process_worker_wrapper, args=args)
            self._process.start()
            self._worker_running = True
        else:
            if self._worker_task:
                raise Exception('double start call')
            logger.info("Using thread")
            coro = asyncio.to_thread(thread_worker_wrapper,
                                     self._job_queue,
                                     self._result_queue,
                                     self._error_queue,
                                     self._shutdown_event,
                                     self._model_path)
            self._worker_task = asyncio.create_task(coro)

            self._worker_running = True
        self._sender_task = get_error_handler().wrap_task(self._sender)
        self._error_task = get_error_handler().wrap_task(self._error_watcher)

    async def gracefull_shutdown(self, timeout=3.0):
        job =  ScriveJob(job_id=-1,
                         data=None,
                         first_chunk=None,
                         last_chunk=None)
        self._job_queue.put_nowait(job)
        start_time = time.time()
        if self._use_mp:
            try:
                while self._process and self._process.is_alive() and time.time() - start_time < timeout:
                    await asyncio.sleep(0.05)
            except:
                msg = f"Whisper worker check got error {traceback.format_exc()}"
                logger.error(msg)
                raise Exception(msg)
            if self._process and self._process.is_alive():
                msg = f"Whisper worker process did not shutdown within requested timeout {timeout}s"
                logger.error(msg)
                raise Exception(msg)
        else:
            try:
                await asyncio.wait_for(self._worker_task, timeout=timeout)
            except asyncio.TimeoutError:
                msg = f"Whisper worker thread did not shutdown within requested timeout {timeout}s"
                logger.error(msg)
                raise Exception(msg)
        
        self._worker_running = False
        sender_time = time.time()
        wait_time = timeout - (sender_time - start_time)
        while True:
            await asyncio.sleep(0.001)
            if self._result_queue.qsize() == 0:
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
        if self._in_speech != value:
            self._in_speech = value
            limit = 16000*0.03
            if not value and self._buffer_pos > limit:
                await self._push_buffer_job()
            else:
                self._buffer = np.zeros(self._config['buffer_samples'], dtype=np.float32)
                self._buffer_pos = 0
                self._first_chunk = None
                self._last_chunk = None
            self._first_chunk = None
            self._last_chunk = None

    async def _handle_chunk(self, event):
        if self._first_chunk is None:
            self._first_chunk = event
        self._last_chunk = event
        # event.data is already np.ndarray, shape (N, 1), dtype=float32, 16kHz mono
        chunk = event.data.flatten()                # → shape (N,), makes life easier
        samples_needed = self._config['buffer_samples'] - self._buffer_pos
        if len(chunk) <= samples_needed:
            # Whole chunk fits → just copy it in
            self._buffer[self._buffer_pos:self._buffer_pos + len(chunk)] = chunk
            self._buffer_pos += len(chunk)
        else:
            # Chunk is bigger than remaining space → fill what we can, process, start new buffer
            self._buffer[self._buffer_pos:] = chunk[:samples_needed]
            await self._push_buffer_job()
            # Put the leftover part into the fresh buffer
            leftover = chunk[samples_needed:]
            self._buffer[:len(leftover)] = leftover
            self._buffer_pos = len(leftover)
        # Every time the buffer becomes full → process immediately
        if self._buffer_pos >= self._config['buffer_samples']:
            await self._push_buffer_job()
        
    async def on_audio_event(self, event):
        if not self._worker_running:
            return
        if isinstance(event, AudioSpeechStartEvent):
            await self.set_in_speech(True)
        elif isinstance(event, AudioSpeechStopEvent):
            if self._last_chunk:
                logger.info("End of speech %s should push, last chunk is %s", event, self._last_chunk)
            else:
                logger.info("End of speech %s no push, last chunk is None", event)
            await self.set_in_speech(False) # does push if needed
        elif isinstance(event, AudioChunkEvent) and not self._in_speech and self._pre_buffer is not None:
            self._pre_buffer.add(event)
        elif isinstance(event, AudioChunkEvent) and self._in_speech:
            if self._pre_buffer and self._pre_buffer.has_data():
                # We collected some while not in speech, get those
                # and process those first. Helps avoid dropped
                # words at the beginning when doing VAD
                #print(f"\nPrepending {len(self._pre_buffer.buffer)}\n")
                for pre_event in self._pre_buffer.get_all(clear=True):
                    await self._handle_chunk(pre_event)
            await self._handle_chunk(event)
        elif isinstance(event, AudioStopEvent):
            logger.info("End of audio %s last chunk is %s", event, self._last_chunk)
            self._audio_stop_event = event
            await self.set_in_speech(False) # does push if needed
        
    def add_text_event_listener(self, e_listener: TextEventListener) -> None:
        self._emitter.on(TextEvent, e_listener.on_text_event)
        
    async def stop(self):
        self._shutdown_event.set()
        if self._use_mp:
            if self._process:
                self._process.join()
                self._process = None
        else:
            res = await self._worker_task
            if res:
                logger.error("Worker task returned error %s", res)
            self._worker_task = None
        self._worker_running = False
        if self._sender_task:
            try:
                self._sender_task.cancel()
            finally:
                self._sender_task = None
        if self._error_task:
            try:
                self._error_task.cancel()
            finally:
                self._error_task = None

    async def _push_buffer_job(self):
        if self._buffer_pos == 0:
            return
        size = self._buffer_pos
        self._job_id_counter += 1
        job =  ScriveJob(job_id=self._job_id_counter,
                         data=np.zeros(size, dtype=np.float32),
                         first_chunk = self._first_chunk,
                         last_chunk = self._last_chunk,
                         initial_prompt=self._initial_prompt)
        job.data[:] = self._buffer[:size]
        self._buffer_pos = 0
        self._first_chunk = None
        self._last_chunk = None
        self._job_queue.put_nowait(job)
        logger.info("pushed job %d, %d pending", job.job_id, job.job_id-self._last_result_id)
        
    async def _sender(self):
        # this is wrapped in an error handler when created, so just let
        # errors propogate
        while self._worker_running:
            while self._result_queue.qsize() == 0:
                try:
                    await asyncio.sleep(0.001)
                except asyncio.exceptions.CancelledError:
                    break
            if self._result_queue.qsize() > 0:                
                job = self._result_queue.get()
                self._last_result_id = job.job_id
                logger.info("Dequeued finished job %d in %f seconds with segment count %d, %d jobs pending",
                            job.job_id, job.duration, len(job.text_segments),
                            self._job_id_counter - 1 - self._last_result_id)
                if len(job.text_segments) == 1 and job.text_segments[0].text == "[BLANK_AUDIO]":
                    logger.info("\n-- blank segment ---\n")
                elif len(job.text_segments) > 0:
                    text = " ".join(segment.text for segment in job.text_segments)
                    event = TextEvent(text=text,
                                      audio_source_id=job.first_chunk.source_id,
                                      audio_start_time=job.first_chunk.timestamp,
                                      audio_end_time=job.last_chunk.timestamp)
                    logger.info("Emitting event %s", event)
                    await self._emitter.emit(TextEvent, event)

        self._sender_task = None

    async def _error_watcher(self):
        # this is wrapped in an error handler when created, so just let
        # errors propogate
        while self._worker_running:
            while self._error_queue.qsize() == 0:
                try:
                    await asyncio.sleep(0.01)
                except asyncio.exceptions.CancelledError:
                    break
            if self._error_queue.qsize() > 0:
                error_dict = self._error_queue.get()
                raise error_dict['exception']
            
        
