#!/usr/bin/env python3
"""
Direct CLI interface for async VAD recorder.

This script provides an interactive command-line interface for recording
and transcribing voice using VAD (Voice Activity Detection).

Usage:
    # Record from default microphone
    python scripts/direct_recorder.py

    # Record from specific device
    python scripts/direct_recorder.py --input hw:1,0

    # Process a WAV file (for testing)
    python scripts/direct_recorder.py --input tests/audio_samples/note1.wav
"""

import asyncio
import argparse
import time
from datetime import datetime
from pathlib import Path

from palaver.recorder.async_vad_recorder import (
    AsyncVADRecorder,
    DEVICE,
    AudioEvent,
    RecordingStateChanged,
    VADModeChanged,
    SpeechStarted,
    SpeechEnded,
    TranscriptionQueued,
    TranscriptionComplete,
    NoteCommandDetected,
    NoteTitleCaptured,
)
from palaver.recorder.audio_sources import FileAudioSource
from palaver.config.recorder_config import RecorderConfig
from palaver.mqtt.mqtt_adapter import MQTTAdapter
from palaver.mqtt.client import MQTTPublisher

# Global recording start time (set when recording begins)
RECORDING_START_TIME = None


def format_timestamp_offset(event_timestamp: float) -> str:
    """
    Format event timestamp as offset from recording start.

    Args:
        event_timestamp: Unix timestamp of the event

    Returns:
        Formatted offset string like "+00.123" or "+12.456"
    """
    if RECORDING_START_TIME is None:
        return "+??.???"

    offset = event_timestamp - RECORDING_START_TIME
    return f"+{offset:06.3f}"


async def event_logger(event: AudioEvent):
    """
    Log events with timestamp offsets.

    Args:
        event: AudioEvent instance
    """
    offset = format_timestamp_offset(event.timestamp)
    event_type = type(event).__name__

    # Format event details based on type
    if isinstance(event, RecordingStateChanged):
        status = "STARTED" if event.is_recording else "STOPPED"
        print(f"[{offset}] RecordingStateChanged: {status}")

    elif isinstance(event, VADModeChanged):
        print(f"[{offset}] VADModeChanged: mode={event.mode}, silence={event.min_silence_ms}ms")

    elif isinstance(event, SpeechStarted):
        print(f"[{offset}] SpeechStarted: segment={event.segment_index}, mode={event.vad_mode}")

    elif isinstance(event, SpeechEnded):
        status = "KEPT" if event.kept else "DISCARDED"
        print(f"[{offset}] SpeechEnded: segment={event.segment_index}, duration={event.duration_sec:.2f}s, {status}")

    elif isinstance(event, TranscriptionQueued):
        print(f"[{offset}] TranscriptionQueued: segment={event.segment_index}, duration={event.duration_sec:.2f}s")

    elif isinstance(event, TranscriptionComplete):
        status = "SUCCESS" if event.success else "FAILED"
        text_preview = event.text[:60] + "..." if len(event.text) > 60 else event.text
        print(f"[{offset}] TranscriptionComplete: segment={event.segment_index}, {status}, text=\"{text_preview}\"")

    elif isinstance(event, NoteCommandDetected):
        print(f"[{offset}] NoteCommandDetected: segment={event.segment_index}")

    elif isinstance(event, NoteTitleCaptured):
        print(f"[{offset}] NoteTitleCaptured: segment={event.segment_index}, title=\"{event.title}\"")

    else:
        # Generic format for other event types
        print(f"[{offset}] {event_type}")


