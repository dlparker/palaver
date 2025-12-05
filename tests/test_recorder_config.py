"""Tests for RecorderConfig."""

import pytest
from pathlib import Path
from palaver.config import RecorderConfig


def test_defaults():
    """Test default configuration values."""
    config = RecorderConfig.defaults()

    assert config.attention_phrase == "clerk"
    assert config.attention_phrase_threshold == 80.0
    assert config.base_segment_size == 5.0
    assert config.base_start_window == 2.0
    assert config.base_termination_silence == 0.8
    assert config.vad_threshold_normal == 0.5
    assert config.vad_threshold_long == 0.7
    assert config.min_segment_duration == 1.2
    assert config.num_workers == 2
    assert config.whisper_timeout == 60
    assert config.command_phrase_threshold == 80.0


def test_validate_valid_config():
    """Test validation of valid configuration."""
    config = RecorderConfig.defaults()
    config.validate()  # Should not raise


def test_validate_invalid_segment_size():
    """Test validation catches invalid base_segment_size."""
    config = RecorderConfig.defaults()
    config.base_segment_size = -1.0

    with pytest.raises(ValueError, match="base_segment_size must be positive"):
        config.validate()


def test_validate_invalid_vad_threshold():
    """Test validation catches invalid VAD threshold."""
    config = RecorderConfig.defaults()
    config.vad_threshold_normal = 1.5  # Out of range

    with pytest.raises(ValueError, match="vad_threshold_normal must be between 0 and 1"):
        config.validate()


def test_validate_invalid_num_workers():
    """Test validation catches invalid num_workers."""
    config = RecorderConfig.defaults()
    config.num_workers = 0

    with pytest.raises(ValueError, match="num_workers must be at least 1"):
        config.validate()


def test_from_file_valid(tmp_path):
    """Test loading configuration from valid YAML file."""
    config_path = tmp_path / "test_config.yaml"
    config_path.write_text("""
attention_phrase: "hey"
attention_phrase_threshold: 85.0
base_segment_size: 3.0
base_start_window: 1.5
base_termination_silence: 0.5
vad_threshold_normal: 0.6
vad_threshold_long: 0.75
min_segment_duration: 1.0
whisper_model: "test_model.ggml"
num_workers: 4
whisper_timeout: 30
command_phrase_threshold: 85.0
""")

    config = RecorderConfig.from_file(config_path)

    assert config.attention_phrase == "hey"
    assert config.attention_phrase_threshold == 85.0
    assert config.base_segment_size == 3.0
    assert config.num_workers == 4


def test_from_file_not_found():
    """Test loading from non-existent file raises error."""
    with pytest.raises(FileNotFoundError):
        RecorderConfig.from_file(Path("/nonexistent/config.yaml"))


def test_from_file_invalid_format(tmp_path):
    """Test loading from invalid YAML raises error."""
    config_path = tmp_path / "invalid.yaml"
    config_path.write_text("not a dict")

    with pytest.raises(ValueError, match="Invalid config file format"):
        RecorderConfig.from_file(config_path)
