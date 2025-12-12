#!/usr/bin/env python3
"""
Scribe Server - Unified CLI for audio transcription.

Supports two modes:
  - mic: Real-time transcription from microphone
  - playback: Transcription from audio files
"""
import sys
import asyncio
import logging
from pathlib import Path
from pprint import pprint
import argparse

from palaver.scribe.text_events import TextEvent, TextEventListener
from palaver.scribe.scriven.wire_commands import ScribeCommandEvent, CommandEventListener
from palaver.scribe.api import ScribeAPIListener


# Setup logging
logging.basicConfig(stream=sys.stdout, level=logging.WARNING)
logger = logging.getLogger("ScribeServer")

class MyListener(ScribeAPIListener):

    def __init__(self):
        super().__init__()
        self.full_text = ""
        self.blocks = []

    async def on_command_event(self, event:ScribeCommandEvent):
        pprint(event)
        if event.command.starts_text_block:
            self.blocks.append("")
            print("-------------------------------------------")
            print(f"MyListener starting block {len(self.blocks)}")
            print("-------------------------------------------")
        elif event.command.ends_text_block:
            print("-------------------------------------------")
            print(f"MyListener ending block {len(self.blocks)}")
            print("-------------------------------------------")
            print("++++++=++++++++++++++++++++++++++++++++++++")
            if len(self.blocks) > 0:
                print(self.blocks[-1])
            print("++++++=++++++++++++++++++++++++++++++++++++")

    async def on_text_event(self, event: TextEvent):
        """Called when new transcribed text is available."""
        logger.info("*" * 100)
        logger.info("--------Text received---------")

        for seg in event.segments:
            logger.info(seg.text)
            print(seg.text + " ", end="", flush=True)
            self.full_text += seg.text + " "
            if len(self.blocks) > 0:
                self.blocks[-1] += seg.text + " "
        logger.info("--------END Text received---------")
        logger.info("*" * 100)
        

def create_parser() -> argparse.ArgumentParser:
    """Create the argument parser for scribe_server."""
    parser = argparse.ArgumentParser(
        description='Scribe Server - Audio transcription with microphone or file playback',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Transcribe from microphone
  %(prog)s mic --model models/ggml-medium.en.bin

  # Transcribe from audio file
  %(prog)s playback --model models/ggml-medium.en.bin audio.wav

  # Multiple files with simulated timing
  %(prog)s playback --model models/ggml-medium.en.bin file1.wav file2.wav
        """
    )

    # Common arguments
    parser.add_argument(
        '--model',
        type=Path,
        default=Path("models/ggml-medium.en.bin"),
        help='Path to Whisper model file (default: models/ggml-medium.en.bin)'
    )

    parser.add_argument(
        '--no-progress',
        action='store_true',
        help='Disable real-time progress printing (print only at end)'
    )

    parser.add_argument(
        '--multiprocess',
        action='store_true',
        help='Use multiprocessing for Whisper (vs threading)'
    )

    parser.add_argument(
        '--log-level',
        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'],
        default='WARNING',
        help='Set logging level'
    )

    parser.add_argument(
        '--output-dir',
        type=Path,
        default=None,
        help='Enable recording and save WAV files to this directory (disabled if not provided)'
    )

    parser.add_argument(
        '--mqtt-broker',
        type=str,
        default=None,
        help='MQTT broker address (enables MQTT publishing if provided)'
    )

    parser.add_argument(
        '--mqtt-port',
        type=int,
        default=1883,
        help='MQTT broker port (default: 1883)'
    )

    parser.add_argument(
        '--mqtt-topic',
        type=str,
        default='palaver/scribe',
        help='MQTT base topic (default: palaver/scribe)'
    )

    parser.add_argument(
        '--mqtt-username',
        type=str,
        default=None,
        help='MQTT username (optional)'
    )

    parser.add_argument(
        '--mqtt-password',
        type=str,
        default=None,
        help='MQTT password (optional)'
    )

    # Subcommands for different modes
    subparsers = parser.add_subparsers(dest='mode', required=True, help='Server mode')

    # Mic mode
    mic_parser = subparsers.add_parser(
        'mic',
        help='Transcribe from microphone'
    )
    mic_parser.add_argument(
        '--chunk-duration',
        type=float,
        default=0.03,
        help='Audio chunk duration in seconds (default: 0.03)'
    )

    # Playback mode
    playback_parser = subparsers.add_parser(
        'playback',
        help='Transcribe from audio file(s)'
    )
    playback_parser.add_argument(
        'files',
        type=Path,
        nargs='+',
        help='Audio file(s) to transcribe'
    )
    playback_parser.add_argument(
        '--chunk-duration',
        type=float,
        default=0.03,
        help='Audio chunk duration in seconds (default: 0.03)'
    )
    playback_parser.add_argument(
        '--no-simulate-timing',
        action='store_true',
        help='Process files as fast as possible (no timing simulation)'
    )

    return parser


async def run_mic_mode(args):
    """Run microphone transcription mode."""
    from palaver.scribe.mic_server import MicServer

    api_listener = MyListener()

    # Prepare MQTT config if broker provided
    mqtt_config = None
    if args.mqtt_broker:
        mqtt_config = {
            'broker': args.mqtt_broker,
            'port': args.mqtt_port,
            'base_topic': args.mqtt_topic,
            'username': args.mqtt_username,
            'password': args.mqtt_password,
        }

    mic_server = MicServer(
        model_path=args.model,
        api_listener=api_listener,
        chunk_duration=args.chunk_duration,
        use_multiprocessing=args.multiprocess,
        recording_output_dir=args.output_dir,
        mqtt_config=mqtt_config,
    )

    await mic_server.run()
    print("MicServer.run() complete")

async def run_playback_mode(args):
    """Run file playback transcription mode."""
    from palaver.scribe.playback_server import PlaybackServer

    api_listener = MyListener()

    # Prepare MQTT config if broker provided
    mqtt_config = None
    if args.mqtt_broker:
        mqtt_config = {
            'broker': args.mqtt_broker,
            'port': args.mqtt_port,
            'base_topic': args.mqtt_topic,
            'username': args.mqtt_username,
            'password': args.mqtt_password,
        }

        
    playback_server = PlaybackServer(
        model_path=args.model,
        audio_files=args.files,
        api_listener=api_listener,
        chunk_duration=args.chunk_duration,
        simulate_timing=not args.no_simulate_timing,
        use_multiprocessing=args.multiprocess,
        recording_output_dir=args.output_dir,
        mqtt_config=mqtt_config,
    )

    await playback_server.run()
    print("PlaybackServer.run() complete")

def main():
    """Main entry point."""
    parser = create_parser()
    args = parser.parse_args()

    # Set logging level
    logging.getLogger().setLevel(getattr(logging, args.log_level))

    # Validate model path
    if not args.model.exists():
        parser.error(f"Model file does not exist: {args.model}")

    # Mode-specific validation
    if args.mode == 'playback':
        for file_path in args.files:
            if not file_path.exists():
                parser.error(f"Audio file does not exist: {file_path}")

    # Run the appropriate mode
    try:
        if args.mode == 'mic':
            asyncio.run(run_mic_mode(args))
        elif args.mode == 'playback':
            asyncio.run(run_playback_mode(args))
    except KeyboardInterrupt:
        print("\nShutdown complete.")
        sys.exit(0)
    except Exception as e:
        logger.error("Fatal error: %s", e, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
