from dataclasses import dataclass
from palaver.scribe.audio_events import AudioEvent, AudioEventListener, AudioChunkEvent
from palaver.scribe.text_events import TextEvent, TextEventListener
from palaver.scribe.command_events import ScribeCommandEvent, CommandEventListener, ScribeCommand


class ScribeAPIListener(AudioEventListener,
                        TextEventListener,
                        CommandEventListener):

    def __init__(self, split_audio=False, split_vad_audio=False):
        self.split_audio = split_audio
        self.split_vad_audio = split_vad_audio

    async def on_pipeline_ready(self, pipeline):
        pass
    
    async def on_pipeline_shutdown(self):
        pass
    
    async def on_command_event(self, event:ScribeCommandEvent):
        pass

    async def on_text_event(self, event: TextEvent):
        pass

    async def on_audio_event(self, event):
        if self.split_audio:
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

@dataclass(kw_only=True)
class StartBlockCommand(ScribeCommand):
    name: str = "start_block"
    starts_text_block: bool = True

@dataclass(kw_only=True)
class StopBlockCommand(ScribeCommand):
    name: str = "stop_block"
    stops_text_block: bool = True


attention_phrases = ['rupert listen', 'rupert command', "rupert c'mon", 'freddy listen']
start_block_command = StartBlockCommand()
stop_block_command = StopBlockCommand()

# patterns should have longest pattern first, then descending for similar patterns
default_commands = [
    (['start a new note', 'start new note', 'start a note',  'start a new block', 'begin note'],
     start_block_command),
    (['break break break', 'end block', 'session end', 'end session'], stop_block_command),
    ]
    
