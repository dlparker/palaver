"""
Configuration system for Palaver recorder.

Loads from YAML file or provides sensible defaults.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional
import yaml


@dataclass
class RecorderConfig:
    """
    Global configuration for recorder behavior.

    All timing values in seconds unless otherwise noted.
    SpeechBucket parameters are relative multipliers of these base values.
    """

    # Attention phrase (VAD start delay workaround)
    attention_phrase: str = "clerk"
    attention_phrase_threshold: float = 80.0  # rapidfuzz similarity %

    # Base timing values (SpeechBucket multiplies these)
    base_segment_size: float = 5.0        # Target chunk duration for real-time feedback
    base_start_window: float = 2.0        # How long to wait for bucket to start
    base_termination_silence: float = 0.8 # Silence duration to end bucket

    # VAD settings
    vad_threshold_normal: float = 0.5
    vad_threshold_long: float = 0.7
    min_segment_duration: float = 1.2

    # Transcription
    whisper_model: str = "models/multilang_whisper_large3_turbo.ggml"
    num_workers: int = 2
    whisper_timeout: int = 60

    # Fuzzy matching
    command_phrase_threshold: float = 80.0  # rapidfuzz similarity %

    # MQTT Configuration (local broker, no retention, QoS 1)
    mqtt_enabled: bool = False
    mqtt_broker: str = "localhost"
    mqtt_port: int = 1883
    mqtt_qos: int = 1  # At least once delivery
    mqtt_topic_prefix: str = "palaver"

    # Debug / Storage
    keep_segment_files: bool = False  # Keep segment WAV files after CommandDoc completion (for debugging)

    @classmethod
    def from_file(cls, path: Path) -> 'RecorderConfig':
        """
        Load configuration from YAML file.

        Args:
            path: Path to YAML config file

        Returns:
            RecorderConfig instance

        Raises:
            FileNotFoundError: If config file doesn't exist
            ValueError: If config file is invalid
        """
        if not path.exists():
            raise FileNotFoundError(f"Config file not found: {path}")

        with open(path) as f:
            data = yaml.safe_load(f)

        if not isinstance(data, dict):
            raise ValueError(f"Invalid config file format: {path}")

        return cls(**data)

    @classmethod
    def defaults(cls) -> 'RecorderConfig':
        """
        Get default configuration.

        Returns:
            RecorderConfig with default values
        """
        return cls()

    def validate(self):
        """
        Validate configuration values.

        Raises:
            ValueError: If any values are invalid
        """
        if self.base_segment_size <= 0:
            raise ValueError("base_segment_size must be positive")

        if self.base_start_window <= 0:
            raise ValueError("base_start_window must be positive")

        if self.base_termination_silence <= 0:
            raise ValueError("base_termination_silence must be positive")

        if not (0.0 <= self.vad_threshold_normal <= 1.0):
            raise ValueError("vad_threshold_normal must be between 0 and 1")

        if not (0.0 <= self.vad_threshold_long <= 1.0):
            raise ValueError("vad_threshold_long must be between 0 and 1")

        if self.min_segment_duration <= 0:
            raise ValueError("min_segment_duration must be positive")

        if self.num_workers < 1:
            raise ValueError("num_workers must be at least 1")

        if self.whisper_timeout <= 0:
            raise ValueError("whisper_timeout must be positive")

        if not (0.0 <= self.attention_phrase_threshold <= 100.0):
            raise ValueError("attention_phrase_threshold must be between 0 and 100")

        if not (0.0 <= self.command_phrase_threshold <= 100.0):
            raise ValueError("command_phrase_threshold must be between 0 and 100")
