"""
palaver/recorder/audio_sources.py
Audio input source abstraction for recorder

Provides uniform interface for audio input from:
- Live microphone (via sounddevice)
- Pre-recorded WAV files (for testing)

Design Notes:
- Both sources call the same callback signature as sounddevice.InputStream
- File source currently supports only WAV files at target sample rate
- File source uses real-time playback simulation for realistic testing
- Format support is intentionally limited: this is primarily for testing/development,
  not production file transcription. Expanding format support would add complexity
  (ffmpeg integration, format detection, etc.) that isn't needed for the core use case.
"""

import wave
import time
import threading
from pathlib import Path
from typing import Callable, Optional
import numpy as np
import sounddevice as sd
from scipy.signal import resample_poly


class AudioSource:
    """Abstract base for audio input sources"""

    def start(self, callback: Callable) -> None:
        """
        Start delivering audio chunks to callback.

        Args:
            callback: Function with signature (indata, frames, time_info, status)
                     matching sounddevice callback convention
        """
        raise NotImplementedError

    def stop(self) -> None:
        """Stop audio delivery and cleanup resources"""
        raise NotImplementedError

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.stop()


class DeviceAudioSource(AudioSource):
    """
    Live microphone input via sounddevice.

    This wraps sd.InputStream and provides the same behavior as the original
    vad_recorder.py implementation.
    """

    def __init__(self, device: str, samplerate: int, channels: int, blocksize: int):
        """
        Args:
            device: ALSA device name (e.g., "hw:1,0")
            samplerate: Sample rate in Hz (e.g., 48000)
            channels: Number of channels (e.g., 2 for stereo)
            blocksize: Samples per chunk (e.g., 1440 for 30ms @ 48kHz)
        """
        self.device = device
        self.samplerate = samplerate
        self.channels = channels
        self.blocksize = blocksize
        self.stream = None

    def start(self, callback: Callable) -> None:
        """Start audio stream from device"""
        self.stream = sd.InputStream(
            samplerate=self.samplerate,
            device=self.device,
            channels=self.channels,
            dtype='float32',
            blocksize=self.blocksize,
            callback=callback,
            latency="low"
        )
        self.stream.start()

    def stop(self) -> None:
        """Stop audio stream"""
        if self.stream:
            self.stream.stop()
            self.stream.close()
            self.stream = None


