#!/usr/bin/env python3
import sys
import logging
import asyncio
from pathlib import Path

from palaver.scribe.api_wrapper import DefaultAPIWrapper
from palaver.scribe.recorders.block_audio import BlockAudioRecorder
from palaver.scribe.listener.mic_listener import MicListener
from palaver.scribe.core import PipelineConfig
from palaver.scribe.script_utils import create_base_parser, validate_model_path, scribe_pipeline_context
from palaver.utils.top_error import run_with_error_handler
from palaver.scribe.loggers import setup_logging

logger = logging.getLogger("ScribeMicRunner")


def create_parser():
    """Create the argument parser for mic recording."""
    default_model = Path("models/ggml-base.en.bin")
    parser = create_base_parser(
        'Scribe Mic Runner - Real-time microphone transcription',
        default_model
    )

    parser.add_argument(
        '--output-dir',
        type=Path,
        default=None,
        help='Enable recording and save WAV file to this directory (disabled if not provided)'
    )

    return parser


def main():
    parser = create_parser()
    args = parser.parse_args()

    # Set logging level
    setup_logging(default_level=args.log_level,
                  info_loggers=[logger.name, 'Commands'],
                  debug_loggers=[],
                  more_loggers=[logger])

    # Validate model path
    validate_model_path(args, parser)

    # Create API wrapper
    api_wrapper = DefaultAPIWrapper()

    # Setup block recorder if requested
    block_recorder = None
    if args.output_dir:
        block_recorder = BlockAudioRecorder(args.output_dir)
        logger.info(f"Block recorder enabled: {args.output_dir}")

    try:
        async def main_task():
            # Create listener
            mic_listener = MicListener(chunk_duration=0.03)

            # Create pipeline config
            config = PipelineConfig(
                model_path=args.model,
                api_listener=api_wrapper,
                target_samplerate=16000,
                target_channels=1,
                use_multiprocessing=True,
                block_recorder=block_recorder,
            )

            # Run pipeline with automatic context management
            async with scribe_pipeline_context(mic_listener, config) as pipeline:
                await pipeline.start_listener()
                try:
                    await pipeline.run_until_error_or_interrupt()
                except (KeyboardInterrupt, asyncio.CancelledError):
                    print("\nControl-C detected. Shutting down...")

        # Run with standard error handling
        run_with_error_handler(main_task, logger)
        print("Microphone Listening complete")

    except KeyboardInterrupt:
        print("\nShutdown complete.")
        sys.exit(0)
    except Exception as e:
        logger.error("Fatal error: %s", e, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
