from copy import deepcopy
import logging
import time
import numpy as np
import resampy
import torch
import asyncio
from silero_vad import load_silero_vad, VADIterator
from scipy.signal import resample_poly
from eventemitter import AsyncIOEventEmitter
from palaver.scribe.audio.downsampler import DownSampler

logger = logging.getLogger("VADFilter")

from palaver_shared.audio_events import (
    AudioEvent,
    AudioStartEvent,
    AudioStopEvent,
    AudioSpeechStartEvent,
    AudioSpeechStopEvent,
    AudioChunkEvent,
    AudioEventListener,
)

# This loads the model without any network calls (uses bundled local files)
_vad_model = load_silero_vad()

# VAD sample rate is a technical constraint (Silero VAD operates at 16kHz)
VAD_SR = 16000

# Note: VAD configuration defaults (threshold, silence_ms, speech_pad_ms)
# are now defined in PipelineConfig and passed in via reset() method


def downsample_to_512(chunk: np.ndarray, in_samplerate) -> np.ndarray:
    """Downsample to exactly 512 samples @ 16 kHz for VAD"""
    down = resample_poly(chunk, VAD_SR, in_samplerate)
    if down.shape[0] > 512:
        down = down[:512]
    elif down.shape[0] < 512:
        down = np.pad(down, (0, 512 - down.shape[0]))
    return down.astype(np.float32)


class VADFilter(AudioEventListener):
    
    def __init__(self, sound_source):
        self.sound_source = sound_source
        self.emitter = AsyncIOEventEmitter()
        self._in_speech = False
        # Initial values - will be overwritten by reset() call from PipelineConfig
        self._silence_ms = 2000
        self._threshold = 0.5
        self._speech_pad_ms = 1500
        self._vad = self.create_vad(self._silence_ms, self._threshold, self._speech_pad_ms)
        self._counter = None
        self._speech_start_time = None
        self._last_in_speech_chunk = None

    def reset(self, silence_ms, threshold, speech_pad_ms):
        self._vad.reset_states()
        self._vad = self.create_vad(silence_ms, threshold, speech_pad_ms)
        
    def create_vad(self, silence_ms, threshold, speech_pad_ms):
        """
        Create VAD iterator with specified silence duration and threshold and speech padding,
        sampling rate is not adjustable
        """
        self._silence_ms = silence_ms
        self._threshold = threshold
        self._speech_pad_ms = speech_pad_ms
        logger.info(f"Creating VAD: silence_threshold={silence_ms}ms, vad_threshold={threshold}")
        vad = VADIterator(
            _vad_model,
            threshold=self._threshold,
            sampling_rate=VAD_SR,
            min_silence_duration_ms=self._silence_ms,
            speech_pad_ms=self._speech_pad_ms
        )
        return vad
        
    async def on_audio_event(self, event):
        if not isinstance(event, AudioChunkEvent):
            if isinstance(event, AudioStopEvent) and self._in_speech:
                end_time = self._last_in_speech_chunk.timestamp - (self._speech_pad_ms/1000.0)
                my_event = AudioSpeechStopEvent(source_id=event.source_id,
                                                timestamp=event.timestamp,
                                                stream_start_time=event.stream_start_time,
                                                speech_start_time=self._speech_start_time,
                                                last_in_speech_chunk_time=end_time,
                                                )
                await self.emitter.emit(AudioEvent, my_event)
                logger.info("[Speech end on audio end] %s", my_event)
            event.in_speech = self._in_speech
            await self.emitter.emit(AudioEvent, event)
            return
        chunk = event.data[:, 0].copy()
        start_time = time.time()
        vad_chunk = downsample_to_512(chunk, event.sample_rate)
        window = self._vad(vad_chunk, return_seconds=False)
        self._counter = time.time() - start_time
        if window:
            if window.get("start") is not None:
                self._speech_start_time = event.timestamp
                my_event = AudioSpeechStartEvent(
                    source_id=event.source_id,
                    timestamp=event.timestamp,
                    stream_start_time=event.stream_start_time,
                    speech_start_time=self._speech_start_time,
                    silence_period_ms=self._silence_ms,
                    vad_threshold=self._threshold,
                    sampling_rate=VAD_SR,
                    speech_pad_ms=self._speech_pad_ms
                )
                if not self._in_speech:
                    await self.emitter.emit(AudioEvent, my_event)
                    logger.info("[Speech start] %s", my_event)
                self._in_speech = True
                await self.sound_source.set_in_speech(True)
                event.in_speech = True
            if window.get("end") is not None:
                end_time = self._last_in_speech_chunk.timestamp - (self._speech_pad_ms/1000.0)
                my_event = AudioSpeechStopEvent(
                    source_id=event.source_id,
                    timestamp=event.timestamp,
                    stream_start_time=event.stream_start_time,
                    speech_start_time=self._speech_start_time,
                    last_in_speech_chunk_time=end_time,
                )
                self._speech_start_time = None
                await self.emitter.emit(AudioEvent, my_event)
                logger.info("[Speech end] %s", my_event)
                self._in_speech = False
                await self.sound_source.set_in_speech(False)
                event.in_speech = False

        if self._in_speech:
            self._last_in_speech_chunk = event
        event.in_speech = self._in_speech
        event.speech_start_time = self._speech_start_time
        await self.emitter.emit(AudioEvent, event)
            
    def add_audio_event_listener(self, e_listener: AudioEventListener) -> None:
        self.emitter.on(AudioEvent, e_listener.on_audio_event)


