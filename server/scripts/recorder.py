#!/usr/bin/env python
import asyncio
from pathlib import Path
import argparse
import numpy as np
import soundfile as sf
import sounddevice as sd


async def run(out_path):

    wave_file = None
    def callback(indata, outdata, frame_count, time_info, status):
        data_to_write = np.concatenate(indata)
        wav_file.write(data_to_write)

    dtype = 'float32'
    blocksize = int(44100 * 0.03)
    stream = sd.Stream(blocksize=blocksize, callback=callback, dtype=dtype,
                             channels=1)
    blocksize = stream.blocksize
    samplerate = stream.samplerate
    channels = stream.channels

    
    wav_file = sf.SoundFile(
        out_path,
        mode='w',
        samplerate=int(samplerate),
        channels=channels[1],
        subtype='PCM_32'
    )
    with stream:
        while True:
            await asyncio.sleep(0.1)
    
async def main():

    parser = argparse.ArgumentParser(
        description="extract text events from block recorder meta_events.json",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        'file_path',
        type=Path,
        nargs='?',
        help=''
    )
    args = parser.parse_args()
    if args.file_path is None:
        parser.error("Must supply a file path")
    
    await run(args.file_path)

if __name__=="__main__":
    asyncio.run(main())


