from typing import Any
import numpy as np

from palaver_shared.audio_events import (
    AudioEventType,
    AudioEvent,
    AudioStartEvent,
    AudioStopEvent,
    AudioChunkEvent,
    AudioSpeechStartEvent,
    AudioSpeechStopEvent,
    AudioErrorEvent,
)
    
from palaver_shared.text_events import TextEvent
from palaver_shared.draft_events import DraftEvent, DraftStartEvent, DraftEndEvent, Draft

event_type_groups = {
    'audio': [
        AudioStartEvent,
        AudioStopEvent,
        AudioChunkEvent,
        AudioSpeechStartEvent,
        AudioSpeechStopEvent,
        AudioErrorEvent,
        ],
    'text': [TextEvent,],
    'draft': [
        DraftStartEvent,
        DraftEndEvent,
    ]
}

event_type_map = {}
for etype in [AudioStartEvent,
              AudioStopEvent,
              AudioChunkEvent,
              AudioSpeechStartEvent,
              AudioSpeechStopEvent,
              AudioErrorEvent,
              TextEvent,
              DraftStartEvent,
              DraftEndEvent,
              ]:
    event_type_map[str(etype)] = etype
    

    
def event_from_dict(event_dict: dict) -> [AudioEvent | TextEvent | DraftEvent]:
    event_class = event_type_map[event_dict['event_class']]
    kwargs = dict(event_dict) # shallow
    del kwargs['event_class']
    if event_class in event_type_groups['audio']:
        aet = AudioEventType(kwargs['event_type'])
        del kwargs['event_type']
        if "data" in kwargs:
            kwargs["data"] = np.array(kwargs["data"], dtype=np.float32)  
        return event_class(**kwargs)
    if event_class in event_type_groups['text']:
        return event_class(**kwargs)
    if event_class in event_type_groups['draft']:
        draft = draft_from_dict(kwargs['draft'])
        kwargs['draft'] = draft
        return event_class(**kwargs)

def draft_from_dict(in_dict: dict) -> [DraftEvent]:
    kwargs = dict(in_dict)
    return Draft(**kwargs)

def draft_from_draft_record_dict(in_dict: dict) -> [DraftEvent]:
    return Draft(start_text=in_dict['start_text'],
                 end_text=in_dict.get('end_text'),
                 full_text=in_dict['full_text'],
                 draft_id=in_dict['draft_id'],
                 parent_draft_id=in_dict['parent_draft_id'])


def draft_record_to_dict(record) -> dict:
    """Convert DraftRecord SQLModel to JSON-serializable dict.

    Args:
        record: DraftRecord instance (or None)

    Returns:
        Dictionary with all fields, datetime converted to ISO string.
        Returns None if record is None.
    """
    if record is None:
        return None

    return {
        "draft_id": record.draft_id,
        "timestamp": record.timestamp,
        "start_text": record.start_text,
        "end_text": record.end_text,
        "full_text": record.full_text,
        "classname": record.classname,
        "directory_path": record.directory_path,
        "parent_draft_id": record.parent_draft_id,
        "created_at": record.created_at.isoformat() if record.created_at else None,
    }

def serialize_event(event: [AudioEvent | TextEvent | DraftEvent]) -> dict[str, Any]:
    event_class = str(event.__class__)
    event_dict = {"event_class": event_class}

    # Extract dataclass fields
    if hasattr(event, "__dataclass_fields__"):
        for field_name in event.__dataclass_fields__:
            value = getattr(event, field_name)
            event_dict[field_name] = serialize_value(value, field_name)

    return event_dict

def serialize_value(value: Any, field_name: str = None) -> Any:
    if value is None:
        return None

    if isinstance(value, np.ndarray):
        return value.tolist()

    if field_name == "event_type":
        if hasattr(value, 'value'):
            return value.value
        else:
            return str(value)

    # Recursively handle nested dataclasses
    if hasattr(value, "__dataclass_fields__"):
        nested_dict = {}
        for nested_field_name in value.__dataclass_fields__:
            nested_value = getattr(value, nested_field_name)
            nested_dict[nested_field_name] = serialize_value(nested_value, nested_field_name)
        return nested_dict

    if isinstance(value, list):
        return [serialize_value(item) for item in value]

    if isinstance(value, dict):
        return {k: serialize_value(v) for k, v in value.items()}

    return value
        
