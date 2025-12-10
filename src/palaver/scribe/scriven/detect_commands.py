from collections.abc import Callable
from rapidfuzz import fuzz, process
from typing import Awaitable

from palaver.scribe.text_events import TextEvent
from palaver.scribe.command_match import CommandMatch, default_command_map

    
class DetectCommands:

    default_config = {'command_dictionary': default_command_map,
                      'match_minimum': 75.0,
                      'on_command': None,
                      'error_callback': None,
                      }
    config_help = {'command_dictionary': "A map of speech patterns to match to command strings",
                   'match_minimum': "Minimum Levenshtein distance that counts as a match",
                   'on_command': "An Awaitable[TextEvent, str] that will receive detected commands in text",
                   'error_callback': "Callable that accepts a dictionary of error info when a background error occurs",
                   }

    def __init__(self, on_command: Awaitable[int],  error_callback: Callable[[dict], None]):
        self._config = dict(self.default_config)
        self._config['error_callback'] = error_callback
        self._config['on_command'] = on_command
        self._command_dictionary = self._config['command_dictionary']

    async def update_config(self, new_config):
        # sanity check threashold
        if new_config['match_mimimum'] < 25 or new_config['match_mimimum'] > 100:
            raise Exception(f"try to make sense regarding match_minimum, not {new_config['match_mimimum']}")
        self._config = new_config
        self._command_dictionary = self._config['command_dictionary']
        
    async def on_text_event(self, event):
        on_command = self._config['on_command']
        issued = set()
        for segment_index, seg in enumerate(event.segments):
            search_buffer = seg.text
            for pattern in self._command_dictionary:
                ratio = fuzz.partial_ratio(pattern,  search_buffer)
                if ratio >= self._config['match_minimum']:
                    command_key = self._command_dictionary[pattern]
                    if command_key in issued:
                        continue
                    res = CommandMatch(command_key, pattern, event, segment_index)
                    await on_command(res)
                    issued.add(command_key)
                
