#!/usr/bin/env python3
"""
Example script showing how to create complex test audio files
with precise control over silence duration between segments.

This demonstrates the pattern for creating test files for various
interaction types.
"""

import subprocess
from pathlib import Path
import sys

# Add tools to path for wav_utils import
sys.path.insert(0, str(Path(__file__).parent))
from wav_utils import concatenate_wavs


def generate_speech_segment(text: str, output_file: Path, model: str = "models/en_US-lessac-medium.onnx"):
    """
    Generate a single speech segment using piper.

    Args:
        text: Text to speak
        output_file: Output WAV file path
        model: Piper model to use
    """
    cmd = [
        "uv", "run", "piper",
        "--model", model,
        "--sentence-silence", "0",  # No silence (we'll add it manually)
        "--output_file", str(output_file)
    ]

    print(f"Generating: {text}")
    result = subprocess.run(
        cmd,
        input=text.encode(),
        capture_output=True
    )

    if result.returncode != 0:
        print(f"Error: {result.stderr.decode()}")
        raise RuntimeError(f"Piper failed for: {text}")

    print(f"  → {output_file}")


def example_note_workflow():
    """
    Generate test file for "start new note" workflow.

    Structure:
    - Command segment (+ 1s silence)
    - Title segment (+ 1s silence)
    - Body sentence 1 (+ 1s silence)
    - Body sentence 2 (+ 6s silence to trigger end)
    """
    print("\n" + "="*70)
    print("Generating: Note Workflow Test File")
    print("="*70 + "\n")

    temp_dir = Path("tests/audio_samples/temp")
    temp_dir.mkdir(exist_ok=True)

    segments = [
        ("Clerk, start a new note.", "seg1_command.wav"),
        ("Clerk, This is the title.", "seg2_title.wav"),
        ("Clerk, This is the body, first sentence.", "seg3_body1.wav"),
        ("Stop", "seg4_body2.wav"),
    ]

    # Generate each segment
    segment_files = []
    for text, filename in segments:
        output_path = temp_dir / filename
        generate_speech_segment(text, output_path)
        segment_files.append(output_path)

    # Concatenate with precise silence control
    # 1s after command, title, and first body sentence
    # 6s after final sentence (> 5s threshold)
    silence_durations = [1.0, 1.0, 1.0, 6.0]

    output_file = Path("tests/audio_samples/note1.wav")
    concatenate_wavs(
        segment_files,
        output_file,
        silence_between=silence_durations
    )

    print(f"\n✅ Created: {output_file}")
    print("   Test case: Note workflow with proper silence for mode termination")

    return output_file


def example_multi_note_workflow():
    """
    Generate test file for multiple notes in sequence.

    Structure:
    - Note 1: command + title + body (6s silence)
    - Note 2: command + title + body (6s silence)
    """
    print("\n" + "="*70)
    print("Generating: Multi-Note Workflow Test File")
    print("="*70 + "\n")

    temp_dir = Path("tests/audio_samples/temp")
    temp_dir.mkdir(exist_ok=True)

    segments = [
        # Note 1
        ("Clerk, start a new note.", "note1_command.wav"),
        ("Clerk, First note title.", "note1_title.wav"),
        ("Clerk, First note body.", "note1_body.wav"),

        # Note 2
        ("Clerk, start a new note.", "note2_command.wav"),
        ("Clerk, Second note title.", "note2_title.wav"),
        ("Clerk, Second note body.", "note2_body.wav"),
    ]

    segment_files = []
    for text, filename in segments:
        output_path = temp_dir / filename
        generate_speech_segment(text, output_path)
        segment_files.append(output_path)

    # Silence pattern:
    # - 1s after command (normal)
    # - 1s after title (now in long mode)
    # - 6s after body (triggers note end, back to normal)
    # - 1s after next command
    # - 1s after next title
    # - 6s after final body
    silence_durations = [1.0, 1.0, 6.0, 1.0, 1.0, 6.0]

    output_file = Path("tests/audio_samples/multi_note.wav")
    concatenate_wavs(
        segment_files,
        output_file,
        silence_between=silence_durations
    )

    print(f"\n✅ Created: {output_file}")
    print("   Test case: Multiple notes with mode switching")

    return output_file


def example_custom_interaction():
    """
    Example showing how to create custom interaction test files.

    This pattern can be adapted for:
    - Command-response flows
    - Menu navigation
    - Error handling scenarios
    - Any interaction requiring specific timing
    """
    print("\n" + "="*70)
    print("Example: Custom Interaction Pattern")
    print("="*70 + "\n")

    print("Pattern for creating custom test audio:")
    print("1. Define your interaction segments (what should be spoken)")
    print("2. Generate each segment separately with piper")
    print("3. Use concatenate_wavs() with precise silence list")
    print("4. Silence durations control VAD behavior")
    print("")
    print("Example code:")
    print("  segments = ['Command 1', 'Response 1', 'Command 2', 'Response 2']")
    print("  files = [generate_speech_segment(s, f'seg{i}.wav') for i, s in enumerate(segments)]")
    print("  concatenate_wavs(files, 'test.wav', silence_between=[0.5, 2.0, 0.5, 6.0])")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Generate test audio files")
    parser.add_argument(
        "scenario",
        choices=["note", "multi-note", "custom-example"],
        help="Which test scenario to generate"
    )

    args = parser.parse_args()

    if args.scenario == "note":
        example_note_workflow()
    elif args.scenario == "multi-note":
        example_multi_note_workflow()
    elif args.scenario == "custom-example":
        example_custom_interaction()
