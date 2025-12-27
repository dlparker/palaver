"""EventNetServer - FastAPI server base class for event streaming.

Provides lifecycle management, shared context, and router composition for
FastAPI servers that stream audio pipeline events.
"""
import asyncio
import logging
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI

from palaver.scribe.audio.mic_listener import MicListener
from palaver.scribe.core import PipelineConfig, ScribePipeline
from palaver.scribe.recorders.sql_drafts import SQLDraftRecorder
from palaver.utils.top_error import TopErrorHandler, TopLevelCallback, ERROR_HANDLER
from palaver.fastapi.event_router import EventRouter
from palaver.stage_markers import Stage, stage

# Import from scripts - this is a transitional dependency
# TODO: Move DefaultAPIWrapper to src/palaver when promoting to MVP
import sys
from pathlib import Path
scripts_path = Path(__file__).parent.parent.parent.parent / "scripts"
sys.path.insert(0, str(scripts_path))
from api_wrapper import DefaultAPIWrapper
sys.path.pop(0)

logger = logging.getLogger("EventNetServer")


@stage(Stage.PROTOTYPE, track_coverage=True)
class EventNetServer:
    """FastAPI server base class for streaming audio pipeline events.

    Provides:
    - Pipeline lifecycle management via lifespan context
    - Shared context (pipeline, event_router, config) for routers
    - Router composition via add_router() method
    - Automatic error handling setup

    Example:
        server = EventNetServer(model_path=Path("models/ggml-base.en.bin"))
        server.add_router(create_event_router(server))
        server.add_router(create_status_router(server))
        uvicorn.run(server.app, host="0.0.0.0", port=8000)
    """

    def __init__(self, model_path: Path, draft_dir: Path = None):
        """Initialize EventNetServer.

        Args:
            model_path: Path to Whisper model file
            draft_dir: Optional directory for draft recording (None disables recording)
        """
        self.model_path = model_path
        self.draft_dir = draft_dir
        self.event_router = EventRouter()
        self.pipeline: Optional[ScribePipeline] = None
        self.mic_listener: Optional[MicListener] = None
        self.app = FastAPI(lifespan=self.lifespan)

    def add_router(self, router):
        """Add a router to the FastAPI application.

        Args:
            router: FastAPI APIRouter instance to include
        """
        self.app.include_router(router)

    @asynccontextmanager
    async def lifespan(self, app: FastAPI):
        """Manage pipeline lifecycle with FastAPI app.

        This lifespan context:
        - Sets up error handling
        - Creates and configures the audio pipeline
        - Starts the microphone listener and pipeline
        - Yields control to FastAPI
        - Cleans up on shutdown

        Args:
            app: FastAPI application instance
        """
        # Setup error handler for pipeline context
        class ErrorCallback(TopLevelCallback):
            async def on_error(self, error_dict: dict):
                logger.error(f"Pipeline error: {error_dict}")

        error_handler = TopErrorHandler(top_level_callback=ErrorCallback(), logger=logger)
        token = ERROR_HANDLER.set(error_handler)

        try:
            # Startup: Create and start pipeline
            logger.info("Starting audio pipeline...")

            # Setup draft recorder if requested
            draft_recorder = None
            if self.draft_dir:
                draft_recorder = SQLDraftRecorder(self.draft_dir)
                logger.info(f"Draft recorder enabled: {self.draft_dir}")

            # Create API wrapper
            api_wrapper = DefaultAPIWrapper(draft_recorder=draft_recorder)

            # Create mic listener
            self.mic_listener = MicListener(chunk_duration=0.03)

            # Create pipeline config
            config = PipelineConfig(
                model_path=self.model_path,
                api_listener=api_wrapper,
                target_samplerate=16000,
                target_channels=1,
                use_multiprocessing=True,
            )

            # Start pipeline with nested context managers
            async with self.mic_listener:
                async with ScribePipeline(self.mic_listener, config) as pipeline:
                    self.pipeline = pipeline

                    # Add event router as listener (to_VAD=True for 16kHz downsampled audio)
                    pipeline.add_api_listener(self.event_router, to_VAD=True)

                    # Start listening
                    await pipeline.start_listener()
                    logger.info("Audio pipeline started")

                    # Yield to run the app
                    yield

                    # Shutdown handled by context manager exit
                    logger.info("Shutting down audio pipeline...")
        finally:
            ERROR_HANDLER.reset(token)