class FileAudioSource(AudioSource):
    """
    Pre-recorded WAV file input for testing.

    Reads WAV file and delivers chunks via callback in real-time simulation.
    This allows testing the full VAD/transcription pipeline with known audio.

    Format Requirements:
    - Must be WAV format
    - Must match target sample rate (or be close enough for resampling)
    - Can be mono or stereo (mono will be converted to fake stereo)

    Note: Format support is intentionally limited. This is for testing/development,
    not production transcription of arbitrary audio files.
    """

    def __init__(self, wav_path: Path, samplerate: int, blocksize: int):
        """
        Args:
            wav_path: Path to WAV file
            samplerate: Target sample rate (file will be resampled if needed)
            blocksize: Samples per chunk (must match VAD chunk size)
        """
        self.wav_path = Path(wav_path)
        self.target_samplerate = samplerate
        self.blocksize = blocksize
        self.callback = None
        self.thread = None
        self.stop_flag = None

        # Validate file exists
        if not self.wav_path.exists():
            raise FileNotFoundError(f"WAV file not found: {wav_path}")

    def start(self, callback: Callable) -> None:
        """Start feeding audio from file in background thread"""
        self.callback = callback
        self.stop_flag = threading.Event()
        self.thread = threading.Thread(target=self._playback_loop, daemon=True)
        self.thread.start()

    def _playback_loop(self):
        """
        Read WAV file and call callback with chunks.
        Simulates real-time playback for realistic testing.
        """
        try:
            with wave.open(str(self.wav_path), 'rb') as wf:
                file_sr = wf.getframerate()
                file_channels = wf.getnchannels()
                file_sampwidth = wf.getsampwidth()

                print(f"[FileAudioSource] Loaded: {file_sr}Hz, {file_channels}ch, {file_sampwidth*8}bit")

                # Read entire file
                # (For very large files, could read in chunks, but test files are small)
                audio_bytes = wf.readframes(wf.getnframes())

                # Convert to numpy based on sample width
                if file_sampwidth == 2:  # 16-bit
                    audio = np.frombuffer(audio_bytes, dtype=np.int16)
                    audio = audio.astype(np.float32) / 32767.0
                elif file_sampwidth == 4:  # 32-bit
                    audio = np.frombuffer(audio_bytes, dtype=np.int32)
                    audio = audio.astype(np.float32) / 2147483647.0
                else:
                    raise ValueError(f"Unsupported sample width: {file_sampwidth} bytes")

                # Handle channels
                if file_channels == 2:
                    audio = audio.reshape(-1, 2)
                elif file_channels == 1:
                    # Convert mono to stereo (duplicate channel)
                    audio = audio.reshape(-1, 1)
                    audio = np.column_stack([audio, audio])
                else:
                    raise ValueError(f"Unsupported channel count: {file_channels}")

                # Resample if needed
                if file_sr != self.target_samplerate:
                    print(f"[FileAudioSource] Resampling {file_sr}Hz → {self.target_samplerate}Hz")
                    audio = resample_poly(audio, self.target_samplerate, file_sr, axis=0)

                # Calculate chunk timing
                chunk_duration = self.blocksize / self.target_samplerate

                print(f"[FileAudioSource] Playing {len(audio)/self.target_samplerate:.2f}s "
                      f"in {chunk_duration*1000:.1f}ms chunks")

                # Feed chunks in real-time simulation
                chunk_count = 0
                for i in range(0, len(audio), self.blocksize):
                    if self.stop_flag.is_set():
                        print(f"[FileAudioSource] Stopped after {chunk_count} chunks")
                        break

                    chunk = audio[i:i+self.blocksize]

                    # Pad last chunk if needed
                    if len(chunk) < self.blocksize:
                        pad_size = self.blocksize - len(chunk)
                        pad = np.zeros((pad_size, 2), dtype=np.float32)
                        chunk = np.vstack([chunk, pad])

                    # Call the callback (same signature as sounddevice)
                    # callback(indata, frames, time_info, status)
                    self.callback(chunk, self.blocksize, None, None)

                    chunk_count += 1

                    # Sleep to simulate real-time playback
                    time.sleep(chunk_duration)

                print(f"[FileAudioSource] Playback complete: {chunk_count} chunks")

        except Exception as e:
            print(f"[FileAudioSource] Error during playback: {e}")
            raise

    def stop(self) -> None:
        """Stop file playback"""
        if self.stop_flag:
            self.stop_flag.set()
        if self.thread:
            self.thread.join(timeout=2.0)
            self.thread = None

    def is_finished(self) -> bool:
        """Check if playback has completed"""
        return self.thread is None or not self.thread.is_alive()

    def wait_for_completion(self, timeout: Optional[float] = None):
        """
        Wait for playback to complete.

        Args:
            timeout: Maximum time to wait in seconds (None = wait forever)
        """
        if self.thread:
            self.thread.join(timeout=timeout)


class SimulatedAudioSource(AudioSource):
    """
    Simulated audio source for fast testing without real audio/VAD.

    This is a minimal placeholder - in simulated mode, we bypass VAD entirely
    and directly create segments with pre-defined transcriptions.

    The main recorder loop will handle simulated mode differently, so this
    class just provides the AudioSource interface for consistency.
    """

    def __init__(self, segment_count: int = 3, realtime: bool = False):
        """
        Args:
            segment_count: Number of segments to simulate
            realtime: If True, simulate timing delays; if False, run immediately
        """
        self.segment_count = segment_count
        self.realtime = realtime
        self.running = False

    def start(self, callback: Callable) -> None:
        """Start simulated audio (no-op)"""
        self.running = True
        print(f"[SimulatedAudioSource] Started (simulated mode)")

    def stop(self) -> None:
        """Stop simulated audio"""
        self.running = False

    def is_finished(self) -> bool:
        """Check if simulation has completed"""
        return not self.running

    def wait_for_completion(self, timeout: Optional[float] = None):
        """Wait for simulation to complete (immediate in simulated mode)"""
        pass


def create_audio_source(input_spec: str, samplerate: int, blocksize: int,
                       channels: int = 2) -> AudioSource:
    """
    Factory function to create appropriate audio source.

    Args:
        input_spec: Either device name (e.g., "hw:1,0") or path to WAV file
        samplerate: Target sample rate
        blocksize: Samples per chunk
        channels: Number of channels for device input (file input always produces stereo)

    Returns:
        AudioSource instance (DeviceAudioSource or FileAudioSource)
    """
    # Detect device vs file
    if input_spec.startswith("hw:") or input_spec.startswith("default") or input_spec.startswith("plughw:"):
        return DeviceAudioSource(
            device=input_spec,
            samplerate=samplerate,
            channels=channels,
            blocksize=blocksize
        )
    else:
        return FileAudioSource(
            wav_path=Path(input_spec),
            samplerate=samplerate,
            blocksize=blocksize
        )
