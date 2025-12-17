#!/usr/bin/env python3
import sys
import asyncio
import logging
import traceback
from dataclasses import dataclass, field
from typing import Optional
from pathlib import Path
from pprint import pprint
import sounddevice as sd
import uuid
import argparse

from palaver.scribe.text_events import TextEvent, TextEventListener
from palaver.scribe.audio_events import AudioEvent, AudioStopEvent, AudioStartEvent, AudioChunkEvent
from palaver.scribe.scriven.wire_commands import ScribeCommandEvent, CommandEventListener
from palaver.scribe.api import ScribeAPIListener
from palaver.scribe.api import StartBlockCommand, StopBlockCommand
from palaver.scribe.recorders.block_audio import BlockAudioRecorder
from palaver.utils.top_error import TopLevelCallback, TopErrorHandler, get_error_handler
from palaver.scribe.playback_server import PlaybackServer
from palaver.scribe.loggers import setup_logging

logger = logging.getLogger("ScribePlayback")


@dataclass
class BlockTracker:
    start_event: StartBlockCommand
    text_events: dict[uuid, TextEvent] = field(default_factory=dict[uuid, TextEvent])
    end_event: Optional[StopBlockCommand] = None
    finalized: Optional[bool] = False

class APIWrapper(ScribeAPIListener):

    def __init__(self, block_recorder, play_sound=False):
        super().__init__()
        self.block_recorder = block_recorder
        self.play_sound = play_sound
        self.server = None
        self.server_type = None
        self.full_text = ""
        self.blocks = []
        self.text_events = {}
        self.last_block_name = None
        self.stream = None

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
        await asyncio.sleep(0.1)
        if len(self.blocks) > 0:
            last_block = self.blocks[-1]
            if not last_block.finalized:
                await self.finalize_block(last_block)
        if self.block_recorder:
            await self.block_recorder.stop()
            
    def set_server(self, server, server_type):
        self.server = server
        self.server_type = server_type
        
    async def on_command_event(self, event:ScribeCommandEvent):
        print("")
        if isinstance(event.command, StartBlockCommand):
            #import ipdb; ipdb.set_trace()
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
            print(f"\n\n rescanned file {wavfile}\n\n")
            
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
        elif isinstance(event, AudioStopEvent):
            logger.info("Got audio stop event %s", event)
            if len(self.blocks) > 0:
                last_block = self.blocks[-1]
                if not last_block.finalized:
                    await self.finalize_block(last_block)
        elif isinstance(event, AudioChunkEvent):
            if not self.stream and self.play_sound:
                self.stream = sd.OutputStream(
                    samplerate=event.sample_rate,
                    channels=event.channels,
                    blocksize=event.blocksize,
                    dtype=event.datatype,
                )
                self.stream.start()
                print("Opened stream")
            if self.play_sound:
                audio = event.data
                self.stream.write(audio)
                

def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description='Scribe Server - Audio transcription with microphone or file playback',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    #default_model = Path("models/ggml-base.en.bin")
    #default_model = Path("models/ggml-medium.en.bin")
    default_model = Path("models/multilang_whisper_large3_turbo.ggml")
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
        '-p' , "--play-sound",
        action="store_true",
        help="play sound while transcribing"
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

    # Set logging level
    setup_logging(default_level=args.log_level,
                  info_loggers=[logger.name,],
                  more_loggers=[logger,])

    short_models = [str(Path("models/ggml-base.en.bin").resolve()),
                    str(Path("models/ggml-tiny.en.bin").resolve()),
                    str(Path("models/ggml-medium.en.bin").resolve()),
                    ]
    long_models = [str(Path("models/multilang_whisper_large3_turbo.ggml").resolve())]

    # Validate model path
    if not args.model.exists():
        parser.error(f"Model file does not exist: {args.model}")
    if str(args.model.resolve()) in long_models:
        seconds_per_scan = 10
    else:
        seconds_per_scan = 2
        
    if args.block_files_dir is None:
        parser.error("Must supply block files dir")
        
    block_files_dir = Path(args.block_files_dir)
    if not block_files_dir.exists():
        parser.error(f"Block Files Dir does not exist: {block_files_dir}")

    block_recorder = BlockAudioRecorder(block_files_dir)
    print("*"*80)
    last_block_files = block_recorder.get_last_block_files()
    pprint(last_block_files)
    block_recorder.set_rescan_block(last_block_files)

    
    api_wrapper = APIWrapper(block_recorder, play_sound=args.play_sound)
    try:
        async def main_loop():
            nonlocal api_wrapper
            nonlocal seconds_per_scan
            nonlocal block_recorder

            playback_server = PlaybackServer(
                model_path=args.model,
                audio_file=last_block_files.sound_path,
                api_listener=api_wrapper,
                require_alerts=False,
                seconds_per_scan=seconds_per_scan,
                simulate_timing=False,
                use_multiprocessing=True,
            )
            api_wrapper.set_server(playback_server, "File playback")
            try:
                await api_wrapper.server.run()
                await asyncio.sleep(0.1)
            except:
                logger.error("One:" + traceback.format_exc())
                pipeline = api_wrapper.server.get_pipeline()
                if pipeline:
                    try:
                        await pipeline.shutdown()
                    except:
                        logger.error("Two" + traceback.format_exc())
                        
                raise
            
        background_error_dict = None
        class MyTLC(TopLevelCallback):
            
            async def on_error(self, error_dict: dict):
                nonlocal background_error_dict
                nonlocal api_wrapper
                background_error_dict  = error_dict
                api_wrapper.server.set_background_error(error_dict)
            
        tlc = MyTLC()
        top_error_handler = TopErrorHandler(top_level_callback=tlc, logger=logger)
        top_error_handler.run(main_loop)
        print(f"{api_wrapper.server_type}.run() complete")
    except KeyboardInterrupt:
        print("\nShutdown complete.")
        sys.exit(0)
    except Exception as e:
        logger.error("Fatal error: %s", e, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
