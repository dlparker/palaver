#!/usr/bin/env python3
import asyncio
import sounddevice as sd
from pathlib import Path
from palaver.scribe.listener.file_listener import FileListener
from palaver.scribe.listen_api import AudioEvent, AudioChunkEvent, AudioStartEvent, AudioStopEvent

# Match your recording settings
RECORD_SR = 48000
CHANNELS = 2
CHUNK_SEC = 0.03
BLOCKSIZE = int(CHUNK_SEC * RECORD_SR)

# Path to your test file
note1_wave = Path(__file__).parent.parent / "tests_slow" / "audio_samples" / "note1_base.wav"

class Player:
    def __init__(self, samplerate: int):
        self.stream = sd.OutputStream(
            samplerate=samplerate,
            channels=CHANNELS,
            dtype='float32',
            blocksize=BLOCKSIZE,
        )
        self.stopped = False

    async def on_event(self, event: AudioEvent):
        if isinstance(event, AudioChunkEvent):
            # AudioChunkEvent.data is already float32, shape (n_samples, 2) or (n_samples,)
            audio = event.data
            if audio.ndim == 1:
                audio = audio.reshape(-1, 1)
                audio = np.column_stack([audio, audio])  # mono → stereo
            self.stream.write(audio)
        elif isinstance(event, AudioStartEvent):
            print(event)
            print("start event")
        elif isinstance(event, AudioStopEvent):
            print(event)
            print("stop event")
            self.stop()
            self.stopped = True
        else:
            print(event)
            import ipdb;ipdb.set_trace()

    def start(self):
        self.stream.start()

    def stop(self):
        self.stream.stop()
        self.stream.close()


async def main():
    player = Player(RECORD_SR)
    listener = FileListener(RECORD_SR, CHANNELS, BLOCKSIZE, [note1_wave])

    await listener.set_event_listener(player)
    player.start()

    async with listener:
        await listener.start_recording()

        # Keep running until the file is fully played
        while listener._running and not player.stopped:
            await asyncio.sleep(0.1)

    player.stop()
    print("Playback finished.")

if __name__ == "__main__":
    asyncio.run(main())
