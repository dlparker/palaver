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
)
from palaver.scribe.text_events import TextEvent, TextEventListener
from palaver.scribe.api import ScribeAPIListener

logger = logging.getLogger("WavAudioRecorder")

class WavAudioRecorder(ScribeAPIListener):

    def __init__(self, wav_path: Path, chunk_ring_seconds=3):
        super().__init__(split_audio=True)
        self._wav_path = Path(wav_path)
        self._wav_file = None

    async def on_audio_event(self, event: AudioEvent):
        if isinstance(event, AudioChunkEvent):
            if self._wav_file is None: 
                self._wav_file = sf.SoundFile(
                    self._wav_path,
                    mode='w',
                    samplerate=int(event.sample_rate),
                    channels=event.channels[1],
                    subtype='PCM_32'
                )
            # Write audio data to WAV file
            data_to_write = np.concatenate(event.data)
            logger.debug("Saving  %d samples to wav file", len(data_to_write))
            self._wav_file.write(data_to_write)
        if isinstance(event, AudioStopEvent) and self._wav_file:
            self._wave_file.close()
            self._wave_file = None
    
