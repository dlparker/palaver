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
            any_match = 0
            for cmd_dev in self.command_defs.values():
                logger.info('s*** Command checking "%s" against %s', search_buffer, cmd_dev.patterns)
                for pattern in cmd_dev.patterns:
                    ratio = fuzz.partial_ratio(pattern,  search_buffer)
                    logger.info('s*** Command checking "%s" against "%s" got %f', search_buffer, pattern, ratio)
                    if ratio >= self._minimum_match:
                        if cmd_dev.name in issued:
                            logger.info('s*** Command  "%s" already issued', cmd_dev.command.name)
                            continue
                        cmd_event = ScribeCommandEvent(cmd_dev.command, pattern, event, segment_index)
                        logger.info('s*** Command  "%s" issuing event %s', cmd_dev.command.name, cmd_event)
                        await self.emitter.emit(ScribeCommandEvent, cmd_event)
                        issued.add(cmd_dev.name)
                        any_match + 1
            logger.info('s*** Command checking "%s" got %d matches', search_buffer, any_match)
            


class CommandShim(TextEventListener, AudioEventListener):
    # Used for rescan, but keys off of speech start and stop
    # listeners, issues command start and stop so that
    # normal event pipeline behavior is immitated.
    # Future refinement might do command searches to get
    # sub commands inside text blocks
    
    def __init__(self, minimum_match = 75.0) -> None:
        self.emitter = AsyncIOEventEmitter()
        self.texts = []
        self.start_issued = False
        self.command_defs = {}
        self.logger = logging.getLogger('CommandShim')

    def register_command(self, command: ScribeCommand, patterns):
        self.command_defs[command.name] = ScribeCommandDef(command.name, command, patterns)

    def add_event_listener(self, e_listener: CommandEventListener) -> None:
        self.emitter.on(ScribeCommandEvent, e_listener.on_command_event)
        
    async def on_text_event(self, event):
        self.texts.append(event)
        if not self.start_issued:
            # this is a hack, may need to fix it
            from palaver.scribe.api import start_block_command, start_rescan_command
            cmd_event = ScribeCommandEvent(start_rescan_command,'rescan fake', event, 0)
            await self.emitter.emit(ScribeCommandEvent, cmd_event)
            cmd_event = ScribeCommandEvent(start_block_command,'rescan fake', event, 0)
            await self.emitter.emit(ScribeCommandEvent, cmd_event)
            self.start_issued = True

    async def on_audio_event(self, event):
        if isinstance(event, AudioSpeechStopEvent):
            # this is a hack, may need to fix it
            from palaver.scribe.api import stop_block_command
            cmd_event = ScribeCommandEvent(stop_block_command, 'rescan fake', event, 0)
            self.logger.info('s****************ending %s', cmd_event)
            await self.emitter.emit(ScribeCommandEvent, cmd_event)
                    
            
                




        
