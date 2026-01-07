from dataclasses import dataclass
from palaver_shared.audio_events import AudioEvent, AudioStartEvent, AudioStopEvent, AudioChunkEvent
from palaver_shared.audio_events import AudioSpeechStartEvent, AudioSpeechStopEvent, AudioEventListener
from palaver_shared.text_events import TextEvent, TextEventListener
from palaver_shared.draft_events import DraftEvent, DraftStartEvent, DraftEndEvent
from palaver_shared.draft_events import DraftEventListener


class PalaverEventListener(AudioEventListener, TextEventListener,  DraftEventListener):

    def __init__(self, event_types=None):
        if event_types == "no_audio":
            self.event_types = [
                str(TextEvent),
                str(DraftStartEvent),
                str(DraftEndEvent),
            ]
        elif event_types is None or event_types == "all" or event_types == "all_but_chunk":
            self.event_types = [
                str(AudioStartEvent),
                str(AudioStopEvent),
                str(AudioSpeechStartEvent),
                str(AudioSpeechStopEvent),
                str(TextEvent),
                str(DraftStartEvent),
                str(DraftEndEvent),
            ]
            if event_types != "all_but_chunk":
                self.event_types.append(str(AudioChunkEvent))
        elif event_types:
            self.event_types = event_types
        else:
            raise Exception(f'What the heck do you want? "{event_types}"')
        

    async def on_audio_event(self, event:AudioEvent):
        pass

    async def on_text_event(self, event: TextEvent):
        pass

    async def on_draft_event(self, event:DraftEvent):
        pass

