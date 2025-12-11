#!/usr/bin/env python3
"""
WAV file recorder with event logging for the Scribe transcription system.

Records full-rate audio to WAV files and logs events (AudioSpeechStart/Stop,
TextEvent) to JSON files.
"""
import asyncio
import logging
import json
import threading
from pathlib import Path
from typing import Optional
from datetime import datetime

import numpy as np
import soundfile as sf

from palaver.scribe.audio_events import (
    AudioEvent, AudioChunkEvent, AudioStartEvent, AudioStopEvent,
    AudioSpeechStartEvent, AudioSpeechStopEvent, AudioErrorEvent,
    AudioEventListener
)
from palaver.scribe.text_events import TextEvent, TextEventListener

logger = logging.getLogger("WavSaveRecorder")


class WavSaveRecorder(AudioEventListener):
    """
    Records full-rate audio to WAV + logs events to JSON.

    Creates:
    - segment_YYYYMMDD_HHMMSS.wav (PCM_16 format)
    - segment_YYYYMMDD_HHMMSS.events.json

    Usage:
        recorder = WavSaveRecorder(output_dir=Path("./recordings"))
        await recorder.start()
        listener.add_event_listener(recorder)
        # ... later ...
        await recorder.stop()
    """

    def __init__(self, output_dir: Path):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._wav_file: Optional[sf.SoundFile] = None
        self._wav_path: Optional[Path] = None
        self._events_path: Optional[Path] = None
        self._events_log: list = []
        self._recording = False
        self._buffer_lock = threading.Lock()

    async def start(self):
        """Start the recorder."""
        self._recording = True
        logger.info("WavSaveRecorder started")

    async def on_audio_event(self, event: AudioEvent):
        """Handle audio events - record chunks and log events."""
        if not self._recording:
            return

        if isinstance(event, AudioStartEvent):
            # Create files with timestamp
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            self._wav_path = self.output_dir / f"segment_{timestamp}.wav"
            self._events_path = self.output_dir / f"segment_{timestamp}.events.json"

            # Open WAV with PCM_16 (not PCM_24) to save space
            self._wav_file = sf.SoundFile(
                self._wav_path, mode='w',
                samplerate=event.sample_rate,
                channels=event.channels,
                subtype='PCM_16'
            )
            await self._log_event(event)
            logger.info(f"Recording to {self._wav_path}")

        elif isinstance(event, AudioChunkEvent):
            if self._wav_file:
                with self._buffer_lock:
                    # Write audio data to WAV file
                    data_to_write = np.concatenate(event.data)
                    self._wav_file.write(data_to_write)

        elif isinstance(event, AudioStopEvent):
            await self._log_event(event)
            await self._close_files()
            logger.info("Recording stopped")

        elif isinstance(event, (AudioSpeechStartEvent, AudioSpeechStopEvent)):
            await self._log_event(event)

        elif isinstance(event, AudioErrorEvent):
            await self._log_event(event)
            await self._close_files()
            logger.error(f"Recording error: {event.message}")

    async def _log_event(self, event: AudioEvent):
        """Extract relevant fields from event and add to log."""
        event_dict = {
            'event_type': event.event_type.value,
            'timestamp': event.timestamp,
            'event_id': event.event_id,
        }

        # Add type-specific fields
        if isinstance(event, AudioStartEvent):
            event_dict.update({
                'sample_rate': event.sample_rate,
                'channels': event.channels,
            })
        elif isinstance(event, AudioErrorEvent):
            event_dict['message'] = event.message

        self._events_log.append(event_dict)

    async def _close_files(self):
        """Close WAV file and write events log to JSON."""
        if self._wav_file:
            with self._buffer_lock:
                self._wav_file.close()
                self._wav_file = None
            logger.info(f"Closed WAV file: {self._wav_path}")

        if self._events_path and self._events_log:
            with open(self._events_path, 'w') as f:
                json.dump(self._events_log, f, indent=2)
            logger.info(f"Saved events log: {self._events_path}")

        self._events_log = []
        self._wav_path = None
        self._events_path = None

    async def stop(self):
        """Stop recording and close files."""
        self._recording = False
        await self._close_files()
        logger.info("WavSaveRecorder stopped")


class TextEventLogger(TextEventListener):
    """
    Logs TextEvents to recorder's event log.

    Usage:
        recorder = WavSaveRecorder(output_dir)
        text_logger = TextEventLogger(recorder)
        # Add text_logger to whisper thread's text listeners
    """

    def __init__(self, recorder: WavSaveRecorder):
        self.recorder = recorder

    async def on_text_event(self, event: TextEvent):
        """Log TextEvent to the recorder's event log."""
        event_dict = {
            'event_type': 'TEXT_EVENT',
            'timestamp': event.timestamp,
            'event_id': event.event_id,
            'segments': [
                {
                    'start_ms': seg.start_ms,
                    'end_ms': seg.end_ms,
                    'text': seg.text
                }
                for seg in event.segments
            ],
        }

        if event.audio_source_id:
            event_dict['audio_source_id'] = event.audio_source_id
        if event.audio_start_time:
            event_dict['audio_start_time'] = event.audio_start_time
        if event.audio_end_time:
            event_dict['audio_end_time'] = event.audio_end_time

        self.recorder._events_log.append(event_dict)
