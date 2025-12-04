#!/usr/bin/env python3
"""
palaver/recorder/vad_recorder.py
Compatibility wrapper for async VAD recorder

This module provides a synchronous wrapper around the async recorder
for backward compatibility with tests and simple scripts.

For new code that needs async/await, use async_vad_recorder.AsyncVADRecorder directly.
For CLI usage, use scripts/direct_recorder.py.
"""

import asyncio
import sys
from pathlib import Path
from typing import Optional

from palaver.recorder.async_vad_recorder import (
    AsyncVADRecorder,
    run_simulated,
    RECORD_SR,
    DEVICE
)
from palaver.recorder.audio_sources import create_audio_source, FileAudioSource


def main(
    input_source: Optional[str] = None,
    mode: str = "auto",
    simulated_segments: Optional[list] = None
) -> Path:
    """
    Synchronous entry point for VAD recorder (for test compatibility).

    Args:
        input_source: Device name (e.g., "hw:1,0") or path to WAV file.
                     If None, uses DEVICE constant.
        mode: "auto" (detect from input), "microphone", "file", or "simulated"
        simulated_segments: For simulated mode: list of (text, duration_sec) tuples

    Returns:
        Path to session directory

    Note:
        This is a blocking synchronous wrapper around the async recorder.
        It uses asyncio.run() internally to execute async code.

        For async usage, use AsyncVADRecorder directly:
            recorder = AsyncVADRecorder()
            await recorder.start_recording()
            session_dir = await recorder.stop_recording()
    """
    # Validate simulated mode
    if mode == "simulated" and simulated_segments is None:
        raise ValueError("simulated_segments required when mode='simulated'")

    # Simulated mode - run async simulated function
    if mode == "simulated":
        return asyncio.run(run_simulated(simulated_segments))

    # Real recording modes - run async recorder
    async def _run_async():
        # Create recorder
        recorder = AsyncVADRecorder()

        # Determine input source
        if input_source is None:
            actual_input = DEVICE
        else:
            actual_input = input_source

        # Detect mode
        if mode == "auto":
            # Auto-detect based on input source
            if actual_input.endswith('.wav'):
                actual_mode = "file"
            else:
                actual_mode = "microphone"
        else:
            actual_mode = mode

        # Start recording
        await recorder.start_recording(input_source=actual_input)

        # Wait for completion (file mode) or return immediately (microphone mode)
        if actual_mode == "file":
            await recorder.wait_for_completion()
            # Auto-stop for file mode
            return await recorder.stop_recording()
        else:
            # Microphone mode - caller must stop (but we can't in sync wrapper)
            # This is a limitation of the sync wrapper
            # For now, just wait indefinitely - caller should use CLI or async API
            print("[Warning: Microphone mode in sync wrapper - limited functionality]")
            print("[Use scripts/direct_recorder.py for full microphone support]")
            # Return recorder for now (not ideal but maintains some compatibility)
            # In practice, tests only use simulated or file mode
            return recorder

    return asyncio.run(_run_async())


if __name__ == "__main__":
    """
    Direct execution is deprecated - use scripts/direct_recorder.py instead.

    This __main__ section is kept for backward compatibility but is not
    the recommended way to run the recorder.
    """
    import argparse

    parser = argparse.ArgumentParser(
        description="VAD-based voice recorder with transcription [DEPRECATED]",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
⚠️  DEPRECATION NOTICE ⚠️
Direct execution of this module is deprecated.
Please use: scripts/direct_recorder.py

Examples:
  # Record from microphone (default device)
  python scripts/direct_recorder.py

  # Record from specific device
  python scripts/direct_recorder.py --input hw:1,0

  # Process a WAV file (for testing)
  python scripts/direct_recorder.py --input tests/audio_samples/note1.wav
"""
    )
    parser.add_argument(
        "--input",
        type=str,
        default=None,
        help="Input source: device name (e.g., hw:1,0) or path to WAV file. "
             "Default: uses DEVICE constant from config"
    )

    args = parser.parse_args()

    try:
        print("\n⚠️  WARNING: Direct execution of vad_recorder.py is deprecated")
        print("⚠️  Please use: scripts/direct_recorder.py\n")
        session_dir = main(input_source=args.input)
        print(f"\nSession complete: {session_dir}")
        sys.exit(0)
    except KeyboardInterrupt:
        print("\n\nInterrupted by user")
        sys.exit(0)
    except Exception as e:
        print(f"\n\nError: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
