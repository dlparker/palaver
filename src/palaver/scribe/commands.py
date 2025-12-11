
from palaver.scribe.scriven.wire_commands import ScribeCommand

default_commands = [
    (['start a note', 'begin note', 'start new note'],
        ScribeCommand('start_note', starts_recording_session=True, starts_text_block=True)),
    (['break break break', 'stop stop stop',],
        ScribeCommand('stop_note', ends_recording_session=True, ends_text_block=True)),
    ]
