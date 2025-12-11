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


# Setup logging
logging.basicConfig(stream=sys.stdout, level=logging.WARNING)
logger = logging.getLogger("ScribeServer")


class CommandPrinter(CommandEventListener):
    
    async def on_command_event(self, event:ScribeCommandEvent):
        pprint(event)
        
class TextPrinter(TextEventListener):
    """
    Text event listener that prints transcribed text and detects commands.
    """

    def __init__(self, print_progress=False):
        self.full_text = ""
        self.print_progress = print_progress

    async def on_text_event(self, event: TextEvent):
        """Called when new transcribed text is available."""
        logger.info("*" * 100)
        logger.info("--------Text received---------")

        for seg in event.segments:
            logger.info(seg.text)
            if self.print_progress:
                print(seg.text + " ", end="", flush=True)
            self.full_text += seg.text + " "

        logger.info("--------END Text received---------")
        logger.info("*" * 100)

    def finish(self):
        """Called at the end to print accumulated text."""
        if not self.print_progress:
            print(self.full_text)
        else:
            print()  # Newline after progress printing


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

    text_printer = TextPrinter(print_progress=not args.no_progress)
    command_printer = CommandPrinter()

    mic_server = MicServer(
        model_path=args.model,
        text_event_listener=text_printer,
        command_event_listener=command_printer,
        chunk_duration=args.chunk_duration,
        use_multiprocessing=args.multiprocess,
        recording_output_dir=args.output_dir,
    )

    await mic_server.run()
    print("MicServer.run() complete")

async def run_playback_mode(args):
    """Run file playback transcription mode."""
    from palaver.scribe.playback_server import PlaybackServer

    text_printer = TextPrinter(print_progress=not args.no_progress)
    command_printer = CommandPrinter()

    playback_server = PlaybackServer(
        model_path=args.model,
        audio_files=args.files,
        text_event_listener=text_printer,
        command_event_listener=command_printer,
        chunk_duration=args.chunk_duration,
        simulate_timing=not args.no_simulate_timing,
        use_multiprocessing=args.multiprocess,
        recording_output_dir=args.output_dir,
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
