import asyncio
from pathlib import Path
import os
import logging
from typing import Optional
import numpy as np
import soundfile as sf
import sounddevice as sd
from piper import PiperVoice

from palaver.scribe.audio_listeners import AudioListener

logger = logging.getLogger("SoundStreamer")

class SpeakerStreamer:
    """ Streams sound from file or audio samples and disables
    audio_listener while this is happening so that the sound
    does not enter the listener stream."""

    def __init__(self, audio_listener: AudioListener, tts_model_path:Optional[os.PathLike] = None):
        self.audio_listener = audio_listener
        self.tts_model_path = tts_model_path

    async def stream_file(self, file_path:os.PathLike):
        sound_file = sf.SoundFile(file_path)
        sr = sound_file.samplerate
        channels = sound_file.channels
        chunk_duration  = 0.03
        frames_per_chunk = max(1, int(round(chunk_duration * sr)))
        def player():
            with sd.OutputStream(
            samplerate=sr,
            channels=channels,
            blocksize=frames_per_chunk,
            dtype="float32",
            ) as out_stream:
                out_stream.start()
                while True:
                    data = sound_file.read(frames=frames_per_chunk, dtype="float32", always_2d=True)
                    if data.shape[0] == 0:
                        break
                    out_stream.write(data)
                out_stream.close()
                sound_file.close()
                # This blocks
                out_stream.stop()

        await self.audio_listener.pause_streaming()
        await asyncio.sleep(0.01) # give background task a chance to notice
        try:
            await asyncio.to_thread(player)
        finally:
            await self.audio_listener.resume_streaming()

    async def stream_tts(self, text):
        def output_tts():
            voice = PiperVoice.load(self.tts_model_path)
            sample_rate = voice.config.sample_rate 
            channels = 1 # Piper outputs mono audio
            with sd.OutputStream(samplerate=sample_rate, channels=channels, dtype='int16') as stream:
                for chunk in voice.synthesize(text):
                    int_data = np.frombuffer(chunk.audio_int16_bytes, dtype=np.int16)
                    stream.write(int_data)
                stream.stop()

        await self.audio_listener.pause_streaming()
        await asyncio.sleep(0.01) # give background task a chance to notice
        try:
            await asyncio.to_thread(output_tts)
        finally:
            await asyncio.sleep(0.75) # give audio hardware time to catch up
            await self.audio_listener.resume_streaming()


