#!/usr/bin/env python3
import sys
import logging
import asyncio
import time
from pathlib import Path

from palaver.scribe.recorders.wav_recorder import WavAudioRecorder
from palaver.scribe.audio.mic_listener import MicListener
from palaver.scribe.text_events import TextEvent
from palaver.scribe.core import PipelineConfig
from palaver.scribe.api import ScribeAPIListener
from script_utils import create_base_parser, validate_model_path, scribe_pipeline_context, run_with_error_handler
from loggers import setup_logging
from cmd_tool import DraftBuilder, MatchPattern
from sounder import play_signal_sound

logger = logging.getLogger("MatchTester")

doc_start_patterns = [
    MatchPattern("rupert start draft", ['rupert', 'draft', 'start']),
    MatchPattern("wake up rupert start draft", ['rupert', 'draft', 'start']),
    MatchPattern("wake up rupert new document", ['rupert', 'document', 'new']),
    MatchPattern("wake up rupert new draft", ['rupert', 'draft', 'new']),
    MatchPattern("rupert wake up new document",['rupert','documentt', 'new']),
    MatchPattern("wake up wake up new draft",['rupert','draft', 'new']),
    ]
doc_end_patterns = [
    MatchPattern("close down rupert end draft",['rupert','draft', 'end']),
    MatchPattern("rupert close draft", ['rupert','draft', 'close']),
    ]

class APIWrapper(ScribeAPIListener):

    def __init__(self):
        super().__init__()
        self.start_time = time.time()
        self.builder = DraftBuilder()
        self.drafts = []
        self.current_draft = None
        self.signal_sounds = False
        self.text_buffer = ""
        for sp in doc_start_patterns:
            self.builder.add_draft_start_pattern(sp)
        for ep in doc_end_patterns:
            self.builder.add_draft_end_pattern(ep)

    async def on_pipeline_ready(self, pipeline):
        if False:
            prompt = ""
            for sp in doc_start_patterns:
                prompt += sp.pattern  + ". "
            for ep in doc_end_patterns:
                prompt += ep.pattern + ". "
        else:
            prompt = "Rupert, new draft, close draft"
            prompt = ""
        pipeline.prime_pump(prompt)
    
    async def on_text_event(self, event: TextEvent):
        et = time.time() - self.start_time
        print(f"-------- Text at {et:7.4f} -------")
        print(f"{et:7.4f}: {event.text}")
        if self.current_draft:
            self.text_buffer += f"{event.text} "
        print(f"--------                   -------")
        if self.current_draft:
            print(f"++++++++ builder working_text +++++++")
            print(f"{self.builder.working_text}")
            print(f"++++++++                      +++++++")

        current_draft,last_draft = await self.builder.new_text(event.text)
        et = time.time() - self.start_time
        print(f"=========== check done at {et:7.4f} ==========")
        change = False
        if last_draft and self.current_draft == last_draft:
            if last_draft.end_text:
                print(f"{et:7.4f}: draft done: {last_draft.end_text.text}")
            else:
                print(f"{et:7.4f}: draft called last but no end_text? {last_draft.start_text.text}")
            self.current_draft = current_draft
            if self.signal_sounds:
                await play_signal_sound('close draft')
            change = True
            print(f"Finalized {last_draft}")
            if self.current_draft:
                self.text_buffer = self.current_draft.start_text.text
        if current_draft and self.current_draft is None:
            print(f"{et:7.4f}: new draft: {current_draft.start_text.text}")
            if self.signal_sounds:
                await play_signal_sound('new draft')
            self.current_draft = current_draft
            change = True
            self.text_buffer = current_draft.start_text.text
        if change:
            print(f"current_draft = {current_draft} last_draft={last_draft}")
            print(f"======  Done at {et:7.4f} =========")
            print(f"++++++++ bnuilder working_text +++++++")
            print(f"{self.builder.working_text}")
            print(f"++++++++                      +++++++")
            

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
    from cmd_tool import logger as dmlogger
    setup_logging(default_level=args.log_level,
                  info_loggers=[logger.name, ],
                  debug_loggers=[dmlogger.name,],
                  more_loggers=[logger, dmlogger])

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
