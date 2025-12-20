#!/usr/bin/env python
import asyncio
from pathlib import Path
import soundfile as sf
import sounddevice as sd


async def main():

    file_path = Path(__file__).parent.parent / "signal_sounds" / "klingon_computer_beep_3.mp3"
    sound_file = sf.SoundFile(file_path)
    sr = sound_file.samplerate
    channels = sound_file.channels
    chunk_duration  = 0.03
    frames_per_chunk = max(1, int(round(chunk_duration * sr)))
    out_stream = sd.OutputStream(
        samplerate=sr,
        channels=channels,
        blocksize=frames_per_chunk,
        dtype="float32",
    )
    out_stream.start()

    while True:
        data = sound_file.read(frames=frames_per_chunk, dtype="float32", always_2d=True)
        if data.shape[0] == 0:
            break
        out_stream.write(data)
    out_stream.close()
    sound_file.close()

if __name__=="__main__":
    asyncio.run(main())


