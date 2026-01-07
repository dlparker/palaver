from typing import Protocol, Optional, Any
from dataclasses import dataclass, field
from palaver.scribe.text_events import TextEvent
from enum import Enum
import time
import uuid


@dataclass
class Draft:
    start_text: str
    end_text: Optional[str] = None
    full_text: Optional[str] = field(default_factory=str)
    timestamp: float = field(default_factory=time.time)
    draft_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    parent_draft_id: Optional[str] = None
    audio_start_time: Optional[float] = None
    audio_end_time: Optional[float] = None
    

@dataclass(kw_only=True)
class DraftEvent:
    draft: Draft
    timestamp: float = field(default_factory=time.time)
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    author_uri: Optional[str] = None  # Source server/service URI (Story 007)

@dataclass(kw_only=True)
class DraftStartEvent(DraftEvent):
    pass

@dataclass(kw_only=True)
class DraftEndEvent(DraftEvent):
    pass

@dataclass(kw_only=True)
class DraftRescanEvent(DraftEvent):
    original_draft_id: str
    draft: Draft


class DraftEventListener(Protocol):

    async def on_draft_event(self, command_event: DraftEvent) -> None: ...
   
