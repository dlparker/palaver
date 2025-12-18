#!/usr/bin/env python3
import sys
import logging
from pathlib import Path
from pprint import pprint

from palaver.scribe.recorders.block_audio import BlockAudioRecorder
from palaver.scribe.audio.file_listener import FileListener
from palaver.scribe.core import PipelineConfig
from script_utils import create_base_parser, validate_model_path, scribe_pipeline_context
from palaver.utils.top_error import run_with_error_handler
from loggers import setup_logging
from api_wrapper import DefaultAPIWrapper

logger = logging.getLogger("ScribeRescan")


def create_parser():
    """Create the argument parser for rescanning blocks."""
    default_model = Path("models/multilang_whisper_large3_turbo.ggml")
    parser = create_base_parser(
        'Scribe Rescan - Re-transcribe recorded blocks with better models',
        default_model
    )

    parser.add_argument(
        '-p', '--play-sound',
        action="store_true",
        help="Play sound while transcribing"
    )

    parser.add_argument(
        'block_files_dir',
        type=Path,
        nargs='?',
        help='Directory used for block files storage'
    )

    return parser


def main():
    """Main entry point."""
    parser = create_parser()
    args = parser.parse_args()

    setup_logging(default_level=args.log_level,
                  info_loggers=[logger.name, 'BlockAudioRecorder','WhisperWrapper', 'Commands'],
                  more_loggers=[logger])

    # Validate model path
    validate_model_path(args, parser)

    # Determine buffer size based on model size
    short_models = [
        str(Path("models/ggml-base.en.bin").resolve()),
        str(Path("models/ggml-tiny.en.bin").resolve()),
        str(Path("models/ggml-medium.en.bin").resolve()),
    ]
    long_models = [
        str(Path("models/multilang_whisper_large3_turbo.ggml").resolve())
    ]

    if str(args.model.resolve()) in long_models:
        seconds_per_scan = 10
    else:
        seconds_per_scan = 2

    # Validate block files directory
    if args.block_files_dir is None:
        parser.error("Must supply block files dir")

    block_files_dir = Path(args.block_files_dir)
    if not block_files_dir.exists():
        parser.error(f"Block Files Dir does not exist: {block_files_dir}")

    # Setup block recorder and get last block
    block_recorder = BlockAudioRecorder(block_files_dir)
    print("*" * 80)
    last_block_files = block_recorder.get_last_block_files()
    pprint(last_block_files)
    block_recorder.set_rescan_block(last_block_files)

    # Create API wrapper with optional sound playback
    api_wrapper = DefaultAPIWrapper(play_sound=args.play_sound)

    try:
        async def main_task():
            # Create listener for the block audio file
            file_listener = FileListener(
                audio_file=last_block_files.sound_path,
                chunk_duration=0.03,
                simulate_timing=False,
            )

            # Create pipeline config with rescan-specific settings
            config = PipelineConfig(
                model_path=args.model,
                api_listener=api_wrapper,
                target_samplerate=16000,
                target_channels=1,
                use_multiprocessing=True,
                require_command_alerts=False,
                vad_silence_ms=3000,
                vad_speech_pad_ms=1000,
                seconds_per_scan=seconds_per_scan,
                block_recorder=block_recorder,
            )

            # Run pipeline with automatic context management
            async with scribe_pipeline_context(file_listener, config) as pipeline:
                await pipeline.start_listener()
                await pipeline.run_until_error_or_interrupt()

        # Run with standard error handling
        run_with_error_handler(main_task, logger)
        print("Rescan complete")

    except KeyboardInterrupt:
        print("\nShutdown complete.")
        sys.exit(0)
    except Exception as e:
        logger.error("Fatal error: %s", e, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
