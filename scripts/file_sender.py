#!/usr/bin/env python3
"""Send audio from a WAV file to vtt_server via websocket.

This tool wraps FileListener to stream audio events to a remote vtt_server
instance. The main logic is in send_file_to_server() which can be imported
and used in pytest tests.
"""
import asyncio
import argparse
import logging
from pathlib import Path
from typing import Optional
import json
from dataclasses import asdict

import websockets
import numpy as np

from palaver.utils.top_error import TopErrorHandler, TopLevelCallback
from palaver.scribe.audio.file_listener import FileListener
from palaver.scribe.audio_events import (
    AudioEvent,
    AudioChunkEvent,
    AudioStartEvent,
    AudioStopEvent,
    AudioEventListener
)

logger = logging.getLogger("FileSender")


class WebSocketEventSender(AudioEventListener):
    """Sends AudioEvents to a websocket endpoint."""

    def __init__(self, websocket):
        self.websocket = websocket
        self._event_count = 0

    async def on_audio_event(self, event: AudioEvent) -> None:
        """Serialize and send an AudioEvent over websocket."""
        # Serialize the event to JSON
        event_dict = asdict(event)

        # Convert numpy arrays to lists for JSON serialization
        if isinstance(event, AudioChunkEvent):
            event_dict['data'] = event.data.tolist()

        # Add event type for receiver to identify
        event_dict['event_type'] = event.event_type.value
        event_dict['event_class'] = event.__class__.__name__

        # Send as JSON
        await self.websocket.send(json.dumps(event_dict))

        self._event_count += 1
        if isinstance(event, AudioStartEvent):
            logger.info(f"Sent AudioStartEvent")
        elif isinstance(event, AudioStopEvent):
            logger.info(f"Sent AudioStopEvent (total events: {self._event_count})")
        elif isinstance(event, AudioChunkEvent) and self._event_count % 100 == 0:
            logger.debug(f"Sent {self._event_count} audio events...")


async def send_file_to_server(file_path: Path, server_url: str) -> int:
    if not file_path.exists():
        raise FileNotFoundError(f"Audio file not found: {file_path}")

    async def main_loop():
        # Construct full websocket URL
        logger.info(f"Connecting to {server_url}")
        logger.info(f"Sending audio from: {file_path}")
        async with websockets.connect(server_url) as websocket:
            logger.info("WebSocket connected")

            # Create event sender
            event_sender = WebSocketEventSender(websocket)

            # Create and configure FileListener
            async with FileListener(audio_file=file_path) as listener:
                # Attach our event sender to the listener
                listener.add_audio_event_listener(event_sender)

                # Start streaming from file
                await listener.start_streaming()

                # Wait for the reader task to complete
                # FileListener's _reader() will run until file is exhausted
                # For simulate_timing=True, this takes real-time duration
                # For simulate_timing=False, this is very fast
                if listener._reader_task:
                    try:
                        await listener._reader_task
                    except asyncio.CancelledError:
                        logger.info("Reader task cancelled")

            logger.info(f"File streaming complete ({event_sender._event_count} events sent)")
            return event_sender._event_count

    background_error_dict = None

    class ErrorCallback(TopLevelCallback):
        async def on_error(self, error_dict: dict):
            nonlocal background_error_dict
            background_error_dict = error_dict

    handler = TopErrorHandler(top_level_callback=ErrorCallback(), logger=logger)
    await handler.async_run(main_loop)


def main():
    """Command-line interface."""
    parser = argparse.ArgumentParser(
        description="Send WAV file audio to vtt_server via websocket",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Send file 
  %(prog)s audio.wav

  # Send to custom server
  %(prog)s audio.wav --server ws://192.168.1.100:8000
        """
    )

    parser.add_argument(
        'audio_file',
        type=Path,
        help='Path to WAV file to send'
    )

    parser.add_argument(
        '--server',
        type=str,
        default='ws://localhost:8000/events',
        help='Server WebSocket URL (default: ws://localhost:8000/events)'
    )


    parser.add_argument(
        '--log-level',
        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'],
        default='INFO',
        help='Set logging level (default: INFO)'
    )

    args = parser.parse_args()

    # Setup logging
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    # Run the main async function
    try:
        event_count = asyncio.run(
            send_file_to_server(
                file_path=args.audio_file,
                server_url=args.server
            )
        )
        print(f"\n✓ Successfully sent {event_count} events to {args.server}")
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        return 130

    return 0


if __name__ == "__main__":
    exit(main())
