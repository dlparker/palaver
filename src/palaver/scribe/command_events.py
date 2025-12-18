from typing import Protocol, Optional, Any
from dataclasses import dataclass, field
from palaver.scribe.text_events import TextEvent
from enum import Enum
import time
import uuid

"""
Want a nested command logic, so probably a tree structure. Want some commands to have a block of text attached that
followed the command voicing, some should be standalone. When command end events get issued they should encapsulate
all the text blocks (if any) between the command start and end (if any). The start event should refer to a structure
that can have children, and the end should refer back to the start. Probably just a single structure and different
event types that refer to it, start event, end event, solo event .


"""

@dataclass(kw_only=True)
class ScribeCommand:
    name: str
    starts_text_block: bool = False
    ends_text_block: bool = False

@dataclass
class ScribeCommandEvent:
    command: ScribeCommand
    pattern: str
    text_event: TextEvent
    segment_index: int
    matched_text: str
    attention_text_event: Optional[TextEvent] = None
    timestamp: float = field(default_factory=time.time)
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))

@dataclass
class ScribeCommandDef:
    name: str
    command: ScribeCommand
    patterns: list[str]

    
class CommandEventListener(Protocol):

    async def on_command_event(self, command_event: ScribeCommandEvent) -> None: ...
   
