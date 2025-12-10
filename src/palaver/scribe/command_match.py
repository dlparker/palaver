from dataclasses import dataclass
from palaver.scribe.text_events import TextEvent

@dataclass
class CommandMatch:
    command_str: str
    pattern: str
    text_event: TextEvent
    segment_number: int


default_command_map = {}
default_command_map['start a new note'] = "start_note"
default_command_map['begin note'] = "start_note"
default_command_map['break break break'] = "end_note"
        
    
