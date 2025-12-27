#!/usr/bin/env python3
"""Event Net Server - Streaming audio pipeline events via websockets.

Refactored to use modular architecture with pluggable routers.
This script provides simple composition of server components.
"""
from pathlib import Path

import uvicorn

from palaver.fastapi.server import EventNetServer
from palaver.fastapi.routers.events import create_event_router
from palaver.fastapi.routers.status import create_status_router
from palaver.stage_markers import Stage, stage

from script_utils import create_base_parser, validate_model_path
from loggers import setup_logging


@stage(Stage.PROTOTYPE, track_coverage=True)
def create_parser():
    """Create argument parser for server.

    Returns:
        Configured ArgumentParser for server command-line options
    """
    default_model = Path("models/ggml-base.en.bin")
    parser = create_base_parser(
        'Event Net Server - Stream audio pipeline events via websockets',
        default_model
    )

    parser.add_argument(
        '--output-dir',
        type=Path,
        default=None,
        help='Enable draft recording to this directory (disabled if not provided)'
    )

    parser.add_argument(
        '--host',
        type=str,
        default='127.0.0.1',
        help='Host to bind server (default: 127.0.0.1)'
    )

    parser.add_argument(
        '--port',
        type=int,
        default=8000,
        help='Port to bind server (default: 8000)'
    )

    return parser


@stage(Stage.PROTOTYPE, track_coverage=True)
def main():
    """Main entry point for Event Net Server.

    Parses command-line arguments, creates EventNetServer, composes routers,
    and starts the server.
    """
    parser = create_parser()
    args = parser.parse_args()

    # Set logging level
    setup_logging(
        default_level=args.log_level,
        info_loggers=["EventNetServer", "EventRouter", "EventsRouter", "StatusRouter"],
        debug_loggers=[],
    )

    # Validate model path
    validate_model_path(args, parser)

    # Create server
    server = EventNetServer(
        model_path=args.model,
        draft_dir=args.output_dir
    )

    # Compose routers
    server.add_router(create_event_router(server))
    server.add_router(create_status_router(server))

    # Run server
    import logging
    logger = logging.getLogger("EventNetServer")
    logger.info(f"Starting server on {args.host}:{args.port}")
    uvicorn.run(
        server.app,
        host=args.host,
        port=args.port,
        log_level=args.log_level.lower()
    )


if __name__ == "__main__":
    main()
