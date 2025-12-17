from typing import Protocol, Optional, Any
from dataclasses import dataclass, field
from palaver.scribe.text_events import TextEvent
from enum import Enum
import time
import uuid

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
    attention_text: Optional[str] = None
    timestamp: float = field(default_factory=time.time)
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))

@dataclass
class ScribeCommandDef:
    name: str
    command: ScribeCommand
    patterns: list[str]

class ScribeCommandMode(str, Enum):
    awaiting_start = "AWAITING_START"
    in_block = "IN_BLOCK"

    
@dataclass
class ScribeCommandState:
    mode: ScribeCommandMode
    block_stack: Optional[list[Any]] = field(default_factory=list)
    command_stack: Optional[list[ScribeCommandEvent]] = field(default_factory=list)
    
    
class CommandEventListener(Protocol):

    async def on_command_event(self, command_event: ScribeCommandEvent) -> None: ...
   
