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


class RevisionSource(Enum):
    """Source/method used to create a draft revision

    Priority for conflict resolution: HUMAN > LLM > WHISPER_REPROCESS > UNKNOWN
    """
    UNKNOWN = "unknown"
    WHISPER_REPROCESS = "whisper_reprocess"  # Re-transcribed with better model
    LLM = "llm"  # LLM suggested corrections
    HUMAN = "human"  # Human approved/edited


@dataclass
class Suggestion:
    """A single suggested change to a draft from LLM analysis"""
    old_text: str
    new_text: str  # empty string for removals
    confidence: float
    reason: str


@dataclass(kw_only=True)
class DraftChangeEvent(DraftEvent):
    """LLM has analyzed a draft and provided editing suggestions

    The draft field contains the original draft that was analyzed.
    """
    suggestions: list[Suggestion]
    llm_model: str  # e.g., "llama3.1:8b-instruct-q4_K_M"
    llm_response_raw: str  # full JSON response for debugging/audit


@dataclass(kw_only=True)
class DraftRevisionEvent(DraftEvent):
    """A revised version of a draft has been created

    The draft field contains the NEW revised draft (result of applying changes).

    For conflict resolution: HUMAN > LLM > WHISPER_REPROCESS > UNKNOWN
    """
    original_draft_id: str  # UUID reference to original draft (for distributed systems)
    source_change_event_id: str  # UUID of the DraftChangeEvent that was approved
    approved_suggestions: list[int]  # indices into suggestions that were applied
    revised_text: str  # The full_text after applying changes (redundant with draft.full_text, but convenient)
    revision_source: RevisionSource  # How this revision was created (for prioritization)


class DraftEventListener(Protocol):

    async def on_draft_event(self, command_event: DraftEvent) -> None: ...
   
