from typing import Optional, Protocol, Callable
from dataclasses import dataclass, field
import uuid
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
                                           )

from palaver.scribe.api import StartBlockCommand, StopBlockCommand

logger = logging.getLogger("Commands")

alert_up_phrases = []
for name in  ['rupert', 'rubik', 'rufus', 'freddy']:
    for signal in ["listen up", "wake up", "gear up", "stand up"]:
        alert_up_phrases.append(f"{name} {signal} {name}")
        alert_up_phrases.append(f"{name} {signal} ")
        alert_up_phrases.append(f"{signal} {name}")

alert_down_phrases = []
for name in  ['rupert', 'rubik', 'rufus', 'freddy']:
    for signal in ["vacation now", "shutdown", "hang up", ]:
        alert_down_phrases.append(f"{name} {signal} {name}")
        alert_down_phrases.append(f"{name} {signal} ")
        alert_down_phrases.append(f"{signal} {name}")
start_block_command = StartBlockCommand()
stop_block_command = StopBlockCommand()

control_commands = [
    (['start a new note', 'start new note',
      'start a note',  'take this down', 'new text block',
      'command is new block'],
     start_block_command),
    (['break break break', 
      'great great great', 'quick quick quick', 'click click click',
     'session end', 'end session',
     'Rupert back to sleep',
     'Rupert vacation now',
     'Rupert signoff',
      ],
     stop_block_command),
    ]

@dataclass
class BlockTracker:
    """Tracks a text block from start to end."""
    start_event: StartBlockCommand
    text_events: dict[uuid.UUID, TextEvent] = field(default_factory=dict)
    end_event: Optional[StopBlockCommand] = None
    finalized: Optional[bool] = False
    buff: Optional[str]  = ""
    buff_pos: int  = 0


class CommandDispatch(TextEventListener):

    def __init__(self, command_score = 75.0, attention_score=70.0, require_alerts=True) -> None:
        self.emitter = AsyncIOEventEmitter()
        self._command_score = command_score
        self._attention_score = attention_score
        self._require_alerts = require_alerts
        self.command_defs = {}
        self._alert = False
        self._alert_text_event = None
        self._in_block = None
        self._free_text = ""
        for patterns, command in control_commands:
            self.register_command(command, patterns)

    def register_command(self, command: ScribeCommand, patterns):
        self.command_defs[command.name] = ScribeCommandDef(command.name, command, patterns)
        
    def add_event_listener(self, e_listener: CommandEventListener) -> None:
        self.emitter.on(ScribeCommandEvent, e_listener.on_command_event)
        
    async def on_text_event(self, event):
        attention_string = None
        # first see if attention signal present or active
        if not self._alert:
            search_buffer = event.text
            logger.debug('Attention check started, search buffer is %s', search_buffer)
            for pattern in alert_up_phrases:
                alignment = fuzz.partial_ratio_alignment(pattern,  search_buffer)
                if alignment.score >= self._attention_score * 0.9:
                    target_string = search_buffer[alignment.dest_start:alignment.dest_end]
                    score = fuzz.ratio(pattern, target_string)
                    if score  >= self._attention_score:
                        if alignment.dest_start > 0:
                            head = search_buffer[:alignment.dest_start]
                        else:
                            head = ""
                        tail = search_buffer[alignment.dest_end:]
                        attention_string = target_string
                        logger.info('Attention "%s" detected as "%s" head="%s" tail="%s"',
                                    pattern, target_string, head, tail)
                        self._alert = True
                        self._alert_text_event = event
                        break
                elif alignment.score >= self._attention_score * 0.70:
                    logger.info("Close score %f for '%s' in '%s'", alignment.score, pattern, search_buffer)
        if not self._alert and self._require_alerts:
            self._free_text += event.text
            return
        issued = set()
        search_buffer = event.text
        any_match = 0
        for cmd_dev in self.command_defs.values():
            if self._in_block is not None and cmd_dev.command == start_block_command:
                continue
            logger.debug('Command checking "%s" against "%s"', search_buffer, cmd_dev.patterns)
            if cmd_dev.name in issued:
                logger.debug('Command  "%s" already issued', cmd_dev.command.name)
                continue
            for pattern in cmd_dev.patterns:
                alignment = fuzz.partial_ratio_alignment(pattern,  search_buffer)
                if alignment.score >= self._command_score * 0.9:
                    target_string = search_buffer[alignment.dest_start:alignment.dest_end]
                    score = fuzz.ratio(pattern, target_string)
                    if score  >= self._command_score:
                        if alignment.dest_start > 0:
                            head = search_buffer[:alignment.dest_start]
                        else:
                            head = ""
                        tail = search_buffer[alignment.dest_end:]
                        cmd_event = ScribeCommandEvent(cmd_dev.command, pattern, event,
                                                       alignment.dest_start, target_string, self._alert_text_event)
                        logger.info('Command "%s" issuing event on match "%s" to "%s"',
                                    cmd_dev.command.name, pattern, target_string)
                        logger.debug('Command "%s" issuing event %s', cmd_dev.command.name, pformat(cmd_event))
                        await self.emitter.emit(ScribeCommandEvent, cmd_event)
                        issued.add(cmd_dev.name)
                        any_match + 1
                        if cmd_dev.command == start_block_command:
                            self._in_block = BlockTracker(start_event=cmd_event,
                                                          text_events=[event,],
                                                          buff=search_buffer,
                                                          buff_pos=alignment.dest_end)
                            logger.debug('New block tracker %s', self._in_block)
                            self._free_text = ""
                        elif cmd_dev.command == stop_block_command:
                            self._alert = False
                            self._in_block = None
                        break
                elif alignment.score >= self._command_score * 0.70:
                    logger.info("Close score %f for '%s' in '%s'", alignment.score, pattern, search_buffer)
            logger.info('Command checking "%s" got %d matches', search_buffer, any_match)
        if not self._in_block:
            self._free_text += event.text

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
        
