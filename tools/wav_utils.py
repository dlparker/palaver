#!/usr/bin/env python3
"""
tools/wav_utils.py
Utilities for manipulating WAV files for test audio generation

Allows precise control over silence duration for creating test scenarios.
"""

import wave
import numpy as np
from pathlib import Path
from typing import List, Union, Optional


def read_wav(wav_path: Union[str, Path]) -> tuple[np.ndarray, int, int]:
    """
    Read WAV file and return audio data.

    Args:
        wav_path: Path to WAV file

    Returns:
        Tuple of (audio_data, sample_rate, num_channels)
        audio_data is normalized float32 array
    """
    with wave.open(str(wav_path), 'rb') as wf:
        sample_rate = wf.getframerate()
        num_channels = wf.getnchannels()
        sample_width = wf.getsampwidth()

        # Read all frames
        audio_bytes = wf.readframes(wf.getnframes())

        # Convert to numpy array
        if sample_width == 2:  # 16-bit
            audio = np.frombuffer(audio_bytes, dtype=np.int16)
        elif sample_width == 4:  # 32-bit
            audio = np.frombuffer(audio_bytes, dtype=np.int32)
        else:
            raise ValueError(f"Unsupported sample width: {sample_width} bytes")

        # Normalize to float32 [-1, 1]
        if sample_width == 2:
            audio = audio.astype(np.float32) / 32767.0
        else:
            audio = audio.astype(np.float32) / 2147483647.0

        # Reshape for channels
        if num_channels > 1:
            audio = audio.reshape(-1, num_channels)

        return audio, sample_rate, num_channels


def write_wav(wav_path: Union[str, Path], audio: np.ndarray,
              sample_rate: int, num_channels: int = 1):
    """
    Write audio data to WAV file.

    Args:
        wav_path: Output path
        audio: Audio data as float32 array [-1, 1]
        sample_rate: Sample rate in Hz
        num_channels: Number of channels
    """
    # Convert float32 to int16
    audio_int16 = np.int16(audio * 32767.0)

    with wave.open(str(wav_path), 'wb') as wf:
        wf.setnchannels(num_channels)
        wf.setsampwidth(2)  # 16-bit
        wf.setframerate(sample_rate)
        wf.writeframes(audio_int16.tobytes())


def create_silence(duration_sec: float, sample_rate: int,
                   num_channels: int = 1) -> np.ndarray:
    """
    Create silence (zeros) of specified duration.

    Args:
        duration_sec: Duration in seconds
        sample_rate: Sample rate in Hz
        num_channels: Number of channels

    Returns:
        Array of zeros with shape appropriate for num_channels
    """
    num_samples = int(duration_sec * sample_rate)

    if num_channels == 1:
        return np.zeros(num_samples, dtype=np.float32)
    else:
        return np.zeros((num_samples, num_channels), dtype=np.float32)


def append_silence(input_wav: Union[str, Path],
                   output_wav: Union[str, Path],
                   silence_sec: float):
    """
    Append silence to the end of a WAV file.

    Args:
        input_wav: Input WAV file path
        output_wav: Output WAV file path
        silence_sec: Seconds of silence to append
    """
    # Read input
    audio, sample_rate, num_channels = read_wav(input_wav)

    # Create silence
    silence = create_silence(silence_sec, sample_rate, num_channels)

    # Concatenate
    if num_channels == 1:
        combined = np.concatenate([audio, silence])
    else:
        combined = np.vstack([audio, silence])

    # Write output
    write_wav(output_wav, combined, sample_rate, num_channels)

    print(f"✓ Created {output_wav}")
    print(f"  Original: {len(audio) / sample_rate:.2f}s")
    print(f"  + Silence: {silence_sec:.2f}s")
    print(f"  = Total: {len(combined) / sample_rate:.2f}s")


