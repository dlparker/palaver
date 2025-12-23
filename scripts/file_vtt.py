#!/usr/bin/env python3
import sys
import logging
from pathlib import Path

from palaver.scribe.audio.file_listener import FileListener
from palaver.scribe.core import PipelineConfig
from script_utils import create_base_parser, validate_model_path, scribe_pipeline_context, run_with_error_handler
from loggers import setup_logging
from api_wrapper import DefaultAPIWrapper

logger = logging.getLogger("ScribePlayback")


def create_parser():
    """Create the argument parser for file playback."""
    default_model = Path("models/ggml-base.en.bin")
    parser = create_base_parser(
        'Scribe Playback - Audio transcription from file',
        default_model
    )

    parser.add_argument(
        '-p', '--play-sound',
        action="store_true",
        help="Play sound while transcribing"
    )

    parser.add_argument(
        '--output-dir',
        type=Path,
        default=None,
        help='Enable recording and save WAV file to this directory (disabled if not provided)'
    )

    parser.add_argument(
        '--rescan-path',
        type=Path,
        default=None,
        help='Rescan the selected file using longer windows'
    )

    parser.add_argument(
        'file',
        type=Path,
        nargs='?',
        help='Audio file to transcribe'
    )

    return parser


def main():
    """Main entry point."""
    parser = create_parser()
    args = parser.parse_args()

    setup_logging(default_level=args.log_level,
                  info_loggers=[logger.name,],
                  debug_loggers=[],
                  more_loggers=[logger])


    seconds_per_scan = 2
    if not args.file and not args.rescan_path:
        parser.error("Audio file argument is required or --rescan-path")
        
    if args.file and args.rescan_path:
        parser.error("Choose one ,--rescan-path or file argument")
    if args.file and not args.file.exists():
        parser.error(f"Audio file does not exist: {args.file}")
    if args.rescan_path and not args.rescan_path.exists():
        parser.error(f"Rescan path  does not exist: {args.rescan_path}")
    if args.rescan_path:
        filepath = args.rescan_path
        model = Path("models/multilang_whisper_large3_turbo.ggml")
        print(f"rescan forcing model to {model}")
        seconds_per_scan = 15
    else:
        filepath = args.file
        model = args.model

    if not model.exists():
        parser.error(f"Model file does not exist: {model}")

    # Setup draft recorder if requested
    draft_recorder = None
    if args.output_dir:
        sim_timing = False
        draft_recorder = DravfRecorder(args.output_dir)
        logger.info(f"Draft recorder enabled: {args.output_dir}")

    # Create API wrapper with optional sound playback
    api_wrapper = DefaultAPIWrapper(draft_recorder=draft_recorder, play_sound=args.play_sound)
    
    try:
        async def main_task():
            # Create listener
            file_listener = FileListener(
                audio_file=filepath,
                chunk_duration=0.03,
                simulate_timing=False,
            )

            # Create pipeline config with playback-specific settings
            config = PipelineConfig(
                model_path=model,
                api_listener=api_wrapper,
                target_samplerate=16000,
                target_channels=1,
                use_multiprocessing=True,
                vad_silence_ms=3000,
                vad_speech_pad_ms=1000,
                seconds_per_scan=seconds_per_scan,
            )

            # Run pipeline with automatic context management
            async with scribe_pipeline_context(file_listener, config) as pipeline:
                await pipeline.start_listener()
                await pipeline.run_until_error_or_interrupt()

        # Run with standard error handling
        run_with_error_handler(main_task, logger)
        print("File playback complete")

    except KeyboardInterrupt:
        print("\nShutdown complete.")
        sys.exit(0)
    except Exception as e:
        logger.error("Fatal error: %s", e, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
