
from palaver.scribe.audio_events import (AudioEvent,
                                         AudioErrorEvent,
                                         AudioSpeechStartEvent,
                                         AudioSpeechStopEvent,
                                         AudioChunkEvent,
                                         )


class UncutVTT:

    def __init__(self, source, on_vad_signals=False):
        self._source = source
        self._on_vad_signals = on_vad_signals
        self._copying = not on_vad_signals

    def on_audio_event(self, event: AudioEvent):
        if not self._copying:
            if not isinstance(event, AudioSpeechStartEvent):
                return
            self._copying = True
        elif self._on_vad_signals:
            if not isinstance(event, AudioSpeechStopEvent):
                self._copying = False
                return
        print(f"will transcribe from {event}")
        

                
            

        
