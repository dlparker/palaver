#!/usr/bin/env python
import asyncio
import logging
from pathlib import Path
from palaver.scribe.text_events import TextEvent
from palaver.scribe.command_events import (ScribeCommand,
                                           ScribeCommandEvent,
                                           CommandEventListener)

from palaver.scribe.scriven.wire_commands import CommandDispatch
from loggers import setup_logging
import soundfile as sf
import sounddevice as sd


async def main():

    last_event = None
    class Catcher(CommandEventListener):

        async def on_command_event(self, event: ScribeCommandEvent):
            nonlocal last_event
            last_event = event

    cd = CommandDispatch()
    catcher = Catcher()
    cd.add_event_listener(catcher)
    text = "Rupert Command Start A new note Note stuff"
    tevent1 = TextEvent(text=text)
    await cd.on_text_event(tevent1)
    assert last_event is not None

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
    setup_logging(default_level="WARNING",
                  info_loggers=[],
                  debug_loggers=['Commands',],
                  more_loggers=[])
    asyncio.run(main())


