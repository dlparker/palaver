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
class Draft:
    start_text: TextMark
    end_text: Optional[TextMark] = None
    full_text: Optional[str] = field(default_factory=str)
    timestamp: float = field(default_factory=time.time)
    draft_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    # TextEvents that contain the start/end boundary phrases
    start_matched_events: Optional[list[TextEvent]] = field(default_factory=list)
    end_matched_events: Optional[list[TextEvent]] = field(default_factory=list)

    @property
    def trimmed_text(self):
        start = self.full_text.find(self.start_text.text) + len(self.start_text.text)
        if self.end_text.text == '':
            end = len(self.full_text)
        else:
            end = self.full_text.find(self.end_text.text)
        return self.full_text[start:end]

    @property
    def audio_start_time(self) -> Optional[float]:
        """Timestamp of first audio sample in draft (seconds since epoch).

        Computed from the earliest audio_start_time in start_matched_events.
        Returns None if no matched events or if events lack timing information.

        This timestamp includes pre-buffered audio (typically 1 second before
        VAD detection), ensuring the complete draft context is available for rescan.
        """
        if not self.start_matched_events:
            return None
        times = [e.audio_start_time for e in self.start_matched_events
                 if e.audio_start_time is not None]
        return min(times) if times else None

    @property
    def audio_end_time(self) -> Optional[float]:
        """Timestamp of last audio sample in draft (seconds since epoch).

        Computed from the latest audio_end_time in end_matched_events.
        Returns None if no matched events or if events lack timing information.

        For drafts without explicit end phrases (force_end or implicit close),
        end_matched_events will be empty and this returns None.
        """
        if not self.end_matched_events:
            return None
        times = [e.audio_end_time for e in self.end_matched_events
                 if e.audio_end_time is not None]
        return max(times) if times else None


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
   
