#!/usr/bin/env python3
"""
tools/generate_from_json.py
Generate test audio files from JSON specifications

This tool reads a JSON file containing speech segments and silence durations,
generates speech using piper, and concatenates them with precise timing control.

JSON Format:
{
  "segments": [
    {"text": "Clerk, start a new note", "silence_after": 1.0},
    {"text": "This is the title", "silence_after": 1.0},
    {"text": "This is the body", "silence_after": 1.0}
    {"text": "Break, Break, Break", "silence_after": 6.0}
  ]
}

Usage:
    python tools/generate_from_json.py test_spec.json
    python tools/generate_from_json.py test_spec.json --output custom_output.wav
    python tools/generate_from_json.py test_spec.json --model models/custom.onnx
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import List, Dict
import soundfile as sf
import sounddevice as sd

# Add tools to path for wav_utils import
sys.path.insert(0, str(Path(__file__).parent))
from wav_utils import concatenate_wavs

# Default voice names (short form)
DEFAULT_VOICES = ["lessac", "amy", "joe", "bryce", "kristin", "ryan"]
length_by_model = {
    "models/en_US-lessac-medium.onnx": 1.5,
    "models/en_US-amy-medium.onnx": 1.2,
    "models/en_US-joe-medium.onnx": 1.0,
    "models/en_US-bryce-medium.onnx": 0.9,
    "models/en_US-kristin-medium.onnx": 1.0,
    "models/en_US-ryan-medium.onnx": 1.4 ,
}


def expand_voice_name(name: str) -> Path:
    """
    Expand a short voice name to full model path.

    Args:
        name: Either a short name like "joe" or a full path

    Returns:
        Path to model file

    Examples:
        "joe" → Path("models/en_US-joe-medium.onnx")
        "models/custom.onnx" → Path("models/custom.onnx")
    """
    # If it looks like a path (has slashes or .onnx), use as-is
    if "/" in name or "\\" in name or name.endswith(".onnx"):
        return Path(name)

    # Otherwise expand short name to standard pattern
    return Path(f"models/en_US-{name}-medium.onnx")


def load_json_spec(json_path: Path) -> Dict:
    """
    Load and validate JSON specification.

    Args:
        json_path: Path to JSON file

    Returns:
        Parsed JSON data

    Raises:
        ValueError: If JSON is invalid or missing required fields
        FileNotFoundError: If JSON file doesn't exist
    """
    if not json_path.exists():
        raise FileNotFoundError(f"JSON file not found: {json_path}")

    with open(json_path) as f:
        data = json.load(f)

    # Validate structure
    if "segments" not in data:
        raise ValueError("JSON must contain 'segments' key")

    if not isinstance(data["segments"], list):
        raise ValueError("'segments' must be a list")

    if not data["segments"]:
        raise ValueError("'segments' list cannot be empty")

    # Validate each segment
    for i, segment in enumerate(data["segments"]):
        if not isinstance(segment, dict):
            raise ValueError(f"Segment {i} must be a dictionary")

        if "text" not in segment:
            raise ValueError(f"Segment {i} missing required 'text' field")

        if "silence_after" not in segment:
            raise ValueError(f"Segment {i} missing required 'silence_after' field")

        if not isinstance(segment["text"], str):
            raise ValueError(f"Segment {i} 'text' must be a string")

        if not isinstance(segment["silence_after"], (int, float)):
            raise ValueError(f"Segment {i} 'silence_after' must be a number")

        if segment["silence_after"] < 0:
            raise ValueError(f"Segment {i} 'silence_after' cannot be negative")

    return data


def play_audio_file(file_path: Path) -> None:
    """
    Play an audio file through the speakers.

    Args:
        file_path: Path to WAV file to play

    Raises:
        RuntimeError: If playback fails
    """
    try:
        sound_file = sf.SoundFile(str(file_path))
        sr = sound_file.samplerate
        channels = sound_file.channels
        chunk_duration = 0.03
        frames_per_chunk = max(1, int(round(chunk_duration * sr)))

        out_stream = sd.OutputStream(
            samplerate=sr,
            channels=channels,
            blocksize=frames_per_chunk,
            dtype="float32",
        )
        out_stream.start()

        while True:
            data = sound_file.read(frames=frames_per_chunk, dtype="float32", always_2d=True)
            if data.shape[0] == 0:
                break
            out_stream.write(data)

        out_stream.close()
        sound_file.close()
    except Exception as e:
        raise RuntimeError(f"Audio playback failed: {e}")


def generate_speech_segment(text: str, output_file: Path, model: str) -> None:
    """
    Generate a single speech segment using piper.

    Args:
        text: Text to speak
        output_file: Output WAV file path
        model: Piper model to use

    Raises:
        RuntimeError: If piper fails
    """
    cmd = [
        "uv", "run", "piper",
        "--model", model,
        "--length-scale", f'{length_by_model.get(model, 1.5)}',
        "--sentence-silence", "0",  # No automatic silence
        "--output_file", str(output_file)
    ]


    print(f"  Generating: {text[:60]}{'...' if len(text) > 60 else ''}")

    result = subprocess.run(
        cmd,
        input=text.encode(),
        capture_output=True
    )

    if result.returncode != 0:
        error_msg = result.stderr.decode() if result.stderr else "Unknown error"
        raise RuntimeError(f"Piper failed: {error_msg}")

    print(f"    → {output_file.name}")


def derive_output_path(json_path: Path, voice_name: str = "") -> Path:
    """
    Derive output WAV filename from JSON filename, optionally with voice suffix.

    Args:
        json_path: Input JSON file path
        voice_name: Optional voice stem to append

    Returns:
        Output WAV file path in tests/audio_samples/

    Examples:
        specs/test.json → tests/audio_samples/test.wav
        specs/test.json + "en_US-amy-medium" → tests/audio_samples/test_en_US-amy-medium.wav
    """
    stem = json_path.stem  # Filename without extension
    if voice_name:
        stem = f"{stem}_{voice_name}"
    return Path("tests/audio_samples") / f"{stem}.wav"


def main():
    parser = argparse.ArgumentParser(
        description="Generate test audio from JSON specification",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
JSON Format:
  {
    "segments": [
      {"text": "First thing to say", "silence_after": 1.0},
      {"text": "Second thing to say", "silence_after": 6.0}
    ]
  }

Available Voice Short Names:
  lessac, amy, joe, bryce, kristin, ryan
  (These expand to models/en_US-{name}-medium.onnx)

Examples:
  # Generate from spec with default voices
  python tools/generate_from_json.py test_spec.json

  # Use short voice names (expands to models/en_US-{name}-medium.onnx)
  python tools/generate_from_json.py --voices joe amy -- test_spec.json

  # Single voice with short name
  python tools/generate_from_json.py --model bryce test_spec.json

  # Full model paths still work
  python tools/generate_from_json.py --voices models/custom.onnx -- test_spec.json

  # Custom output location
  python tools/generate_from_json.py test_spec.json --output custom.wav

  # Keep intermediate files for debugging
  python tools/generate_from_json.py test_spec.json --keep-temp

  # Play segments through speakers as they're generated
  python tools/generate_from_json.py --voices joe -- test_spec.json --play
"""
    )

    parser.add_argument(
        "json_file",
        type=Path,
        help="Path to JSON specification file"
    )

    parser.add_argument(
        "--model",
        type=str,
        help="Single Piper model to use (overrides --voices)"
    )

    parser.add_argument(
        "--voices",
        nargs="+",
        default=DEFAULT_VOICES,
        help="List of voice names or model paths. Short names like 'joe' or 'amy' expand to 'models/en_US-{name}-medium.onnx'. Use '--' before json_file if needed. Default: lessac, amy, joe, bryce, kristin, ryan."
    )

    parser.add_argument(
        "--output",
        type=Path,
        help="Base output WAV file path (default: derived from JSON filename). If multiple voices, suffixes are added."
    )

    parser.add_argument(
        "--output-single",
        type=Path,
        help="Output a single WAV file path, voice spec required",
    )

    parser.add_argument(
        "--temp-dir",
        type=Path,
        default=Path("tests/audio_samples/temp"),
        help="Temporary directory for intermediate files (default: tests/audio_samples/temp)"
    )

    parser.add_argument(
        "--keep-temp",
        action="store_true",
        help="Keep intermediate files (for debugging)"
    )

    parser.add_argument(
        "--play",
        action="store_true",
        help="Play each segment through speakers as it's generated"
    )

    single_spec = None
    args = parser.parse_args()
    if args.output_single:
        if args.voices is None or len(args.voices) != 1:
            parser.error("You must specify a single voice for single file output")
        single_spec = (args.output_single, expand_voice_name(args.voices[0]))
    try:
        # Load and validate JSON
        print(f"\n{'='*70}")
        print(f"Loading JSON specification: {args.json_file}")
        print(f"{'='*70}\n")

        spec = load_json_spec(args.json_file)
        segments = spec["segments"]

        print(f"✓ Loaded {len(segments)} segments\n")

        # Determine models to use
        if single_spec:
            models_to_use = [single_spec[1]]
        elif args.model:
            models_to_use = [expand_voice_name(args.model)]
        else:
            models_to_use = [expand_voice_name(v) for v in args.voices]

        print(f"Using {len(models_to_use)} voice model(s):")
        for model in models_to_use:
            print(f"  - {model}")
        print()

        # Create temp directory
        temp_dir = args.temp_dir
        temp_dir.mkdir(parents=True, exist_ok=True)
        print(f"✓ Temp directory: {temp_dir}\n")

        # Loop over each model
        for model_path in models_to_use:
            voice_name = model_path.stem  # e.g., "en_US-amy-medium"
            print(f"{'-'*70}")
            print(f"Processing voice: {voice_name}")
            print(f"{'-'*70}\n")

            # Check model exists
            if not model_path.exists():
                print(f"⚠ Warning: Model file not found: {model_path}")
                print("  Skipping this voice.")
                continue

            # Derive output path with voice suffix (unless single model and custom output)
            if single_spec is not None:
                output_wav = single_spec[0]
            elif args.output:
                output_wav = args.output.with_stem(f"{args.output.stem}_{voice_name}" if len(models_to_use) > 1 else args.output.stem)
            else:
                output_wav = derive_output_path(args.json_file, voice_name)

            print(f"✓ Output for this voice: {output_wav}\n")

            # Generate each segment for this voice
            print("Generating speech segments:")
            segment_files = []
            silence_durations = []

            for i, segment in enumerate(segments):
                text = segment["text"]
                silence = segment["silence_after"]

                output_file = temp_dir / f"{voice_name}_seg_{i:03d}.wav"

                generate_speech_segment(text, output_file, str(model_path))

                # Play segment if requested
                if args.play:
                    print(f"    ♪ Playing segment...")
                    play_audio_file(output_file)

                segment_files.append(output_file)
                silence_durations.append(silence)

            print(f"\n✓ Generated {len(segment_files)} segment files for {voice_name}")

            # Concatenate with precise silence control
            print(f"\nConcatenating segments with silence control...")
            output_wav.parent.mkdir(parents=True, exist_ok=True)

            concatenate_wavs(
                segment_files,
                output_wav,
                silence_between=silence_durations
            )

            # Cleanup for this voice
            if not args.keep_temp:
                print(f"\nCleaning up temporary files for {voice_name}...")
                for temp_file in segment_files:
                    temp_file.unlink()
                print(f"✓ Removed {len(segment_files)} temporary files")
            else:
                print(f"\n✓ Kept temporary files in: {temp_dir}")

        # Final summary
        print(f"\n{'='*70}")
        print(f"✅ SUCCESS - Processed {len(models_to_use)} voices")
        print(f"{'='*70}")
        print(f"Segments per file: {len(segments)}")
        print(f"Total silence per file: ~{sum(s['silence_after'] for s in segments):.1f}s + speech time")
        print()

    except FileNotFoundError as e:
        print(f"\n❌ Error: {e}", file=sys.stderr)
        sys.exit(1)
    except ValueError as e:
        print(f"\n❌ Invalid JSON: {e}", file=sys.stderr)
        sys.exit(1)
    except RuntimeError as e:
        print(f"\n❌ Generation failed: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
    
