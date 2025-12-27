#!/usr/bin/env python3
"""Event Net Test Client - POC websocket client for testing event streaming.

Connects to the Event Net Server, subscribes to events, and prints them.
"""
import asyncio
import json
import argparse
from typing import Set

import websockets

from palaver.stage_markers import Stage, stage
from palaver.scribe.audio_events import AudioEventType


@stage(Stage.POC, track_coverage=False)
async def event_client(
    server_url: str,
    event_types: Set[str],
    exclude_chunks: bool = True
):
    """Connect to server and stream events.

    Args:
        server_url: WebSocket URL (e.g., ws://localhost:8000/events)
        event_types: Set of event type names to subscribe to, or {"all"}
        exclude_chunks: If True, don't print AudioChunkEvent (default: True)
    """
    print(f"Connecting to {server_url}...")

    async with websockets.connect(server_url) as websocket:
        print(f"Connected! Subscribing to: {event_types}")

        # Send subscription message
        subscription = {"subscribe": list(event_types)}
        await websocket.send(json.dumps(subscription))
        print("Subscription sent. Waiting for events...\n")

        # Receive and print events
        event_count = 0
        chunk_count = 0
        try:
            async for message in websocket:
                event = json.loads(message)
                event_type = event.get("event_type")

                # Skip AudioChunkEvent if requested
                if exclude_chunks and event_type == AudioEventType.audio_chunk:
                    chunk_count += 1
                    if chunk_count % 100 == 0:
                        print(f"[Received {chunk_count} AudioChunkEvents - filtering...]")
                    continue

                event_count += 1
                print(f"[{event_count}] {event_type}")

                # Remove data field from AudioChunkEvent to avoid huge output
                if event_type == AudioEventType.audio_chunk and "data" in event:
                    event_copy = event.copy()
                    event_copy["data"] = f"<{len(event['data'])} samples>"
                    print(f"    {json.dumps(event_copy, indent=2)}\n")
                else:
                    print(f"    {json.dumps(event, indent=2)}\n")

        except websockets.exceptions.ConnectionClosed:
            print("\nConnection closed by server")
        except KeyboardInterrupt:
            print("\nShutting down client...")


@stage(Stage.POC, track_coverage=False)
def create_parser():
    """Create argument parser for test client."""
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

    parser.add_argument(
        '--events',
        type=str,
        nargs='+',
        default=['all'],
        help='Event types to subscribe to (default: all). Examples: AudioStartEvent TextEvent'
    )

    parser.add_argument(
        '--show-chunks',
        action='store_true',
        help='Include AudioChunkEvent in output (default: exclude)'
    )

    return parser


def main():
    parser = create_parser()
    args = parser.parse_args()

    # Convert event list to set
    event_types = set(args.events)

    # Run client
    try:
        asyncio.run(event_client(
            server_url=args.url,
            event_types=event_types,
            exclude_chunks=not args.show_chunks
        ))
    except KeyboardInterrupt:
        print("\nClient stopped")


if __name__ == "__main__":
    main()
