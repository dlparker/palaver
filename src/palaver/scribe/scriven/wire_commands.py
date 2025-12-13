from typing import Optional, Protocol, Callable
from dataclasses import dataclass
import traceback
import logging
from eventemitter import AsyncIOEventEmitter
from rapidfuzz import fuzz, process

from palaver.scribe.text_events import TextEvent, TextEventListener
from palaver.scribe.command_events import (ScribeCommand,
                                           ScribeCommandEvent,
                                           ScribeCommandDef,
                                           CommandEventListener)

logger = logging.getLogger("Commands")


class CommandDispatch(TextEventListener):

    def __init__(self, minimum_match = 75.0) -> None:
        self.emitter = AsyncIOEventEmitter()
        self._minimum_match = minimum_match
        self.command_defs = {}

    def register_command(self, command: ScribeCommand, patterns):
        self.command_defs[command.name] = ScribeCommandDef(command.name, command, patterns)
        
    def add_event_listener(self, e_listener: CommandEventListener) -> None:
        self.emitter.on(ScribeCommandEvent, e_listener.on_command_event)
        
    async def on_text_event(self, event):
        issued = set()
        for segment_index, seg in enumerate(event.segments):
            search_buffer = seg.text
            for cmd_dev in self.command_defs.values():
                for pattern in cmd_dev.patterns:
                    ratio = fuzz.partial_ratio(pattern,  search_buffer)
                    if ratio >= self._minimum_match:
                        if cmd_dev.name in issued:
                            continue
                        cmd_event = ScribeCommandEvent(cmd_dev.command, pattern, event, segment_index)
                        await self.emitter.emit(ScribeCommandEvent, cmd_event)
                        issued.add(cmd_dev.name)

