from typing import Protocol, Optional, Any
from dataclasses import dataclass, field
from palaver.scribe.text_events import TextEvent
from enum import Enum
import time
import uuid


@dataclass
class TextMark:
    start: int
    end: int
    text: str
    
@dataclass
class Section:
    draft: 'Draft'
    start_text: TextMark
    end_text: Optional[TextMark] = None
    full_text: Optional[str] = field(default_factory=str)
    timestamp: float = field(default_factory=time.time)
    section_id: str = field(default_factory=lambda: str(uuid.uuid4()))

@dataclass
class Draft:
    start_text: TextMark
    end_text: Optional[TextMark] = None
    sections: Optional[list[Section]] = field(default_factory=list[Section])
    full_text: Optional[str] = field(default_factory=str)
    text_buffer: Optional[str] = field(default_factory=str)
    timestamp: float = field(default_factory=time.time)
    draft_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    @property
    def trimmed_text(self):
        start = self.full_text.find(self.start_text.text) + len(self.start_text.text)
        if self.end_text.text == '':
            end = len(self.full_text)
        else:
            end = self.full_text.find(self.end_text.text)
        return self.full_text[start:end]


@dataclass(kw_only=True)
class DraftEvent:
    draft: Draft
    timestamp: float = field(default_factory=time.time)
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))

@dataclass(kw_only=True)
class DraftStartEvent(DraftEvent):
    pass

@dataclass(kw_only=True)
class DraftEndEvent(DraftEvent):
    pass
    
class DraftEventListener(Protocol):

    async def on_draft_event(self, command_event: DraftEvent) -> None: ...
   
