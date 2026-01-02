#!/usr/bin/env python3
from pathlib import Path
import argparse
import asyncio
import logging

import uvicorn

from palaver.fastapi.event_server import EventNetServer, ServerMode
from palaver.scribe.core import PipelineConfig
from palaver.scribe.recorders.sql_drafts import SQLDraftRecorder
from palaver.scribe.audio.mic_listener import MicListener
from palaver.scribe.audio.net_listener import NetListener
from palaver.scribe.api import ScribeAPIListener

from loggers import setup_logging

logger = logging.getLogger('VTTServer')


async def main():
    parser = argparse.ArgumentParser(
        description="VTT Server",
    )

    default_model = Path("models/ggml-base.en.bin")
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

    default_store = Path("vtt_results")
    parser.add_argument(
        '--output-dir',
        type=Path,
        default=default_store,
        help='Location for output storage'
    )

    parser.add_argument(
        '--host',
        type=str,
        default='0.0.0.0',
        help='Host to bind server (default: 0.0.0.0)'
    )

    parser.add_argument(
        '--port',
        type=int,
        default=8000,
        help='Port to bind server (default: 8000)'
    )

    parser.add_argument(
        '--audio-url',
        type=str,
        default=None,
        help='URL for audio event source instead of microphone'
    )
    parser.add_argument(
        '--rescan',
        type=str,
        default=None,
        help='Listen for drafts from uri and use multilang_whisper_large3_turbo.ggml and large window to rescan'
    )

    args = parser.parse_args()

    # Set logging level
    setup_logging(
        default_level=args.log_level,
        info_loggers=[],
        debug_loggers=['VTTServer', 'EventRouter', 'Rescanner', 'DraftRouter', 'DraftMaker',],
    )

    draft_recorder = SQLDraftRecorder(args.output_dir)
    model = args.model
    if args.audio_url:
        audio_listener = NetListener(args.audio_url, chunk_duration=0.03)
        mode = ServerMode.remote
    else:
        audio_listener = MicListener(chunk_duration=0.03)
        mode = ServerMode.direct
    if args.rescan:
        mode = ServerMode.rescan
        if model == default_model:
            model = Path("models/multilang_whisper_large3_turbo.ggml")
        else:
            print("Warning, you specified --rescan but specified a non-default model, using your selection")
        audio_listener = NetListener(args.rescan, audio_only=False, chunk_duration=0.03)
    
    pipeline_config = PipelineConfig(
        model_path=model,
        api_listener=None, 
        target_samplerate=16000,
        target_channels=1,
        use_multiprocessing=True,
    )
    if args.rescan:
        pipeline_config.vad_silence_ms = 3000
        pipeline_config.vad_speech_pad_ms = 1000
        pipeline_config.seconds_per_scan = 15

    server = EventNetServer(audio_listener,
                            pipeline_config = pipeline_config,
                            draft_recorder=draft_recorder,
                            port=args.port,
                            mode=mode)
    logger.info(f"Starting server on {args.host}:{args.port}")


    config = uvicorn.Config(
        server.app,
        host=args.host,
        port=args.port,
        log_level=args.log_level.lower()
    )
    
    server = uvicorn.Server(config)
    await server.serve()


if __name__ == "__main__":
    asyncio.run(main())
