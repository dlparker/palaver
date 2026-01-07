import asyncio
from pprint import pprint
from palaver_client.api import PalaverEventListener
from palaver_client.rest_client import PalaverRestClient
from palaver_client.websocket_client import PalaverWebSocketClient
from palaver_shared.audio_events import AudioEvent, AudioStartEvent, AudioStopEvent, AudioChunkEvent
from palaver_shared.audio_events import AudioSpeechStartEvent, AudioSpeechStopEvent
from palaver_shared.text_events import TextEvent
from palaver_shared.draft_events import DraftEvent, DraftStartEvent, DraftEndEvent, DraftRescanEvent

base_url = "http://localhost:8000"

class Listener(PalaverEventListener):

    first_chunk = None
    
    async def on_draft_event(self, event:DraftEvent):
        print("-"*80)
        print("Draft event")
        pprint(event.draft)
        print("-"*80)

    async def on_text_event(self, event: TextEvent):
        print(event)

    async def on_audio_event(self, event:AudioEvent):
        if isinstance(event, AudioChunkEvent):
            if not self.first_chunk:
                self.first_chunk = event
                print("Got a chunk")
        else:
            print(event)
    
async def main():

    rest_cli = PalaverRestClient(base_url)
    async with PalaverRestClient("http://localhost:8000") as client:
        drafts = await client.fetch_all_drafts(limit=1)
        if drafts:
            print(drafts[0])
        else:
            print("No draft found")
        async with PalaverWebSocketClient(listener=Listener(),
                                          palaver_url="http://localhost:8000") as ws_client:
            ws_client.start_listening()
            while True:
                await asyncio.sleep(1)

if __name__ == "__main__":
    asyncio.run(main())
