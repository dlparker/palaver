#!/usr/bin/env python3
"""
Scribe Server - Unified CLI for audio transcription.

Supports two modes:
  - mic: Real-time transcription from microphone
  - playback: Transcription from audio files
"""
import sys
import asyncio
import logging
import traceback
from pathlib import Path
from pprint import pprint
import argparse

from palaver.scribe.text_events import TextEvent, TextEventListener
from palaver.scribe.audio_events import AudioEvent, AudioStopEvent, AudioStartEvent
from palaver.scribe.scriven.wire_commands import ScribeCommandEvent, CommandEventListener
from palaver.scribe.api import ScribeAPIListener
from palaver.scribe.api import StartNoteCommand, StopNoteCommand, StartRescanCommand
from palaver.scribe.recorders.block_audio import BlockAudioRecorder
from palaver.scribe.mic_server import MicServer
from palaver.utils.top_error import TopLevelCallback, TopErrorHandler, get_error_handler

# Setup logging
log_format = '%(asctime)s - %(name)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s'
logging.basicConfig(stream=sys.stdout, level=logging.WARNING,
                    format=log_format)
logger = logging.getLogger("ScribeServer")

class APIWrapper(ScribeAPIListener):

    def __init__(self):
        super().__init__()
        self.server = None
        self.server_type = None
        self.full_text = ""
        self.blocks = []
        self.mqtt_publisher = None
        self.block_recorder = None
        self.doing_rescan = False
        self.last_block_name = None

    async def add_recorder(self, args):
        # Setup recording if output_dir provided
        self.block_recorder = BlockAudioRecorder(args.output_dir)
        logger.info(f"Recording enabled but not yet wired: {args.output_dir}")
        
    async def on_pipeline_ready(self, pipeline):
        parts = pipeline.get_pipeline_parts()
        if self.mqtt_publisher:
            parts['audio_merge'].add_event_listener(self.mqtt_publisher)
            parts['transcription'].add_text_event_listener(self.mqtt_publisher)
        if self.block_recorder:
            pipeline.add_api_listener(self.block_recorder, to_merge=True)
            logger.info(f"Recording wired")

    async def on_pipeline_shutdown(self):
        if self.mqtt_publisher:
            try:
                await self.mqtt_publisher.disconnect()
            except:
                print(traceback.format_exc())
            finally:
                self.self.mqtt_publisher = None
        if self.block_recorder:
            await self.block_recorder.stop()
            
    def set_server(self, server, server_type):
        self.server = server
        self.server_type = server_type
        
    async def on_command_event(self, event:ScribeCommandEvent):
        print("")
        if isinstance(event.command, StartRescanCommand):
            self.doing_rescan = True
            print("-------------------------------------------")
            print(f"APIWrapper starting rescan")
            print("-------------------------------------------")
            return
        elif isinstance(event.command, StartNoteCommand):
            #import ipdb; ipdb.set_trace()
            self.blocks.append("")
            print("-------------------------------------------")
            print(f"APIWrapper starting block {len(self.blocks)}")
            print("-------------------------------------------")
            await self.handle_text_event(event.text_event)
        elif isinstance(event.command, StopNoteCommand):
            print("-------------------------------------------")
            print(f"APIWrapper ending block {len(self.blocks)}")
            print("-------------------------------------------")
            print("++++++++++++++++++++++++++++++++++++++++++")
            print("     Full block:")
            print("++++++++++++++++++++++++++++++++++++++++++")
            if len(self.blocks) > 0:
                print(self.blocks[-1])
            print("++++++=++++++++++++++++++++++++++++++++++++")
            # give time for block recorder to act
            if self.block_recorder:
                await asyncio.sleep(0.1)
                wavfile = self.block_recorder.get_last_block_wav_path()

    async def handle_text_event(self, event: TextEvent):
        logger.info("*" * 100)
        logger.info("--------Text received---------")

        for seg in event.segments:
            logger.info(seg.text)
            print(seg.text + " ", end="", flush=True)
            self.full_text += seg.text + " "
            if len(self.blocks) > 0:
                self.blocks[-1] += seg.text + " "
        logger.info("--------END Text received---------")
        logger.info("*" * 100)
        
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
    logging.getLogger().setLevel(getattr(logging, args.log_level))

    # Validate model path
    if not args.model.exists():
        parser.error(f"Model file does not exist: {args.model}")

    api_wrapper = APIWrapper()

    mic_server = MicServer(
        model_path=args.model,
        api_listener=api_wrapper,
        use_multiprocessing=True,
    )
    api_wrapper.set_server(mic_server, "Microphone Listening")
    try:
        async def main_task():
            nonlocal api_wrapper
            if args.output_dir:
                await api_wrapper.add_recorder(args)
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
        top_error_handler.run(main_task)
        print(f"{api_wrapper.server_type}.run() complete")
    except KeyboardInterrupt:
        print("\nShutdown complete.")
        sys.exit(0)
    except Exception as e:
        logger.error("Fatal error: %s", e, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
