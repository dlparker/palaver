import asyncio
import json
import logging
import time
from datetime import datetime
from pathlib import Path
import numpy as np
from typing import List, Dict, Any

# Usage in main pipeline:
# vadfilter.add_event_listener(SegmentRecorder())
# In mic_to_text.py or similar: await segment_recorder.finalize_all() on shutdown

from palaver.scribe.audio_events import (
    AudioEventListener, AudioSpeechStartEvent, AudioSpeechStopEvent,
    AudioChunkEvent, AudioStartEvent, AudioStopEvent, TextEvent
)
from palaver.scribe.text_events import TextEventListener
from palaver.scribe.scriven.whisper_thread import WhisperThread

logger = logging.getLogger("SegmentRecorder")

class AudioRingBuffer:
    def __init__(self, max_seconds: float = 2.0):
        if max_seconds <= 0:
            raise ValueError("max_seconds must be positive")
        self.max_seconds = max_seconds
        self.buffer: List[AudioChunkEvent] = []  # Simple list for now; use deque for efficiency if needed

    def has_data(self) -> bool:
        return bool(self.buffer)

    def add(self, event: AudioChunkEvent) -> None:
        self.buffer.append(event)
        self._prune()

    def _prune(self, now: float = None) -> None:
        if now is None:
            now = time.time()
        total_duration = 0.0
        for i in range(len(self.buffer) - 1, -1, -1):
            total_duration += self.buffer[i].duration
            if total_duration > self.max_seconds:
                self.buffer = self.buffer[i + 1:]
                break

    def get_all(self, clear: bool = False) -> List[AudioChunkEvent]:
        res = self.buffer[:]
        if clear:
            self.buffer.clear()
        return res

