from dataclasses import dataclass
from palaver.scribe.text_events import TextEvent

@dataclass
class CommandMatch:
    command_str: str
    pattern: str
    text_event: TextEvent
    segment_number: int


default_command_map = {}
default_command_map['new command'] = "new command"
default_command_map['attention command'] = "new command"
default_command_map['run command'] = "new command"
default_command_map['mark command'] = "new command"
default_command_map['start a new note'] = "start_note"
default_command_map['begin note'] = "start_note"
default_command_map['break break break'] = "end_note"
default_command_map['stop stop stop'] = "end sequence"
default_command_map['Create a new topic'] = "new_topic"
default_command_map['Add to topic'] = "append_topic"

        
    
