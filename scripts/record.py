#!/usr/bin/env python3
#!/usr/bin/env python3
import asyncio
import threading
from pathlib import Path
import traceback
import numpy as np
import soundfile as sf

from palaver.scribe.listener.mic_listener import MicListener
from palaver.scribe.listen_api import AudioChunkEvent, AudioStartEvent, AudioStopEvent, AsyncIterator, AudioErrorEvent

CHUNK_SEC = 0.03

class Recorder:

    def __init__(self, path):
        self.soundfile = None
        self.stopped = True
        self.path = path
        self.audio_buffer = []
        self.buffer_lock = threading.Lock()
        
    async def on_event(self, event):
        if isinstance(event, AudioStartEvent):
            self.sound_file = sf.SoundFile(self.path, mode='w',
                                           samplerate=event.sample_rate,
                                           channels=event.channels,
                                           subtype='PCM_24')
            print("Opened stream")
            print(event)
        elif isinstance(event, AudioChunkEvent):
            try:
                with self.buffer_lock:
                    data_to_write = np.concatenate(event.data)
                    self.audio_buffer.clear()
                    self.sound_file.write(data_to_write)
            except:
                print(f"Got error processing \n{event}\n{traceback.format_exc()}")
                self.stop()
        elif isinstance(event, AudioStopEvent):
            print(event)
            self.stop()
        elif isinstance(event, AudioErrorEvent):
            print(f"got error event\n {event.message}")
            self.stop()
        else:
            print(f"got unknown event {event}")
            self.stop()

    def start(self):
        self.stopped = False
        
    def stop(self):
        if self.stream:
            self.stream.stop()
            self.stream.close()
        self.stopped = True

async def main():
    listener = MicListener(chunk_duration=CHUNK_SEC)
    recorder = Recorder("test.wav")
    listener.add_event_listener(recorder)
    recorder.start()

    async with listener:          
        await listener.start_recording()
        try:
            while True:
                await asyncio.sleep(0.1)
        except KeyboardInterrupt:
            recorder.stop()
            print("\nRecording stopped.")


if __name__ == "__main__":
    asyncio.run(main())    
