from palaver.scribe.audio_events import AudioEvent, AudioEventListener, AudioChunkEvent
from palaver.scribe.text_events import TextEvent, TextEventListener
from palaver.scribe.command_events import ScribeCommandEvent, CommandEventListener, ScribeCommand


class ScribeAPIListener(AudioEventListener,
                        TextEventListener,
                        CommandEventListener):

    def __init__(self, split_audio=False, split_vad_audio=False):
        self.split_audio = split_audio
        self.split_vad_audio = split_vad_audio
        
    async def on_command_event(self, event:ScribeCommandEvent):
        pass

    async def on_text_event(self, event: TextEvent):
        pass

    async def on_audio_event(self, event):
        if split_audio:
            if isinstance(event, AudioChunkEvent):
                await self.on_audio_chunk_event(event)
            else:
                await self.on_audio_change_event(event)
        else:
            pass
    
    async def on_audio_change_event(self, event):
        pass

    async def on_audio_chunk_event(self, event):
        pass
    

default_commands = [
    (['start a note', 'begin note', 'start new note'],
        ScribeCommand('start_note', starts_recording_session=True, starts_text_block=True)),
    (['break break break', 'stop stop stop',],
        ScribeCommand('stop_note', ends_recording_session=True, ends_text_block=True)),
    ]
    
