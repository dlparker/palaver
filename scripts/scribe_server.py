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
from palaver.scribe.audio_events import AudioEvent, AudioStopEvent
from palaver.scribe.scriven.wire_commands import ScribeCommandEvent, CommandEventListener
from palaver.scribe.api import ScribeAPIListener
from palaver.scribe.recorders.block_audio import BlockAudioRecorder
from palaver.utils.top_error import TopLevelCallback, TopErrorHandler, get_error_handler

# Setup logging
logging.basicConfig(stream=sys.stdout, level=logging.WARNING)
logger = logging.getLogger("ScribeServer")

class APIWrapper(ScribeAPIListener):

    def __init__(self, done_callback):
        super().__init__()
        self.done_callback = done_callback
        self.server = None
        self.server_type = None
        self.full_text = ""
        self.blocks = []
        self.mqtt_publisher = None
        self.block_recorder = None

    async def add_mqtt_publisher(self, args):
        from palaver.scribe.comms.mqtt_publisher import MQTTPublisher
        self.mqtt_publisher = MQTTPublisher(broker=args.mqtt_broker,
                                       port=args.mqtt_port,
                                       base_topic=args.mqtt_topic,
                                       username=args.mqtt_username,
                                       password=args.mqtt_password,
                                       )
        await self.mqtt_publisher.connect()
        logger.info(f"MQTT publishing connected by not yet wrired: {args.mqtt_broker}")


    async def add_recorder(self, args):
        # Setup recording if output_dir provided
        self.block_recorder = BlockAudioRecorder(args.output_dir)
        logger.info(f"Recording enabled but not yet wired: {args.output_dir}")
        
    async def on_pipeline_ready(self, pipeline):
        parts = pipeline.get_pipeline_parts()
        if self.mqtt_publisher:
            parts['audio_merge'].add_event_listener(self.mqtt_publisher)
            parts['transcription'].add_text_event_listener(self.mqtt_publisher)
            parts['command_dispatch'].add_event_listener(self.mqtt_publisher)
            logger.info("MQTT publishing wired")
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
        pprint(event)
        if event.command.starts_text_block:
            self.blocks.append("")
            print("-------------------------------------------")
            print(f"APIWrapper starting block {len(self.blocks)}")
            print("-------------------------------------------")
        elif event.command.ends_text_block:
            print("-------------------------------------------")
            print(f"APIWrapper ending block {len(self.blocks)}")
            print("-------------------------------------------")
            print("++++++=++++++++++++++++++++++++++++++++++++")
            if len(self.blocks) > 0:
                print(self.blocks[-1])
            print("++++++=++++++++++++++++++++++++++++++++++++")
            # give time for block recorder to act
            await asyncio.sleep(0.1)
            if self.block_recorder and False:
                last_record_block = self.block_recorder.get_last_block()
                if last_record_block:
                    print("\n\nRescanning!\n\n")
                    pipeline = self.server.get_pipeline()
                    parts = pipeline.get_pipeline_parts()
                    downsampler = parts['downsampler']
                    transcriber = parts['transcription']
                    async def rescan(block):
                        await transcriber.rescan_direct(block.events, downsampler)
                    get_error_handler().wrap_task(rescan, last_record_block)
                else:
                    print("\n\nCANNOT RESCAN!!! no block\n\n")
                

    async def on_text_event(self, event: TextEvent):
        """Called when new transcribed text is available."""
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
        
    async def on_audio_event(self, event:AudioEvent):
        if isinstance(event, AudioStopEvent):
            logger.info("Got audio stop event %s", event)
            if self.done_callback:
                await self.done_callback(event)
    

