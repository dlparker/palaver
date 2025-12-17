from typing import Optional, Protocol, Callable
from dataclasses import dataclass
import traceback
import logging
import asyncio
from enum import Enum
from pprint import pformat
from eventemitter import AsyncIOEventEmitter
from rapidfuzz import fuzz, process

from palaver.scribe.audio_events import AudioSpeechStartEvent, AudioSpeechStopEvent, AudioEventListener
from palaver.scribe.text_events import TextEvent, TextEventListener
from palaver.scribe.command_events import (ScribeCommand,
                                           ScribeCommandEvent,
                                           ScribeCommandDef,
                                           CommandEventListener,
                                           ScribeCommandMode,
                                           )

from palaver.scribe.api import attention_phrases

logger = logging.getLogger("Commands")




class CommandDispatch(TextEventListener):

    def __init__(self, minimum_match = 75.0, attention_match=70.0) -> None:
        self.emitter = AsyncIOEventEmitter()
        self._minimum_match = minimum_match
        self._attention_match = attention_match
        self.command_defs = {}
        self._alert = False
        self._mode = None
        self._require_alert = False

    def register_command(self, command: ScribeCommand, patterns):
        self.command_defs[command.name] = ScribeCommandDef(command.name, command, patterns)
        
    def add_event_listener(self, e_listener: CommandEventListener) -> None:
        self.emitter.on(ScribeCommandEvent, e_listener.on_command_event)
        
    async def on_text_event(self, event):
        attention_string = None
        # first see if attention signal present or active
        if not self._alert:
            logger.debug('Attention check started')
            for index, seg in enumerate(event.segments):
                search_buffer = seg.text
                for pattern in attention_phrases:
                    alignment = fuzz.partial_ratio_alignment(pattern,  search_buffer)
                    if alignment.score >= self._attention_match:
                        target_string = search_buffer[alignment.dest_start:alignment.dest_end]
                        score = fuzz.ratio(pattern, target_string)
                        if score  >= self._attention_match:
                            if alignment.dest_start > 0:
                                head = search_buffer[:alignment.dest_start]
                            else:
                                head = ""
                            tail = search_buffer[alignment.dest_end:]
                            attention_string = target_string
                            logger.info('Attention "%s" detected as "%s" head="%s" tail="%s"',
                                        pattern, target_string, head, tail)
                            self._alert = True
                            self._mode = ScribeCommandMode.awaiting_start
                            break
                if self._alert:
                    break
        if not self._alert:
            return
        issued = set()
        for index, seg in enumerate(event.segments):
            any_match = 0
            search_buffer = seg.text
            for cmd_dev in self.command_defs.values():
                logger.debug('Command checking "%s" against %s', search_buffer, cmd_dev.patterns)
                if cmd_dev.name in issued:
                    logger.debug('Command  "%s" already issued', cmd_dev.command.name)
                    continue
                for pattern in cmd_dev.patterns:
                    alignment = fuzz.partial_ratio_alignment(pattern,  search_buffer)
                    if alignment.score >= self._minimum_match:
                        target_string = search_buffer[alignment.dest_start:alignment.dest_end]
                        score = fuzz.ratio(pattern, target_string)
                        if score  >= self._minimum_match:
                            if alignment.dest_start > 0:
                                head = search_buffer[:alignment.dest_start]
                            else:
                                head = ""
                            tail = search_buffer[alignment.dest_end:]
                            cmd_event = ScribeCommandEvent(cmd_dev.command, pattern, event,
                                                           index, target_string, attention_string)
                            logger.info('Command "%s" issuing event', cmd_dev.command.name)
                            logger.debug('Command "%s" issuing event %s', cmd_dev.command.name, pformat(cmd_event))
                            await self.emitter.emit(ScribeCommandEvent, cmd_event)
                            issued.add(cmd_dev.name)
                            any_match + 1
                            if cmd_dev.command.name == "stop_block":
                                self._alert = False
                            break
            logger.info('Command checking "%s" got %d matches', seg, any_match)

    async def issue_block_end(self, start_event):
        cmd_event = None
        for cmd_dev in self.command_defs.values():
            if cmd_dev.command.name == "stop_block":
                pattern = "No pattern match, ended on end of input stream"
                cmd_event = ScribeCommandEvent(cmd_dev.command, pattern, start_event.text_event, 0, 'None')
                break
        if not cmd_event:
            raise Exception("no stop block command found")
        logger.info('Command  "%s" issuing forced block event %s', cmd_dev.command.name, cmd_event)
        await self.emitter.emit(ScribeCommandEvent, cmd_event)
        
