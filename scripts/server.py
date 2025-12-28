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
from palaver.fastapi.routers.revisions import create_revision_router
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

    # Rescan mode arguments (Story 008)
    parser.add_argument(
        '--rescan-mode',
        action='store_true',
        help='Enable rescan mode (connect to remote audio source and rescan drafts)'
    )

    parser.add_argument(
        '--audio-source-url',
        type=str,
        default=None,
        help='WebSocket URL to subscribe to for audio events (e.g., ws://machine1:8765/events)'
    )

    parser.add_argument(
        '--revision-target',
        type=str,
        default=None,
        help='HTTP URL to send completed revisions (e.g., http://machine1:8765/api/revisions)'
    )

    parser.add_argument(
        '--rescan-buffer-seconds',
        type=float,
        default=60.0,
        help='Size of audio buffer in seconds for rescan mode (default: 60.0)'
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
        info_loggers=["EventNetServer", "EventRouter", "EventsRouter", "StatusRouter", "RevisionRouter", "RescanListener"],
        debug_loggers=[],
    )

    # Validate model path
    validate_model_path(args, parser)

    # Validate rescan mode arguments (Story 008)
    if args.rescan_mode:
        if not args.audio_source_url:
            parser.error("--rescan-mode requires --audio-source-url")
        if not args.revision_target:
            parser.error("--rescan-mode requires --revision-target")

    # Run in rescan mode (Story 008)
    if args.rescan_mode:
        import asyncio
        asyncio.run(run_rescan_mode(args))
    else:
        # Normal server mode
        run_server_mode(args)


def run_server_mode(args):
    """Run normal server mode with mic listener and event broadcasting."""
    # Create server
    server = EventNetServer(
        model_path=args.model,
        draft_dir=args.output_dir
    )

    # Compose routers
    server.add_router(create_event_router(server))
    server.add_router(create_status_router(server))
    server.add_router(create_revision_router(server))

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


async def run_rescan_mode(args):
    """Run rescan mode - subscribe to remote audio source and rescan drafts.

    Story 008: Rescan Mode for Distributed High-Quality Transcription

    This mode creates a local WhisperWrapper with high-quality model,
    subscribes to a remote audio source via WebSocket, buffers audio,
    and rescans completed drafts, submitting improved transcriptions back
    to the remote server.
    """
    import logging
    from palaver.fastapi.rescan_listener import RescanListener
    from palaver.scribe.scriven.whisper import WhisperWrapper
    from palaver.scribe.scriven.drafts import DraftMaker
    from palaver.scribe.api import ScribeAPIListener
    from palaver.scribe.draft_events import DraftEvent
    from palaver.utils.top_error import TopErrorHandler, TopLevelCallback, ERROR_HANDLER

    logger = logging.getLogger("RescanMode")

    # Setup error handler
    class ErrorCallback(TopLevelCallback):
        async def on_error(self, error_dict: dict):
            logger.error(f"Rescan error: {error_dict}")

    error_handler = TopErrorHandler(top_level_callback=ErrorCallback(), logger=logger)
    token = ERROR_HANDLER.set(error_handler)

    # Create API wrapper for rescan mode (routes events to DraftMaker)
    class RescanAPIWrapper(ScribeAPIListener):
        """API wrapper that routes TextEvents to DraftMaker for rescan mode."""

        def __init__(self):
            super().__init__()
            self.draft_maker = DraftMaker()
            self._draft_listeners = []

        async def on_text_event(self, event):
            """Route TextEvents to DraftMaker."""
            await self.draft_maker.handle_text_event(event)

        async def on_draft_event(self, event):
            """Route DraftEvents from DraftMaker to registered listeners."""
            for listener in self._draft_listeners:
                await listener.on_draft_event(event)

        def add_draft_listener(self, listener):
            """Add listener for DraftEvents from DraftMaker."""
            self._draft_listeners.append(listener)

        async def on_pipeline_ready(self, pipeline):
            """Wire up DraftMaker to emit events to this wrapper."""
            self.draft_maker.add_event_listener(self)

    try:
        logger.info("Starting rescan mode...")
        logger.info(f"  Audio source: {args.audio_source_url}")
        logger.info(f"  Revision target: {args.revision_target}")
        logger.info(f"  Local model: {args.model}")
        logger.info(f"  Buffer size: {args.rescan_buffer_seconds}s")

        # Create local WhisperWrapper for high-quality rescanning
        # Use multiprocessing for better performance on Machine 2 (better GPU)
        whisper = WhisperWrapper(
            model_path=str(args.model),
            use_mp=True,
        )

        # Create API wrapper (contains DraftMaker)
        api_wrapper = RescanAPIWrapper()

        # Wire up: WhisperWrapper → API wrapper (TextEvents)
        whisper.add_text_event_listener(api_wrapper)

        # Wire up DraftMaker to emit through API wrapper
        await api_wrapper.on_pipeline_ready(None)

        # Create RescanListener
        rescan_listener = RescanListener(
            audio_source_url=args.audio_source_url,
            revision_target=args.revision_target,
            local_whisper=whisper,
            local_draft_maker=api_wrapper.draft_maker,
            buffer_seconds=args.rescan_buffer_seconds,
        )

        # Wire up: API wrapper → RescanListener (DraftEvents)
        api_wrapper.add_draft_listener(rescan_listener)

        # Connect to remote audio source
        await rescan_listener.connect()
        logger.info("Connected to remote audio source, listening for drafts...")

        # Run until interrupted
        try:
            while True:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            logger.info("Shutting down rescan mode...")
        finally:
            await rescan_listener.disconnect()
            await whisper.graceful_shutdown(timeout=3.0)
            logger.info("Rescan mode shutdown complete")

    finally:
        ERROR_HANDLER.reset(token)


if __name__ == "__main__":
    main()
