from copy import deepcopy
import numpy as np
import resampy
from eventemitter import AsyncIOEventEmitter

from palaver.scribe.audio_events import (
    AudioEvent,
    AudioStartEvent,
    AudioChunkEvent,
    AudioEventListener,
)

class DownSampler(AudioEventListener):
    
    def __init__(self, target_samplerate:int, target_channels:int, quality: str = "kaiser_fast"):
        self.target_sr = target_samplerate
        self.target_ch = target_channels
        self.quality = quality  # kaiser_fast is fast & excellent; use "sinc_best" if you want absolute max quality
        self.emitter = AsyncIOEventEmitter()

    async def convert(self, event):
        if isinstance(event, AudioStartEvent):
            new_event = deepcopy(event)
            if self.target_sr is not None and self.target_sr != new_event.sample_rate:
                new_event.sample_rate = self.target_sr
            if self.target_ch is not None and self.target_ch != new_event.channels:  # ← was .target_ch
                new_event.channels = self.target_ch            
            new_event.blocksize = int(event.blocksize * (self.target_sr / event.sample_rate))
            if self.target_ch == 1 and new_event.channels == 2:
                new_event.blocksize / 2
            if self.target_ch == 2 and new_event.channels == 1:
                new_event.blocksize * 2
            return new_event
        
        data = event.data
        src_sr = event.sample_rate
        src_ch = event.channels
        if not isinstance(src_ch, int):
            src_ch = src_ch[1]

        if src_sr != self.target_sr and self.target_sr is not None:
            data = resampy.resample(data, src_sr, self.target_sr, filter=self.quality, axis=0)

        # ── Channel conversion ───────────────────────────
        if src_ch != self.target_ch and self.target_ch is not None:
            if self.target_ch == 1 and src_ch > 1:
                # Downmix to mono (simple average)
                data = data.mean(axis=1, keepdims=True)
            elif src_ch == 1 and self.target_ch > 1:
                # Duplicate mono → stereo/multichannel
                data = np.repeat(data, self.target_ch, axis=1)
            else:
                # Truncate or zero-pad
                if src_ch > self.target_ch:
                    data = data[:, :self.target_ch]
                else:
                    pad = np.zeros((data.shape[0], self.target_ch - src_ch), dtype=np.float32)
                    data = np.hstack((data, pad))

        new_duration = data.shape[0] / self.target_sr

        new_event = AudioChunkEvent(
            source_id=event.source_id,
            data=data,
            duration=new_duration,
            sample_rate=self.target_sr,
            channels=self.target_ch,
            blocksize=data.shape[0],
            datatype="float32",
            in_speech=event.in_speech,
            meta_data=event.meta_data,
            timestamp=event.timestamp,
            event_id=event.event_id,
        )
        return new_event
        
    async def on_audio_event(self, event):
        if isinstance(event, (AudioChunkEvent, AudioStartEvent)):
            new_event = await self.convert(event)
            await self.emitter.emit(AudioEvent, new_event)
        # For all other events (Stop, Error, etc.), forward unchanged
        elif not isinstance(event, (AudioChunkEvent, AudioStartEvent)):
            await self.emitter.emit(AudioEvent, event)
        
    def add_event_listener(self, e_listener: AudioEventListener) -> None:
        self.emitter.on(AudioEvent, e_listener.on_audio_event)


