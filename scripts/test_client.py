#!/usr/bin/env python3
"""Event Net Test Client - POC websocket client for testing event streaming.

Connects to the Event Net Server, subscribes to events, and prints them.
"""
import asyncio
import json
import argparse
from pprint import pprint
from typing import Set

import websockets

from palaver.scribe.audio_events import AudioChunkEvent
from palaver.scribe.text_events import TextEvent
from palaver.scribe.draft_events import DraftEvent
from palaver.utils.serializers import event_from_dict


async def main():
    parser = argparse.ArgumentParser(
        description='Event Net Test Client - Subscribe to server events',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        '--url',
        type=str,
        default='ws://localhost:8000/events',
        help='WebSocket URL (default: ws://localhost:8000/events)'
    )

    args = parser.parse_args()
    
    print(f"Connecting to {args.url}...")

    async with websockets.connect(args.url) as websocket:
        print(f"Connected! Subscribing to: all")

        # Send subscription message
        subscription = {"subscribe": ['all']}
        await websocket.send(json.dumps(subscription))
        print("Subscription sent. Waiting for events...\n")

        chunk_count = 0
        try:
            async for message in websocket:
                event_dict = json.loads(message)
                event = event_from_dict(event_dict)
                if isinstance(event, AudioChunkEvent):
                    if chunk_count % 100 == 0:
                        pprint(event)
                    chunk_count += 1
                else:
                    pprint(event)

        except websockets.exceptions.ConnectionClosed:
            print("\nConnection closed by server")
        except KeyboardInterrupt:
            print("\nShutting down client...")


if __name__ == "__main__":
    asyncio.run(main())
