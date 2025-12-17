from typing import Optional, Protocol, Callable
from dataclasses import dataclass
import traceback
import logging
import asyncio
from eventemitter import AsyncIOEventEmitter
from rapidfuzz import fuzz, process

from palaver.scribe.audio_events import AudioSpeechStartEvent, AudioSpeechStopEvent, AudioEventListener
from palaver.scribe.text_events import TextEvent, TextEventListener
from palaver.scribe.command_events import (ScribeCommand,
                                           ScribeCommandEvent,
                                           ScribeCommandDef,
                                           CommandEventListener)

logger = logging.getLogger("Commands")


class CommandDispatch(TextEventListener):

    def __init__(self, minimum_match = 50.0) -> None:
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
            any_match = 0
            for cmd_dev in self.command_defs.values():
                logger.debug('s*** Command checking "%s" against %s', search_buffer, cmd_dev.patterns)
                for pattern in cmd_dev.patterns:
                    ratio = fuzz.ratio(pattern,  search_buffer)
                    logger.debug('s*** Command checking "%s" against "%s" got %f', search_buffer, pattern, ratio)
                    if ratio >= self._minimum_match:
                        if cmd_dev.name in issued:
                            logger.debug('s*** Command  "%s" already issued', cmd_dev.command.name)
                            continue
                        cmd_event = ScribeCommandEvent(cmd_dev.command, pattern, event, segment_index)
                        logger.info('s*** Command  "%s" issuing event %s', cmd_dev.command.name, cmd_event)
                        await self.emitter.emit(ScribeCommandEvent, cmd_event)
                        issued.add(cmd_dev.name)
                        any_match + 1
            logger.info('s*** Command checking "%s" got %d matches', seg, any_match)

    async def issue_block_end(self, start_event):
        cmd_event = None
        for cmd_dev in self.command_defs.values():
            if cmd_dev.command.name == "stop_block":
                pattern = "No pattern match, ended on end of input stream"
                segment_index = 0
                cmd_event = ScribeCommandEvent(cmd_dev.command, pattern, start_event.text_event, segment_index)
                break
        if not cmd_event:
            raise Exception("no stop block command found")
        logger.info('s*** Command  "%s" issuing forced block event %s', cmd_dev.command.name, cmd_event)
        await self.emitter.emit(ScribeCommandEvent, cmd_event)
        
