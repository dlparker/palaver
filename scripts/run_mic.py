#!/usr/bin/env python3
import sys
import asyncio
import logging
import traceback
from dataclasses import dataclass, field
import uuid
from typing import Optional
from pathlib import Path
from pprint import pprint
import argparse

from palaver.scribe.text_events import TextEvent, TextEventListener
from palaver.scribe.audio_events import AudioEvent, AudioStopEvent, AudioStartEvent
from palaver.scribe.scriven.wire_commands import ScribeCommandEvent, CommandEventListener
from palaver.scribe.api import ScribeAPIListener
from palaver.scribe.api import StartBlockCommand, StopBlockCommand
from palaver.scribe.recorders.block_audio import BlockAudioRecorder
from palaver.scribe.listener.mic_listener import MicListener
from palaver.scribe.core import ScribePipeline, PipelineConfig
from palaver.utils.top_error import TopLevelCallback, TopErrorHandler, get_error_handler
from palaver.scribe.loggers import setup_logging

logger = logging.getLogger("ScribeMicRunner")

@dataclass
class BlockTracker:
    start_event: StartBlockCommand
    text_events: dict[uuid, TextEvent] = field(default_factory=dict[uuid, TextEvent])
    end_event: Optional[StopBlockCommand] = None
    finalized: Optional[bool] = False

class APIWrapper(ScribeAPIListener):

    def __init__(self):
        super().__init__()
        self.full_text = ""
        self.blocks = []
        self.text_events = {}
        self.block_recorder = None
        self.last_block_name = None

    async def add_recorder(self, args):
        # Setup recording if output_dir provided
        self.block_recorder = BlockAudioRecorder(args.output_dir)
        logger.info(f"Recording enabled but not yet wired: {args.output_dir}")
        
    async def on_pipeline_ready(self, pipeline):
        parts = pipeline.get_pipeline_parts()
        if self.block_recorder:
            pipeline.add_api_listener(self.block_recorder, to_merge=True)
            logger.info(f"Recording wired")

    async def on_pipeline_shutdown(self):
        if len(self.blocks) > 0:
            last_block = self.blocks[-1]
            if not last_block.finalized:
                await self.finalize_block(last_block)
        if self.block_recorder:
            await self.block_recorder.stop()

    async def on_command_event(self, event:ScribeCommandEvent):
        print("")
        if isinstance(event.command, StartBlockCommand):
            self.blocks.append(BlockTracker(start_event=event))
            print("-------------------------------------------")
            print(f"APIWrapper starting block {len(self.blocks)}")
            print("-------------------------------------------")
            await self.handle_text_event(event.text_event)
        elif isinstance(event.command, StopBlockCommand):
            if len(self.blocks) > 0:
                last_block = self.blocks[-1]
                if not last_block.finalized:
                    last_block.end_event = event
                    await self.finalize_block(last_block)

    async def finalize_block(self, block):
        print("-------------------------------------------")
        print(f"APIWrapper ending block {len(self.blocks)}")
        print("-------------------------------------------")
        print("++++++++++++++++++++++++++++++++++++++++++")
        print("     Full block:")
        print("++++++++++++++++++++++++++++++++++++++++++")
        for uuid,text_event in block.text_events.items():
            for seg in text_event.segments:
                print(seg.text)
        print("++++++=++++++++++++++++++++++++++++++++++++")
        block.finalized = True
        # give time for block recorder to act
        if self.block_recorder:
            await asyncio.sleep(0.05)
            wavfile = self.block_recorder.get_last_block_wav_path()
            print(f"\n\n wrote file {wavfile}\n\n")
                        
    async def handle_text_event(self, event: TextEvent):
        if event.event_id in self.text_events:
            return
        self.text_events[event.event_id] = event
        if not last_block.finalized:
            last_block.text_events[event.event_id] = event
            logger.info(f"text {event.event_id} added to block")
            for seg in event.segments:
                if logger.isEnabledFor(logging.INFO):
                    logger.info("-----Adding text to block-----\n%s", seg.text)
                else:
                    logger.info("-----Adding text to block-----\n")
                    print(seg.text)
                    logger.info("----------\n")
            self.full_text += seg.text + " "
        else:
            print(f"ignoring text {event.segments}")
        
    async def handle_text_event(self, event: TextEvent):
        if event.event_id == self.text_events:
            return
        self.text_events[event.event_id] = event
        if len(self.blocks) > 0:
            last_block = self.blocks[-1]
            if not last_block.finalized:
                last_block.text_events[event.event_id] = event
                logger.info(f"text {event.event_id} added to block")
                for seg in event.segments:
                    if logger.isEnabledFor(logging.INFO):
                        logger.info("-----Adding text to block-----\n%s", seg.text)
                    else:
                        logger.info("-----Adding text to block-----\n")
                        print(seg.text)
                        logger.info("----------\n")
                self.full_text += seg.text + " "
            else:
                print(f"ignoring text {event.segments}")
                
    async def on_text_event(self, event: TextEvent):
        """Called when new transcribed text is available."""
        await self.handle_text_event(event)
        
    async def on_audio_event(self, event:AudioEvent):
        if isinstance(event, AudioStartEvent):
            #import ipdb; ipdb.set_trace()
            pass
        if isinstance(event, AudioStopEvent):
            logger.info("Got audio stop event %s", event)
    

def create_parser() -> argparse.ArgumentParser:
    """Create the argument parser for scribe_server."""
    parser = argparse.ArgumentParser(
        description='Scribe Server - Audio transcription with microphone or file playback',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        )
    default_model = Path("models/ggml-base.en.bin")
    # Common arguments
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
    info_loggers = [logger.name,]
    
    setup_logging(default_level=args.log_level,
                  info_loggers=[logger.name,'Commands'],
                  debug_loggers=[],
                  more_loggers=[logger,])

    # Validate model path
    if not args.model.exists():
        parser.error(f"Model file does not exist: {args.model}")

    api_wrapper = APIWrapper()

    try:
        async def main_task():
            nonlocal api_wrapper
            if args.output_dir:
                await api_wrapper.add_recorder(args)

            # Create listener directly
            mic_listener = MicListener(chunk_duration=0.03)

            # Create pipeline config
            config = PipelineConfig(
                model_path=args.model,
                api_listener=api_wrapper,
                target_samplerate=16000,
                target_channels=1,
                use_multiprocessing=True,
            )

            # Manage context and lifecycle
            async with mic_listener:
                async with ScribePipeline(mic_listener, config) as pipeline:
                    await pipeline.start_listener()
                    try:
                        await pipeline.run_until_error_or_interrupt()
                    except (KeyboardInterrupt, asyncio.CancelledError):
                        print("\nControl-C detected. Shutting down...")

        background_error_dict = None
        class MyTLC(TopLevelCallback):

            async def on_error(self, error_dict: dict):
                nonlocal background_error_dict
                background_error_dict = error_dict

        tlc = MyTLC()
        top_error_handler = TopErrorHandler(top_level_callback=tlc, logger=logger)
        top_error_handler.run(main_task)
        print("Microphone Listening complete")
    except KeyboardInterrupt:
        print("\nShutdown complete.")
        sys.exit(0)
    except Exception as e:
        logger.error("Fatal error: %s", e, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
