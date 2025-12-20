#!/usr/bin/env python3
import sys
import logging
from pathlib import Path

from palaver.scribe.recorders.block_audio import BlockAudioRecorder
from palaver.scribe.audio.file_listener import FileListener
from palaver.scribe.core import PipelineConfig
from script_utils import create_base_parser, validate_model_path, scribe_pipeline_context, run_with_error_handler
from loggers import setup_logging
from api_wrapper import DefaultAPIWrapper

logger = logging.getLogger("ScribePlayback")


def create_parser():
    """Create the argument parser for file playback."""
    default_model = Path("models/ggml-medium.en.bin")
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
                  info_loggers=[logger.name, 'BlockAudioRecorder', 'Commands'],
                  debug_loggers=[],
                  more_loggers=[logger])

    # Validate paths
    validate_model_path(args, parser)

    if not args.file:
        parser.error("Audio file argument is required")
    if not args.file.exists():
        parser.error(f"Audio file does not exist: {args.file}")

    # Create API wrapper with optional sound playback
    api_wrapper = DefaultAPIWrapper(play_sound=args.play_sound)

    # Setup block recorder if requested
    block_recorder = None
    if args.output_dir:
        sim_timing = False
        if sim_timing:
            chunk_ring_seconds = 3
        else:
            # Note: There is something causing chunk events to get delayed
            # in delivery to the block recorder when we playback a file at
            # full data speed. This workaround uses a larger ring buffer.
            # The operational modes will be more like a mic or streaming.
            chunk_ring_seconds = 12
        block_recorder = BlockAudioRecorder(args.output_dir, chunk_ring_seconds)
        logger.info(f"Block recorder enabled: {args.output_dir}")

    try:
        async def main_task():
            # Create listener
            file_listener = FileListener(
                audio_file=args.file,
                chunk_duration=0.03,
                simulate_timing=False,
            )

            # Create pipeline config with playback-specific settings
            config = PipelineConfig(
                model_path=args.model,
                api_listener=api_wrapper,
                target_samplerate=16000,
                target_channels=1,
                use_multiprocessing=True,
                require_command_alerts=False,
                vad_silence_ms=3000,
                vad_speech_pad_ms=1000,
                seconds_per_scan=3,
                block_recorder=block_recorder,
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
