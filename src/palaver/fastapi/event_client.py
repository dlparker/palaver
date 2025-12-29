import asyncio
import json
import logging

import websockets

from palaver.scribe.audio_events import AudioEventType

logger = logging.getLogger("EventClientFastAPI")

class EventClientFastAPI:

    def __init__(self, server_url, event_types: set[str], exclude_chunks: bool = False):
        self.server_url = server_url
        

async def event_client(
    server_url: str,
):
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

                # Special handling for AudioChunkEvent to highlight key info
                if event_type == AudioEventType.audio_chunk:
                    sample_rate = event.get("sample_rate", "unknown")
                    in_speech = event.get("in_speech", False)
                    data_len = len(event.get("data", []))
                    print(f"[{event_count}] {event_type} - {sample_rate}Hz, in_speech={in_speech}, {data_len} samples")

                    if "data" in event:
                        event_copy = event.copy()
                        event_copy["data"] = f"<{data_len} samples>"
                        print(f"    {json.dumps(event_copy, indent=2)}\n")
                    else:
                        print(f"    {json.dumps(event, indent=2)}\n")
                else:
                    print(f"[{event_count}] {event_type}")
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
        '--with-chunks',
        action='store_true',
        help='Subscribe to AudioChunkEvent (speech-only chunks with in_speech=True). Server filters by default.'
    )

    parser.add_argument(
        '--show-chunks',
        action='store_true',
        help='Print AudioChunkEvent to console (default: show summary only). Requires --with-chunks.'
    )

    return parser


def main():
    parser = create_parser()
    args = parser.parse_args()

    # Convert event list to set
    event_types = set(args.events)

    # Add AudioChunkEvent if --with-chunks is specified
    if args.with_chunks:
        event_types.add("AudioChunkEvent")

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
