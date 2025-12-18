#!/usr/bin/env python
import asyncio
import logging
from palaver.scribe.text_events import TextEvent, VTTSegment
from palaver.scribe.command_events import (ScribeCommand,
                                           ScribeCommandEvent,
                                           CommandEventListener)

from palaver.scribe.scriven.wire_commands import CommandDispatch
from loggers import setup_logging


async def main():

    last_event = None
    class Catcher(CommandEventListener):

        async def on_command_event(self, event: ScribeCommandEvent):
            nonlocal last_event
            last_event = event
            
    cd = CommandDispatch()
    catcher = Catcher()
    cd.add_event_listener(catcher)
    segs = [VTTSegment(0, 200, "Rupert Command"),
            VTTSegment(20, 500, "Start A new note"),
            VTTSegment(20, 500, "Note stuff")]
    tevent1 = TextEvent(segments=segs)
    await cd.on_text_event(tevent1)
    assert last_event is not None


if __name__=="__main__":
    setup_logging(default_level="WARNING",
                  info_loggers=[],
                  debug_loggers=['Commands',],
                  more_loggers=[])
    asyncio.run(main())


