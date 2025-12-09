from copy import deepcopy
import logging
import time
import numpy as np
import resampy
import torch
from scipy.signal import resample_poly
from eventemitter import AsyncIOEventEmitter
from palaver.scribe.listener.downsampler import DownSampler

logger = logging.getLogger("VADFilter")

from palaver.scribe.audio_events import (
    AudioEvent,
    AudioStartEvent,
    AudioStopEvent,
    AudioSpeechStartEvent,
    AudioSpeechStopEvent,
    AudioChunkEvent,
    AudioEventListener,
)
logger.info("Loading Silero VAD...")
_vad_model, _vad_utils = torch.hub.load(
    'snakers4/silero-vad',
    'silero_vad',
    trust_repo=True,
    verbose=False
)
_VADIterator = _vad_utils[3]
logger.info("Silero VAD Loading complete")

VAD_THRESHOLD = 0.5          # Default threshold
MIN_SILENCE_MS = 1000         # Default 1.0 seconds
SPEECH_PAD_MS = 1500
VAD_SR = 16000


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
        self._silence_ms = MIN_SILENCE_MS
        self._threshold = VAD_THRESHOLD
        self._speech_pad_ms = SPEECH_PAD_MS
        self._vad = self.create_vad(self._silence_ms, self._threshold, self._speech_pad_ms)
        self._counter = None

    def create_vad(self, silence_ms, threshold, speech_pad_ms):
        """
        Create VAD iterator with specified silence duration and threshold and speech padding,
        sampling rate is not adjustable
        """
        self._silence_ms = silence_ms
        self._threshold = threshold
        self._speech_pad_ms = speech_pad_ms
        logger.info(f"[DEBUG] Creating VAD: silence_threshold={silence_ms}ms, vad_threshold={threshold}")
        vad = _VADIterator(
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
                my_event = AudioSpeechStopEvent(
                    source_id=event.source_id, 
                )
                await self.emitter.emit(AudioEvent, my_event)
                logger.debug("[Speech end on audio end] %s", my_event)
            await self.emitter.emit(AudioEvent, event)
            return
        chunk = event.data[:, 0].copy()
        start_time = time.time()
        vad_chunk = downsample_to_512(chunk, event.sample_rate)
        window = self._vad(vad_chunk, return_seconds=False)
        self._counter = time.time() - start_time
        if window:
            if window.get("start") is not None:
                my_event = AudioSpeechStartEvent(
                    source_id=event.source_id, 
                    silence_period_ms=self._silence_ms,
                    vad_threshold=self._threshold,
                    sampling_rate=VAD_SR,
                    speech_pad_ms=self._speech_pad_ms
                )
                if not self._in_speech:
                    await self.emitter.emit(AudioEvent, my_event)
                    logger.debug("[Speech start] %s", my_event)
                self._in_speech = True
            if window.get("end") is not None:
                my_event = AudioSpeechStopEvent(
                    source_id=event.source_id, 
                )
                if self._in_speech:
                    await self.emitter.emit(AudioEvent, my_event)
                    logger.debug("[Speech end] %s", my_event)
                self._in_speech = False
                logger.debug(f"\n\n------------------------\nTime for speech detection {self._counter}\n------------------\n\n")

        event.in_speech = self._in_speech
        await self.emitter.emit(AudioEvent, event)
            
    def add_event_listener(self, e_listener: AudioEventListener) -> None:
        self.emitter.on(AudioEvent, e_listener.on_audio_event)