async def async_input(prompt: str = "") -> str:
    """
    Non-blocking async input.

    Args:
        prompt: Prompt string to display

    Returns:
        User input string
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, input, prompt)


async def run_interactive_recording(input_source: str = None, auto_start: bool = False) -> Path:
    """
    Run interactive recording session.

    Args:
        input_source: Device name or file path (None = use default device)
        auto_start: If True, start immediately without waiting for Enter

    Returns:
        Path to session directory
    """
    global RECORDING_START_TIME

    # Load configuration from file if exists, otherwise use defaults
    config_path = Path("config.yaml")
    if config_path.exists():
        config = RecorderConfig.from_file(config_path)
        print(f"✓ Loaded configuration from {config_path}")
    else:
        config = RecorderConfig.defaults()
        print("⚠ Using default configuration (config.yaml not found)")

    # Setup MQTT if enabled
    mqtt_adapter = None
    mqtt_client = None
    if config.mqtt_enabled:
        try:
            mqtt_client = MQTTPublisher(
                broker=config.mqtt_broker,
                port=config.mqtt_port,
                qos=config.mqtt_qos
            )
            await mqtt_client.connect()

            # Use timestamp session_id (matches sessions/ directory naming)
            session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
            mqtt_adapter = MQTTAdapter(mqtt_client, session_id)

            print(f"✓ MQTT enabled: publishing to {config.mqtt_broker}:{config.mqtt_port}")
            print(f"  Session ID: {session_id}")
            print(f"  Topic prefix: {config.mqtt_topic_prefix}")
        except Exception as e:
            print(f"⚠ Failed to setup MQTT: {e}")
            print("  Continuing without MQTT...")
            mqtt_adapter = None

    # Create combined event handler (calls both logger and MQTT)
    async def combined_event_handler(event: AudioEvent):
        """Handle events for both logging and MQTT"""
        await event_logger(event)
        if mqtt_adapter:
            await mqtt_adapter.handle_event(event)

    # Create recorder with combined callback and config options
    recorder = AsyncVADRecorder(
        event_callback=combined_event_handler,
        keep_segment_files=config.keep_segment_files
    )

    # Determine input source
    if input_source is None:
        input_source = DEVICE

    # Auto-detect mode
    is_file_mode = input_source.endswith('.wav')

    print(f"\n{'='*70}")
    print("🎙️  VAD Recorder - Async Mode")
    print(f"{'='*70}")
    print(f"Input: {'FILE' if is_file_mode else 'MICROPHONE'}")
    print(f"Source: {input_source}")
    print(f"{'='*70}\n")

    # Wait for user to start (unless auto_start or file mode)
    if not auto_start and not is_file_mode:
        await async_input("Press Enter to start...")

    # Capture recording start time (right before starting)
    RECORDING_START_TIME = time.time()
    print(f"\n📍 Recording start time: {RECORDING_START_TIME:.3f} (unix timestamp)")
    print("   Event timestamps will show as offsets: [+SS.sss]\n")

    # Start recording
    await recorder.start_recording(input_source=input_source)

    if is_file_mode:
        # File mode - automatically process and stop
        print("Processing audio file...")
        await recorder.wait_for_completion()
        print("File processing complete, finalizing...")
        session_dir = await recorder.stop_recording()
    else:
        # Microphone mode - wait for user to stop
        print("\n" + "="*70)
        print("🔴 RECORDING IN PROGRESS")
        print("="*70)
        print("Speak, pause, speak again...")
        print("Press Enter to stop recording")
        print("="*70 + "\n")

        try:
            await async_input()
        except KeyboardInterrupt:
            print("\n[Interrupted by user]")

        print("\nStopping recording...")
        session_dir = await recorder.stop_recording()

    # Cleanup MQTT connection
    if mqtt_client:
        # Small delay to allow final MQTT messages to be sent (especially in file mode)
        await asyncio.sleep(0.5)
        await mqtt_client.disconnect()
        print("✓ MQTT disconnected")

    return session_dir


def main():
    """Main CLI entry point"""
    parser = argparse.ArgumentParser(
        description="VAD-based voice recorder with transcription (async)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Record from microphone (default device)
  python scripts/direct_recorder.py

  # Record from specific device
  python scripts/direct_recorder.py --input hw:1,0

  # Process a WAV file (for testing)
  python scripts/direct_recorder.py --input tests/audio_samples/note1.wav

Features:
  • Voice Activity Detection (VAD) with dynamic silence thresholds
  • Real-time transcription with Whisper
  • "Start new note" command detection
  • Automatic mode switching for extended dictation

Output:
  Recording sessions are saved to sessions/YYYYMMDD_HHMMSS/ with:
    • Individual segment WAV files (seg_NNNN.wav)
    • Real-time transcript (transcript_incremental.txt)
    • Final transcript (transcript_raw.txt)
    • Session metadata (manifest.json)
"""
    )

    parser.add_argument(
        "--input",
        type=str,
        default=None,
        help="Input source: device name (e.g., hw:1,0) or path to WAV file. "
             "Default: uses DEVICE constant from config (hw:1,0)"
    )

    parser.add_argument(
        "--auto",
        action="store_true",
        help="Start recording immediately without waiting for Enter"
    )

    args = parser.parse_args()

    try:
        # Run async recording
        session_dir = asyncio.run(run_interactive_recording(
            input_source=args.input,
            auto_start=args.auto
        ))

        # Success message
        print(f"\n{'='*70}")
        print("✅ Recording Complete")
        print(f"{'='*70}")
        print(f"Session directory: {session_dir}")
        print(f"\nOutput files:")
        print(f"  • transcript_incremental.txt - Real-time transcript")
        print(f"  • transcript_raw.txt - Final transcript")
        print(f"  • manifest.json - Session metadata")
        print(f"  • seg_*.wav - Audio segments")
        print(f"{'='*70}\n")
        print("Transcript:")
        print(f"{'+'*70}\n")
        with open(Path(session_dir, "transcript_raw.txt")) as f:
            print(f.read())
        print(f"{'+'*70}\n")

        return 0

    except KeyboardInterrupt:
        print("\n\n" + "="*70)
        print("⚠️  Interrupted by user")
        print("="*70 + "\n")
        return 0

    except FileNotFoundError as e:
        print(f"\n❌ Error: File not found: {e}")
        return 1

    except Exception as e:
        print(f"\n{'='*70}")
        print("❌ Error")
        print(f"{'='*70}")
        print(f"{e}")
        print(f"{'='*70}\n")

        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
