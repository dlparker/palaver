import asyncio
import logging
import json
import time
import threading
import uuid
import traceback
from io import BytesIO
from typing import Optional
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, field, asdict, fields

import numpy as np
import soundfile as sf

from palaver.scribe.audio_events import (
    AudioEvent, AudioChunkEvent, AudioStartEvent, AudioStopEvent,
    AudioSpeechStartEvent, AudioSpeechStopEvent, AudioErrorEvent,
    AudioEventListener
)
from palaver.scribe.text_events import TextEvent, TextEventListener
from palaver.scribe.api import ScribeAPIListener
from palaver.scribe.command_events import ScribeCommandEvent
from palaver.scribe.scriven.whisper_thread import AudioRingBuffer
from palaver.scribe.api import StartBlockCommand, StopBlockCommand, StartRescanCommand

logger = logging.getLogger("BlockAudioRecorder")

@dataclass
class TextBlock:
    directory: Path
    sound_path: Path
    text_path: Path
    full_events_path: Path
    meta_events_path: Path
    wav_file: Optional[sf.SoundFile] = None
    samplerate: Optional[int] = None
    channels: Optional[int] = None
    timestamp: float = field(default_factory=time.time)
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    events: list[AudioEvent | TextEvent | ScribeCommandEvent] =   field(
        default_factory=list[AudioEvent | TextEvent | ScribeCommandEvent])
    meta_events: list[AudioEvent | TextEvent | ScribeCommandEvent] =   field(
        default_factory=list[AudioEvent | TextEvent | ScribeCommandEvent])


class NpEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, np.integer):
            return int(obj)
        if isinstance(obj, np.floating):
            return float(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        return super(NpEncoder, self).default(obj)


def pre_serialize_events(event_set):
    INCLUDE_DATA = False
    result = []
    for event in event_set:
        if isinstance(event, ScribeCommandEvent):
            save_data = {'classname': str(event.__class__), 'properties': asdict(event)}
            if event.text_event is not None:
                save_data['properties']['text_event'] = {'classname': str(TextEvent), 'properties': asdict(event.text_event)}
            result.append(save_data)
        if not isinstance(event, AudioChunkEvent):
            result.append({'classname': str(event.__class__), 'properties': asdict(event)})
        else:
            save_data = {field.name: getattr(event, field.name) for field in fields(event) if field.name != 'data'}
            if INCLUDE_DATA:
                json_str = json.dumps({'data': event.data}, cls=NpEncoder)
                save_data['data'] = json.loads(json_str)['data']
            else:
                save_data['data'] = "omitted"
            result.append(save_data)
    return result
    
class BlockAudioRecorder(ScribeAPIListener):

    def __init__(self, output_dir: Path):
        super().__init__(split_audio=True)
        self._output_dir = Path(output_dir)
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._rescanning = False
        self._current_block = None
        self._last_block = None
        self._buffer_lock = threading.Lock()
        self._last_speech_start_event = None # only set when no current block 
        self._chunk_ring = AudioRingBuffer(max_seconds=3)
        self._full_text = ""

    async def on_pipeline_ready(self, pipeline):
        pass
    
    async def on_pipeline_shutdown(self):
        if self._current_block:
            try:
                await self._save_block()
                await self._close_block()
            except:
                logger.error(traceback.format_exc())

    def make_new_block(self, event):
        timestamp = datetime.now()
        timestr = timestamp.strftime("%Y%m%d_%H%M%S")
        directory = self._output_dir / f"block-{timestr}"
        directory.mkdir()
        sound_path = directory / "block.wav"
        text_path = directory / "first_draft.txt"
        full_events_path = directory / "full_events.json"
        meta_events_path = directory / "meta_events.json"
        # Open WAV with PCM_16 (not PCM_24) to save space
        self._current_block = TextBlock(directory,
                                        sound_path,
                                        text_path,
                                        full_events_path,
                                        meta_events_path,
                                        None,
                                        timestamp)
        logger.info("\nOpened new text block save directory %s\n", str(directory))

    def get_last_block(self):
        return self._last_block
    
    async def on_command_event(self, event:ScribeCommandEvent):
        command = event.command
        if isinstance(command, StartRescanCommand):
            if self._current_block:
                raise Exception("logic error, got rescan command while in block")
            self._rescanning = True
            return
        if isinstance(command, StartBlockCommand) and self._rescanning:
            return
        if isinstance(command, StopBlockCommand) and self._rescanning:
            self._rescanning = False
            return
        if isinstance(command, StartBlockCommand) or isinstance(command, StopBlockCommand):
            self._last_block = self._current_block
            await self._save_block()
            await self._close_block()
            self._full_text = ""
            if isinstance(command, StopBlockCommand):
                self._rescanning = False
        if isinstance(command, StartBlockCommand):
            self.make_new_block(event)
            if self._last_speech_start_event:
                self._current_block.events.append(self._last_speech_start_event)
                self._current_block.meta_events.append(self._last_speech_start_event)
                if self._chunk_ring.has_data():
                    for event in self._chunk_ring.get_from(self._last_speech_start_event.timestamp):
                        await self.on_audio_chunk_event(event)
                        self._current_block.events.append(event)
                    self._chunk_ring.clear()
            self._current_block.events.append(event)
            self._current_block.meta_events.append(event)
            self._last_speech_start_event = None
                                            
    async def on_text_event(self, event: TextEvent):
        if self._current_block and not self._rescanning:
            self._current_block.meta_events.append(event)
            for seg in event.segments:
                self._full_text += seg.text + " "

    async def on_audio_event(self, event: AudioEvent):
        if not self._current_block and not self._rescanning:
            if isinstance(event, AudioSpeechStartEvent):
                self._last_speech_start_event = event
            if isinstance(event, AudioChunkEvent):
                self._chunk_ring.add(event)
            return
        await super().on_audio_event(event)
        
    async def on_audio_change_event(self, event):
        if self._current_block:
            self._current_block.events.append(event)
            self._current_block.meta_events.append(event)

    async def on_audio_chunk_event(self, event):
        if not self._current_block:
            return
        self._current_block.events.append(event)
        if isinstance(event.channels, tuple):
            channels = event.channels[1]
        else:
            channels = event.channels
        self._current_block.channels  = channels
        samplerate = int(int(event.sample_rate))
        self._current_block.samplerate = samplerate
        if self._current_block.wav_file is None:
            wav_file = sf.SoundFile(
                self._current_block.sound_path,
                mode='w',
                samplerate=samplerate,
                channels=channels,
                subtype='PCM_16'
            )
            leading_seconds = 0.4
            leading_frames = int(samplerate * leading_seconds)
            silence_block = np.zeros((leading_frames, channels), dtype=np.float32)
            wav_file.write(silence_block)
            self._current_block.wav_file = wav_file
            
        with self._buffer_lock:
            # Write audio data to WAV file
            data_to_write = np.concatenate(event.data)
            self._current_block.wav_file.write(data_to_write)

    async def _save_block(self):
        if not self._current_block or self._rescanning:
            return
        block = self._current_block
        with open(block.text_path, 'w') as f:
            f.write(self._full_text)
        with open(block.meta_events_path, 'w') as f:
            json.dump(pre_serialize_events(block.meta_events), f, indent=2)
        with open(block.full_events_path, 'w') as f:
            json.dump(pre_serialize_events(block.events), f, indent=2)
        logger.info("Saved events to files in %s", str(self._current_block.directory))

    async def _close_block(self):
        if not self._current_block or self._rescanning:
            return
        if self._current_block.wav_file:
            trailing_seconds = 0.4
            trailing_frames = int(self._current_block.samplerate * trailing_seconds)
            silence_block = np.zeros((trailing_frames, self._current_block.channels), dtype=np.float32)
            self._current_block.wav_file.write(silence_block)
            self._current_block.wav_file.close()
            
        self._full_text = ""
        self.events = []
        self.meta_events = []
        self._current_block = None
        
    async def stop(self):
        await self._save_block()
        await self._close_block()
        logger.info("BlockAudioRecorder stopped")

    def list_blocks(self):
        return list(self._output_dir.glob("block-*"))

    def get_last_block_name(self):
        block_list = self.list_blocks() 
        if block_list is None:
            return None
        block_list = sorted(block_list)
        block_dir = block_list[-1]
        return block_dir
        
    def get_last_block_wav_path(self):
        block_list = self.list_blocks() 
        if block_list == []:
            return None
        block_list = sorted(block_list)
        block_dir = block_list[-1]
            
        return block_dir / 'block.wav'
        
    def last_has_rescan(self):
        block_dir = self.get_last_block_name()
        if block_dir is None:
            raise Exception('no blocks found')
        rescan = block_dir / "rescan.txt"
        return rescan.exists()
    
    def get_rescan_text_path(self, block_dir=None):
        if block_dir is None:
            block_list = self.list_blocks() 
            if self._last_block is None and block_list == []:
                raise Exception('no last block')
            block_list = sorted(block_list)
            block_dir = block_list[-1]
        else:
            block_dir = Path(block_dir).resolve()
            assert block_dir.exists()
        return block_dir / "rescan.txt"
