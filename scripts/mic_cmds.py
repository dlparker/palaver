#!/usr/bin/env python3
import sys
import logging
import asyncio
import time
from pathlib import Path

from palaver.scribe.recorders.wav_recorder import WavAudioRecorder
from palaver.scribe.audio.mic_listener import MicListener
from palaver.scribe.draft_events import DraftEvent, DraftStartEvent, DraftEndEvent
from palaver.scribe.text_events import TextEvent
from palaver.scribe.core import PipelineConfig
from palaver.scribe.api import ScribeAPIListener
from script_utils import create_base_parser, validate_model_path, scribe_pipeline_context, run_with_error_handler
from loggers import setup_logging
from sounder import play_signal_sound

logger = logging.getLogger("MatchTester")


class APIWrapper(ScribeAPIListener):

    def __init__(self):
        super().__init__()
        self.start_time = time.time()
        self.drafts = []
        self.current_draft = None
        self.signal_sounds = False
        self.text_buffer = ""

    async def on_pipeline_ready(self, pipeline):
        prompt = ""
        pipeline.prime_pump(prompt)

    async def on_draft_event(self, event: DraftEvent):
        if isinstance(event, DraftStartEvent):
            print("\n\nNew draft\n\n")
            self.current_draft = event.draft
        if isinstance(event, DraftEndEvent):
            print("\n\nFinihsed draft\n\n")
            self.current_draft = None
            print('-'*100)
            print(event.draft.full_text)
            print('-'*100)
            
    async def on_text_event(self, event: TextEvent):
        et = time.time() - self.start_time
        print(f"-------- Text at {et:7.4f} -------")
        print(f"{et:7.4f}: {event.text}")
            

def create_parser():
    default_model = Path("models/ggml-base.en.bin")
    parser = create_base_parser(
        'Scribe Mic Runner - Real-time microphone transcription',
        default_model
    )

    parser.add_argument(
        '--wav','-w',
        type=Path,
        default=None,
        help='Enable recording and save to this WAV file (disabled if not provided)'
    )

    return parser

def main():
    parser = create_parser()
    args = parser.parse_args()

    # Set logging level
    setup_logging(default_level=args.log_level,
                  info_loggers=[logger.name, ],
                  debug_loggers=[],
                  more_loggers=[logger])

    # Validate model path
    validate_model_path(args, parser)

    # Create API wrapper
    api_wrapper = APIWrapper()

    wav_recorder = None
    if args.wav:
        wav_recorder = WavAudioRecorder(args.wav)
        logger.info(f"Wav recorder enabled: {args.wav}")
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
            )
            # Run pipeline with automatic context management
            async with scribe_pipeline_context(mic_listener, config) as pipeline:
                if args.wav:
                    pipeline.add_api_listener(wav_recorder)
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
