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

# Add tools to path for wav_utils import
sys.path.insert(0, str(Path(__file__).parent))
from wav_utils import concatenate_wavs


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
        "--length-scale", '1.6',
        "--sentence-silence", "0",  # No automatic silence
        "--output_file", str(output_file)
    ]

    print(f"  Generating: {cmd} {text[:60]}{'...' if len(text) > 60 else ''}")

    result = subprocess.run(
        cmd,
        input=text.encode(),
        capture_output=True
    )

    if result.returncode != 0:
        error_msg = result.stderr.decode() if result.stderr else "Unknown error"
        raise RuntimeError(f"Piper failed: {error_msg}")

    print(f"    → {output_file.name}")


def derive_output_path(json_path: Path) -> Path:
    """
    Derive output WAV filename from JSON filename.

    Args:
        json_path: Input JSON file path

    Returns:
        Output WAV file path in tests/audio_samples/

    Examples:
        specs/test.json → tests/audio_samples/test.wav
        my_test.json → tests/audio_samples/my_test.wav
    """
    stem = json_path.stem  # Filename without extension
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

Examples:
  # Generate from spec
  python tools/generate_from_json.py test_spec.json

  # Custom output location
  python tools/generate_from_json.py test_spec.json --output custom.wav

  # Custom model
  python tools/generate_from_json.py test_spec.json --model models/custom.onnx

  # Keep intermediate files for debugging
  python tools/generate_from_json.py test_spec.json --keep-temp
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
        default="models/en_US-lessac-medium.onnx",
        help="Piper model path (default: models/en_US-lessac-medium.onnx)"
    )

    parser.add_argument(
        "--output",
        type=Path,
        help="Output WAV file path (default: derived from JSON filename)"
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

    args = parser.parse_args()

    try:
        # Load and validate JSON
        print(f"\n{'='*70}")
        print(f"Loading JSON specification: {args.json_file}")
        print(f"{'='*70}\n")

        spec = load_json_spec(args.json_file)
        segments = spec["segments"]

        print(f"✓ Loaded {len(segments)} segments")

        # Check model exists
        model_path = Path(args.model)
        if not model_path.exists():
            print(f"\n⚠ Warning: Model file not found: {model_path}")
            print("  Continuing anyway (piper will fail if model is invalid)")

        # Determine output path
        output_wav = args.output if args.output else derive_output_path(args.json_file)
        print(f"✓ Output will be: {output_wav}")

        # Create temp directory
        temp_dir = args.temp_dir
        temp_dir.mkdir(parents=True, exist_ok=True)
        print(f"✓ Temp directory: {temp_dir}\n")

        # Generate each segment
        print("Generating speech segments:")
        segment_files = []
        silence_durations = []

        for i, segment in enumerate(segments):
            text = segment["text"]
            silence = segment["silence_after"]

            output_file = temp_dir / f"seg_{i:03d}.wav"

            generate_speech_segment(text, output_file, args.model)

            segment_files.append(output_file)
            silence_durations.append(silence)

        print(f"\n✓ Generated {len(segment_files)} segment files")

        # Concatenate with precise silence control
        print(f"\nConcatenating segments with silence control...")
        output_wav.parent.mkdir(parents=True, exist_ok=True)

        concatenate_wavs(
            segment_files,
            output_wav,
            silence_between=silence_durations
        )

        # Cleanup
        if not args.keep_temp:
            print(f"\nCleaning up temporary files...")
            for temp_file in segment_files:
                temp_file.unlink()
            # Remove temp dir if empty
            if temp_dir.exists() and not any(temp_dir.iterdir()):
                temp_dir.rmdir()
            print(f"✓ Removed {len(segment_files)} temporary files")
        else:
            print(f"\n✓ Kept temporary files in: {temp_dir}")

        # Success summary
        print(f"\n{'='*70}")
        print(f"✅ SUCCESS")
        print(f"{'='*70}")
        print(f"Created: {output_wav}")
        print(f"Segments: {len(segments)}")
        print(f"Total duration: ~{sum(silence_durations):.1f}s+ speech time")
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
