"""Shared utilities for Palaver scripts."""

from pathlib import Path
import argparse
from contextlib import asynccontextmanager

from palaver.scribe.core import ScribePipeline


def create_base_parser(description: str, default_model: Path) -> argparse.ArgumentParser:
    """
    Create base argument parser with common arguments.

    Args:
        description: Script description for help text
        default_model: Default path to Whisper model file

    Returns:
        ArgumentParser with common arguments added
    """
    parser = argparse.ArgumentParser(
        description=description,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        '--model',
        type=Path,
        default=default_model,
        help=f'Path to Whisper model file (default: {default_model})'
    )

    parser.add_argument(
        '--log-level',
        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'],
        default='WARNING',
        help='Set logging level'
    )

    return parser


def validate_model_path(args, parser):
    """
    Validate that model file exists.

    Args:
        args: Parsed arguments
        parser: ArgumentParser instance (for error reporting)

    Raises:
        SystemExit: If model file doesn't exist
    """
    if not args.model.exists():
        parser.error(f"Model file does not exist: {args.model}")


@asynccontextmanager
async def scribe_pipeline_context(listener, config):
    """
    Context manager that properly nests listener and pipeline contexts.

    Usage:
        async with scribe_pipeline_context(listener, config) as pipeline:
            await pipeline.start_listener()
            await pipeline.run_until_error_or_interrupt()

    Args:
        listener: AudioListener instance (MicListener or FileListener)
        config: PipelineConfig instance

    Yields:
        ScribePipeline instance
    """
    async with listener:
        async with ScribePipeline(listener, config) as pipeline:
            yield pipeline
