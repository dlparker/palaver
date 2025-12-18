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
    AudioEventListener, AudioRingBuffer,
)
from palaver.scribe.text_events import TextEvent, TextEventListener
from palaver.scribe.api import ScribeAPIListener
from palaver.scribe.command_events import ScribeCommandEvent
from palaver.scribe.api import StartBlockCommand, StopBlockCommand

logger = logging.getLogger("BlockAudioRecorder")
@dataclass
class BlockFileset:
    top_dir: Path
    sound_path: Path
    meta_events_path: Path
    full_events_path: Path
    first_draft_path: Path
    rescan_directory: Path
    rescan_meta_events_path: Path
    rescan_text_path: Path

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

    def __init__(self, output_dir: Path, chunk_ring_seconds=3):
        super().__init__(split_audio=True)
        self._output_dir = Path(output_dir)
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._current_block = None
        self._last_block = None
        self._buffer_lock = threading.Lock()
        self._last_speech_start_event = None # only set when no current block 
        self._chunk_ring = AudioRingBuffer(max_seconds=chunk_ring_seconds)
        self._full_text = ""
        self._rescan_block = None

    async def on_pipeline_ready(self, pipeline):
        pass
    
    async def on_pipeline_shutdown(self):
        if self._current_block:
            try:
                await self._save_block()
                await self._close_block()
            except:
                logger.error(traceback.format_exc())

    def set_rescan_block(self, block):
        self._rescan_block = block
        directory = Path(self._rescan_block.top_dir)
        if not directory.exists():
            raise Exception(f"cannot rescan non_existant {directory}")
        
    def make_new_block(self, event):
        if not self._rescan_block:
            timestamp = datetime.now()
            timestr = timestamp.strftime("%Y%m%d_%H%M%S")
            directory = self._output_dir / f"block-{timestr}"
            directory.mkdir()
            sound_path = directory / "block.wav"
            text_path = directory / "first_draft.txt"
            full_events_path = directory / "full_events.json"
            meta_events_path = directory / "meta_events.json"
            self._current_block = TextBlock(directory,
                                            sound_path,
                                            text_path,
                                            full_events_path,
                                            meta_events_path,
                                            None,
                                            None,
                                            None,
                                            timestamp)
            logger.info("\nOpened new text block save directory %s\n", str(directory))
        else:
            timestamp = datetime.now()
            directory = Path(self._rescan_block.top_dir)
            sound_path = None
            text_path = self._rescan_block.rescan_text_path
            full_events_path = None
            meta_events_path = self._rescan_block.rescan_meta_events_path
            self._current_block = TextBlock(directory,
                                            sound_path,
                                            text_path,
                                            full_events_path,
                                            meta_events_path,
                                            None,
                                            None,
                                            None,
                                            timestamp)
            logger.info("\nOpened rescan text block for directory %s\n", str(directory))
        logger.info(f'New block {self._current_block}')
    def get_last_block(self):
        return self._last_block
    
    async def on_command_event(self, event:ScribeCommandEvent):
        command = event.command
        if isinstance(command, StartBlockCommand) or isinstance(command, StopBlockCommand):
            self._last_block = self._current_block
            await self._save_block()
            await self._close_block()
            self._full_text = ""
        if isinstance(command, StartBlockCommand):
            self.make_new_block(event)
            if self._last_speech_start_event:
                self._current_block.events.append(self._last_speech_start_event)
                self._current_block.meta_events.append(self._last_speech_start_event)
                if self._chunk_ring.has_data():
                    for event in self._chunk_ring.get_from(self._last_speech_start_event.timestamp):
                        await self.on_audio_chunk_event(event)
                    self._chunk_ring.clear()
            self._current_block.events.append(event)
            self._current_block.meta_events.append(event)
            self._last_speech_start_event = None
                                            
    async def on_text_event(self, event: TextEvent):
        if self._current_block:
            self._current_block.meta_events.append(event)
            for seg in event.segments:
                self._full_text += seg.text + " "

    async def on_audio_event(self, event: AudioEvent):
        if not self._current_block:
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
        if self._current_block.wav_file is None and not self._rescan_block:
            # Open WAV with PCM_16 (not PCM_24) to save space
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

        if not self._rescan_block:
            with self._buffer_lock:
                # Write audio data to WAV file
                data_to_write = np.concatenate(event.data)
                logger.debug("Saving  %d samples to wav file", len(data_to_write))
                self._current_block.wav_file.write(data_to_write)

    async def _save_block(self):
        if not self._current_block:
            return
        block = self._current_block
        if self._rescan_block:
            meta_path = self._rescan_block.top_dir / "rescan_meta_events.json"
            text_path = self._rescan_block.top_dir / "rescan_draft.txt"
            with open(meta_path, 'w') as f:
                json.dump(pre_serialize_events(block.meta_events), f, indent=2)
            with open(text_path, 'w') as f:
                f.write(self._full_text)
        else:
            logger.info("Saving %d bytes of text to %s", len(self._full_text),
                        block.text_path)
            with open(block.text_path, 'w') as f:
                f.write(self._full_text)
            logger.info("Saving  %d events json to %s", len(block.meta_events),
                        block.meta_events_path)
            with open(block.meta_events_path, 'w') as f:
                json.dump(pre_serialize_events(block.meta_events), f, indent=2)
            logger.info("Saving  %d events json to %s", len(block.events),
                        block.full_events_path)
            with open(block.full_events_path, 'w') as f:
                json.dump(pre_serialize_events(block.events), f, indent=2)
        logger.info("Saved events to files in %s", str(self._current_block.directory))

    async def _close_block(self):
        if not self._current_block:
            return
        logger.info("Closing block")
        if self._current_block.wav_file is not None:
            trailing_seconds = 0.4
            trailing_frames = int(self._current_block.samplerate * trailing_seconds)
            silence_block = np.zeros((trailing_frames, self._current_block.channels), dtype=np.float32)
            self._current_block.wav_file.write(silence_block)
            self._current_block.wav_file.close()
            self._current_block.wav_file = None
            
        self._full_text = ""
        self._current_block = None
        
    async def stop(self):
        await self._save_block()
        await self._close_block()
        logger.info("BlockAudioRecorder stopped")

    def list_blocks(self):
        return list(self._output_dir.glob("block-*"))

    def get_block_catalog(self, dir_override=None):
        if dir_override:
            top_dir = Path(dir_override)
            if not top_dir.exists():
                raise Exception(f"{top_dir} does not exist")
        else:
            top_dir = self._output_dir
        
        block_list = self.list_blocks() 
        if block_list is None:
            return None
        block_list = sorted(block_list)
        res = []

            
        for block in block_list:
            res.append(self._get_block_files(block))
        return res

    def _get_block_files(self, block):
        bdir = block.resolve()
        args = [bdir]
        for part in ['block.wav', 'meta_events.json', 'full_events.json', 'first_draft.txt', 'rescan_directory']:
            item = bdir / part
            if "rescan" in part:
                rescan_dir = item
            if item.exists():
                args.append(item)
            else:
                args.append(None)
            
        if rescan_dir.exists():
            rescan_text = rescan_dir / 'rescan_draft.txt'
            if rescan_text.exists():
                args.append(rescan_text)
            else:
                args.append(None)
            rescan_meta = rescan_dir / 'rescan_meta_events.json'
            if rescan_meta.exists():
                args.append(rescan_meta)
            else:
                args.append(None)
        else:
            args.append(None)
            args.append(None)
        return BlockFileset(*args)
        
    def get_last_block_files(self):
        block = self.get_last_block_name()
        if block is None:
            return None
        return self._get_block_files(block)
        
    def get_last_block_name(self):
        block_list = self.list_blocks() 
        if block_list is None:
            return None
        block_list = sorted(block_list)
        block_dir = block_list[-1]
        return block_dir

    def get_last_block_wav_path(self):
        block_list = self.list_blocks() 
        if block_list is None:
            return None
        block_list = sorted(block_list)
        block_dir = block_list[-1]
        return block_dir / 'block.wav'
    