def create_parser() -> argparse.ArgumentParser:
    """Create the argument parser for scribe_server."""
    parser = argparse.ArgumentParser(
        description='Scribe Server - Audio transcription with microphone or file playback',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Transcribe from microphone
  %(prog)s mic --model models/ggml-medium.en.bin

  # Transcribe from audio file
  %(prog)s playback --model models/ggml-medium.en.bin audio.wav

  # Multiple files with simulated timing
  %(prog)s playback --model models/ggml-medium.en.bin file1.wav file2.wav
        """
    )

    default_model = Path("models/ggml-medium.en.bin")
    default_model = Path("models/ggml-base.en.bin")
    # Common arguments
    parser.add_argument(
        '--model',
        type=Path,
        default=default_model,
        help=f'Path to Whisper model file (default: {default_model})'
    )

    parser.add_argument(
        '--no-progress',
        action='store_true',
        help='Disable real-time progress printing (print only at end)'
    )

    parser.add_argument(
        '--multiprocess',
        action='store_true',
        help='Use multiprocessing for Whisper (vs threading)'
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
        help='Enable recording and save WAV files to this directory (disabled if not provided)'
    )

    parser.add_argument(
        '--mqtt-broker',
        type=str,
        default=None,
        help='MQTT broker address (enables MQTT publishing if provided)'
    )

    parser.add_argument(
        '--mqtt-port',
        type=int,
        default=1883,
        help='MQTT broker port (default: 1883)'
    )

    parser.add_argument(
        '--mqtt-topic',
        type=str,
        default='palaver/scribe',
        help='MQTT base topic (default: palaver/scribe)'
    )

    parser.add_argument(
        '--mqtt-username',
        type=str,
        default=None,
        help='MQTT username (optional)'
    )

    parser.add_argument(
        '--mqtt-password',
        type=str,
        default=None,
        help='MQTT password (optional)'
    )
    parser.add_argument(
        '--rescan',
        action='store_true',
        help='Rescan a file with best transcription settings (only playback mode)',
    )

    # Subcommands for different modes
    subparsers = parser.add_subparsers(dest='mode', required=True, help='Server mode')

    # Mic mode
    mic_parser = subparsers.add_parser(
        'mic',
        help='Transcribe from microphone'
    )
    mic_parser.add_argument(
        '--chunk-duration',
        type=float,
        default=0.03,
        help='Audio chunk duration in seconds (default: 0.03)'
    )

    # Playback mode
    playback_parser = subparsers.add_parser(
        'playback',
        help='Transcribe from audio file(s)'
    )
    playback_parser.add_argument(
        'files',
        type=Path,
        nargs='+',
        help='Audio file(s) to transcribe'
    )
    playback_parser.add_argument(
        '--chunk-duration',
        type=float,
        default=0.03,
        help='Audio chunk duration in seconds (default: 0.03)'
    )
    playback_parser.add_argument(
        '--no-simulate-timing',
        action='store_true',
        help='Process files as fast as possible (no timing simulation)'
    )

    return parser


async def setup_mic_mode(args, done_callback):
    from palaver.scribe.mic_server import MicServer

    api_wrapper = APIWrapper(done_callback)
    if args.mqtt_broker:
        await api_wrapper.add_mqtt_publisher(args)
    if args.output_dir:
        await api_wrapper.add_recorder(args)
        
    mic_server = MicServer(
        model_path=args.model,
        api_listener=api_wrapper,
        chunk_duration=args.chunk_duration,
        use_multiprocessing=args.multiprocess,
    )
    api_wrapper.set_server(mic_server, "Microphone Listening")
    return api_wrapper


async def setup_playback_mode(args, done_callback):

    from palaver.scribe.playback_server import PlaybackServer
    
    api_wrapper = APIWrapper(done_callback)
    if args.mqtt_broker:
        await api_wrapper.add_mqtt_publisher(args)
    if args.output_dir and not args.rescan_mode:
        await api_wrapper.add_recorder(args)

    playback_server = PlaybackServer(
        model_path=args.model,
        audio_files=args.files,
        api_listener=api_wrapper,
        rescan_mode=args.rescan,
        chunk_duration=args.chunk_duration,
        simulate_timing=not args.no_simulate_timing,
        use_multiprocessing=args.multiprocess,
    )
    api_wrapper.set_server(playback_server, "File playback")
    return api_wrapper
    

def main():
    """Main entry point."""
    parser = create_parser()
    args = parser.parse_args()

    # Set logging level
    logging.getLogger().setLevel(getattr(logging, args.log_level))

    # Validate model path
    if not args.model.exists():
        parser.error(f"Model file does not exist: {args.model}")

    # Mode-specific validation
    if args.mode == 'playback':
        for file_path in args.files:
            if not file_path.exists():
                parser.error(f"Audio file does not exist: {file_path}")


    try:
        api_wrapper = None
        done_noted = False
        async def done_callback(event):
            nonlocal done_noted
            done_noted = done_noted
        async def final_setup():
            nonlocal api_wrapper
            if args.mode == 'mic':
                api_wrapper = await setup_mic_mode(args, done_callback)
            else:
                api_wrapper = await setup_playback_mode(args, done_callback)
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
        top_error_handler.run(final_setup)
        print(f"{api_wrapper.server_type}.run() complete")
    except KeyboardInterrupt:
        print("\nShutdown complete.")
        sys.exit(0)
    except Exception as e:
        logger.error("Fatal error: %s", e, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
