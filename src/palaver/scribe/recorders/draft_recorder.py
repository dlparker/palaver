import asyncio
import logging
import json
import time
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, field, asdict, fields


import numpy as np
import soundfile as sf
from palaver.scribe.audio_events import AudioEvent, AudioChunkEvent, AudioRingBuffer
from palaver.scribe.api import ScribeAPIListener
from palaver.scribe.text_events import TextEvent
from palaver.scribe.draft_events import DraftEvent, DraftStartEvent, DraftEndEvent

logger = logging.getLogger("DraftRecorder")


class DraftRecorder(ScribeAPIListener):

    def __init__(self, output_dir: Path, chunk_ring_seconds=5):
        super().__init__(split_audio=False)
        self._output_dir = Path(output_dir)
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._current_dir = None
        self._current_draft = None
        self._chunk_ring = AudioRingBuffer(max_seconds=chunk_ring_seconds)
        self._wav_file = None
        self._events = []

    async def on_pipeline_ready(self, pipeline):
        pass
    
    async def on_pipeline_shutdown(self):
        await self._close()

    async def on_audio_event(self, event: AudioEvent):
        if not self._current_draft:
            if isinstance(event, AudioChunkEvent):
                self._chunk_ring.add(event)
            return
        
        if not isinstance(event, AudioChunkEvent):
            self._events.append(event)
            return

        async def write_from_event(event):
            data_to_write = np.concatenate(event.data)
            logger.debug("Saving  %d samples to wav file", len(data_to_write))
            self._wav_file.write(data_to_write)
        if self._wav_file is None:
            await self._open_wav_file(event)
            if self._chunk_ring.has_data():
                for event in self._chunk_ring.get_from(self._current_draft.timestamp-3):
                    await write_from_event(event)
                self._chunk_ring.clear()
        await write_from_event(event)

    async def _open_wav_file(self, event:AudioChunkEvent):
        if isinstance(event.channels, tuple):
            channels = event.channels[1]
        else:
            channels = event.channels
        samplerate = int(int(event.sample_rate))
        self._wav_file = sf.SoundFile(
            self._current_dir / "draft.wav",
            mode='w',
            samplerate=samplerate,
            channels=channels,
            subtype='PCM_16'
        )
        leading_seconds = 0.4
        leading_frames = int(samplerate * leading_seconds)
        silence_block = np.zeros((leading_frames, channels), dtype=np.float32)
        self._wav_file.write(silence_block)

    async def on_draft_event(self, event:DraftEvent):
        self._events.append(event)
        if isinstance(event, DraftStartEvent) or isinstance(event, DraftEndEvent):
            await self._close()
        if isinstance(event, DraftStartEvent):
            self._current_draft = event.draft
            timestamp = datetime.fromtimestamp(self._current_draft.timestamp)
            timestr = timestamp.strftime("%Y-%m0%d_%H-%M-%S-%f")
            directory = self._output_dir / f"draft-{timestr}"
            directory.mkdir()
            self._current_dir = directory

    async def on_text_event(self, event:TextEvent):
        self._events.append(event)
        
    async def _close(self):
        if self._current_dir:
            if self._wav_file:
                self._wav_file.close()
                self._wav_file = None
            if self._current_draft:
                text_path = self._current_dir / "first_draft.txt"
                with open(text_path, 'w') as f:
                    f.write(self._current_draft.full_text)
                json_draft_path = self._current_dir / "first_draft.json"
                json_draft = {'classname': str(self._current_draft.__class__), 'properties': asdict(self._current_draft)}
                with open(json_draft_path, 'w') as f:
                    json.dump(json_draft, f, indent=2)
                self._current_draft = None
            if len(self._events) > 0:
                meta_events_path = self._current_dir / "meta_events.json"
                data = []
                for event in self._events:
                    save_data = {'classname': str(event.__class__), 'properties': asdict(event)}
                    data.append(save_data)
                with open(meta_events_path, 'w') as f:
                    json.dump(data, f, indent=2)
                self._events = []
            self._current_dir = None
