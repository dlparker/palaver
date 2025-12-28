from typing import Any, Optional, ClassVar, Protocol
from enum import Enum
import time
import uuid
from dataclasses import dataclass, field


@dataclass()
class TextEvent:
    text: str = ""
    timestamp: float = field(default_factory=time.time)
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    audio_source_id: str = None
    audio_start_time: float = None
    audio_end_time: float = None
    author_uri: Optional[str] = None  # Source server/service URI (Story 007)
    
class TextEventListener(Protocol):

    async def on_text_event(self, TextEvent) -> None: ...
