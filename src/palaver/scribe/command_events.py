from typing import Protocol
from dataclasses import dataclass
from palaver.scribe.text_events import TextEvent

@dataclass
class ScribeCommand:
    name: str
    starts_text_block: bool = False
    ends_text_block: bool = False
    starts_recording_session: bool = False
    ends_recording_session: bool = False
    stops_audio: bool = False
    starts_audio: bool = False
    stops_audio: bool = False

@dataclass
class ScribeCommandEvent:
    command: ScribeCommand
    pattern: str
    text_event: TextEvent
    segment_number: int

@dataclass
class ScribeCommandDef:
    name: str
    command: ScribeCommand
    patterns: list[str]
    
class CommandEventListener(Protocol):

    async def on_command_event(self, command_event: ScribeCommandEvent) -> None: ...
   