def concatenate_wavs(input_wavs: List[Union[str, Path]],
                     output_wav: Union[str, Path],
                     silence_between: Union[float, List[float]] = 0.0):
    """
    Concatenate multiple WAV files with optional silence between/after them.

    Args:
        input_wavs: List of input WAV file paths
        output_wav: Output WAV file path
        silence_between: Either a single float (same silence everywhere) or
                        a list of floats (silence after each input file).
                        If list, should have len(input_wavs) elements.
                        Last element is silence after the final file.

    Example:
        # Same 1s silence between all files
        concatenate_wavs(["a.wav", "b.wav", "c.wav"], "out.wav", silence_between=1.0)

        # Custom silence: 1s after a, 1s after b, 6s after c
        concatenate_wavs(["a.wav", "b.wav", "c.wav"], "out.wav",
                        silence_between=[1.0, 1.0, 6.0])
    """
    if not input_wavs:
        raise ValueError("No input files provided")

    # Convert silence_between to list
    if isinstance(silence_between, (int, float)):
        silence_list = [silence_between] * len(input_wavs)
    else:
        silence_list = list(silence_between)
        if len(silence_list) != len(input_wavs):
            raise ValueError(f"silence_between list must have {len(input_wavs)} elements")

    # Read first file to get format
    first_audio, sample_rate, num_channels = read_wav(input_wavs[0])

    # Start with first file
    segments = [first_audio]

    # Add silence after first file
    if silence_list[0] > 0:
        segments.append(create_silence(silence_list[0], sample_rate, num_channels))

    # Process remaining files
    for i, wav_path in enumerate(input_wavs[1:], start=1):
        audio, sr, nc = read_wav(wav_path)

        # Verify format matches
        if sr != sample_rate:
            raise ValueError(f"Sample rate mismatch: {wav_path} has {sr}Hz, expected {sample_rate}Hz")
        if nc != num_channels:
            raise ValueError(f"Channel mismatch: {wav_path} has {nc} channels, expected {num_channels}")

        segments.append(audio)

        # Add silence after this file
        if silence_list[i] > 0:
            segments.append(create_silence(silence_list[i], sample_rate, num_channels))

    # Concatenate all segments
    if num_channels == 1:
        combined = np.concatenate(segments)
    else:
        combined = np.vstack(segments)

    # Write output
    write_wav(output_wav, combined, sample_rate, num_channels)

    print(f"✓ Created {output_wav}")
    print(f"  Inputs: {len(input_wavs)} files")
    print(f"  Total duration: {len(combined) / sample_rate:.2f}s")
    for i, (wav_path, silence) in enumerate(zip(input_wavs, silence_list)):
        audio, _, _ = read_wav(wav_path)
        print(f"    {i+1}. {Path(wav_path).name}: {len(audio) / sample_rate:.2f}s + {silence:.2f}s silence")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="WAV file manipulation utilities",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Append 6 seconds of silence to a file
  python wav_utils.py append input.wav output.wav --silence 6.0

  # Concatenate files with 1s between, 6s at end
  python wav_utils.py concat a.wav b.wav c.wav -o output.wav --silence 1.0 1.0 6.0

  # Concatenate with same silence everywhere
  python wav_utils.py concat a.wav b.wav c.wav -o output.wav --silence 1.5
"""
    )

    subparsers = parser.add_subparsers(dest='command', help='Command to run')

    # Append command
    append_parser = subparsers.add_parser('append', help='Append silence to a WAV file')
    append_parser.add_argument('input', type=Path, help='Input WAV file')
    append_parser.add_argument('output', type=Path, help='Output WAV file')
    append_parser.add_argument('--silence', type=float, required=True,
                              help='Seconds of silence to append')

    # Concatenate command
    concat_parser = subparsers.add_parser('concat', help='Concatenate WAV files')
    concat_parser.add_argument('inputs', type=Path, nargs='+', help='Input WAV files')
    concat_parser.add_argument('-o', '--output', type=Path, required=True,
                              help='Output WAV file')
    concat_parser.add_argument('--silence', type=float, nargs='+', required=True,
                              help='Silence duration(s) after each file. '
                                   'Single value applies to all, or provide one per input file.')

    args = parser.parse_args()

    if args.command == 'append':
        append_silence(args.input, args.output, args.silence)

    elif args.command == 'concat':
        concatenate_wavs(args.inputs, args.output, args.silence)

    else:
        parser.print_help()