class SegmentRecorder(AudioEventListener, TextEventListener):
    def __init__(self, silence_threshold_sec: float = 12.0, pre_silence_sec: float = 1.5, model_path: str = "models/ggml-base.en.bin"):
        self.silence_start: Optional[float] = None
        self.in_segment: bool = False
        self.pre_buffer = AudioRingBuffer(max_seconds=pre_silence_sec)
        self.audio_buffer: List[np.ndarray] = []  # Temp buffer for active segment
        self.events_log: List[Dict[str, Any]] = []
        self.sample_rate: int = 48000  # Default; set from AudioStartEvent
        self.channels: int = 2  # Framework stereo
        self.dtype: str = 'float32'
        self.silence_threshold_sec = silence_threshold_sec
        self.pre_silence_sec = pre_silence_sec
        self.current_file: Optional['sf.SoundFile'] = None  # Lazy import soundfile
        self.current_path: Optional[Path] = None
        self.meta_path: Optional[Path] = None
        self.medium_whisper: Optional[WhisperThread] = None
        self.model_path = model_path  # Base model for real-time
        self.medium_model_path = "models/ggml-medium.en.bin"  # For deferred

    async def start(self):
        def medium_error_callback(error_dict: Dict):
            logger.error(f"Medium Whisper error: {error_dict}")
        self.medium_whisper = WhisperThread(self.medium_model_path, medium_error_callback)
        await self.medium_whisper.start()

    async def stop(self):
        if self.medium_whisper:
            await self.medium_whisper.stop()
        await self._finalize_segment()

    async def on_audio_event(self, event: AudioEvent):
        if isinstance(event, AudioStartEvent):
            self.sample_rate = event.sample_rate
            self.channels = event.channels
            self.dtype = event.datatype
            self.events_log.append({
                'type': 'audio_start',
                'timestamp': event.timestamp,
                'sample_rate': self.sample_rate,
                'channels': self.channels,
                'dtype': self.dtype
            })
        elif isinstance(event, AudioSpeechStartEvent):
            await self._start_segment(event)
        elif isinstance(event, AudioSpeechStopEvent):
            self.silence_start = asyncio.get_event_loop().time()
        elif isinstance(event, AudioChunkEvent):
            if not self.in_segment and not event.in_speech:
                self.pre_buffer.add(event)
            if self.in_segment or event.in_speech:
                await self._write_chunk(event)
            if self.silence_start and (asyncio.get_event_loop().time() - self.silence_start > self.silence_threshold_sec):
                await self._finalize_segment()
                self.silence_start = None
        elif isinstance(event, AudioStopEvent):
            await self._finalize_segment()

    async def on_text_event(self, event: TextEvent):
        if self.in_segment:
            self.events_log.append({
                'type': 'text',
                'timestamp': event.timestamp,
                'segments': [{'start_ms': s.start_ms, 'end_ms': s.end_ms, 'text': s.text} for s in event.segments],
                'audio_start_time': event.audio_start_time,
                'audio_end_time': event.audio_end_time
            })

    async def _start_segment(self, start_event: AudioSpeechStartEvent):
        if self.in_segment:
            await self._finalize_segment()
        timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.current_path = Path(f"segment_{timestamp_str}.wav")
        self.meta_path = self.current_path.with_suffix(".events.jsonl")

        import soundfile as sf  # Lazy import
        self.current_file = sf.SoundFile(
            self.current_path,
            mode='w',
            samplerate=self.sample_rate,
            channels=1,  # Mono
            subtype='PCM_16'
        )

        # Write pre-buffer (downmix to mono)
        pre_events = self.pre_buffer.get_all(clear=True)
        for pre_event in pre_events:
            data = pre_event.data.astype(np.float32)
            if data.shape[1] == 2:
                data = np.mean(data, axis=1, keepdims=True)
            self.current_file.write(data)

        self.events_log.append({
            'type': 'speech_start',
            'timestamp': start_event.timestamp,
            'silence_period_ms': start_event.silence_period_ms,
            'vad_threshold': start_event.vad_threshold,
            'speech_pad_ms': start_event.speech_pad_ms
        })
        self.in_segment = True
        logger.info(f"Started segment: {self.current_path}")

    async def _write_chunk(self, event: AudioChunkEvent):
        if not self.current_file:
            return
        data = event.data.astype(np.float32)
        if data.shape[1] == 2:
            data = np.mean(data, axis=1, keepdims=True)
        self.current_file.write(data)

    async def _finalize_segment(self):
        if not self.current_file:
            return
        self.current_file.close()
        self.current_file = None
        self.in_segment = False

        # Dump metadata
        with open(self.meta_path, 'w') as f:
            for evt in self.events_log:
                json.dump(evt, f)
                f.write('\n')
        self.events_log.clear()

        logger.info(f"Finalized segment: {self.current_path} + {self.meta_path}")

        # Schedule medium transcription during silence
        asyncio.create_task(self._transcribe_medium(self.current_path, self.meta_path))

    async def _transcribe_medium(self, wav_path: Path, meta_path: Path):
        logger.info(f"Starting medium transcription for {wav_path} during silence...")
        # Load metadata for potential alignment
        with open(meta_path, 'r') as f:
            metadata = [json.loads(line) for line in f]

        # Run medium Whisper on the full WAV
        # Assuming WhisperThread can take a file path or buffer; adapt as needed
        # For simplicity, read WAV and feed as single job
        import soundfile as sf
        data, sr = sf.read(wav_path, dtype='float32')
        # Mock feeding to medium_whisper (extend WhisperThread if needed to support file input)
        # For now, assume you can push a large chunk
        chunk_event = AudioChunkEvent(
            source_id="medium_transcribe",
            data=data,
            duration=len(data) / sr,
            sample_rate=sr,
            channels=1,
            blocksize=len(data),
            datatype='float32',
            timestamp=time.time()
        )
        await self.medium_whisper.on_audio_event(chunk_event)
        await self.medium_whisper.gracefull_shutdown(3.0)  # Wait for processing

        # Collect refined TextEvents from medium_whisper (via emitter or queue)
        # Then save to e.g., wav_path.with_suffix('.refined.txt') or .org
        # Align with metadata timestamps if needed
        logger.info(f"Medium transcription complete for {wav_path}")
