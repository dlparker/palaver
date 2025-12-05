"""Tests for SpeechBucket."""

import pytest
from palaver.commands import SpeechBucket
from palaver.config import RecorderConfig


def test_speech_bucket_creation():
    """Test basic SpeechBucket creation."""
    bucket = SpeechBucket(
        name="test_bucket",
        display_name="Test Bucket"
    )

    assert bucket.name == "test_bucket"
    assert bucket.display_name == "Test Bucket"
    assert bucket.segment_size == 1.0
    assert bucket.start_window == 1.0
    assert bucket.termination_silence == 1.0


def test_speech_bucket_with_custom_params():
    """Test SpeechBucket with custom parameters."""
    bucket = SpeechBucket(
        name="custom_bucket",
        display_name="Custom Bucket",
        segment_size=2.0,
        start_window=3.0,
        termination_silence=0.5
    )

    assert bucket.segment_size == 2.0
    assert bucket.start_window == 3.0
    assert bucket.termination_silence == 0.5


def test_get_absolute_params():
    """Test conversion of relative to absolute parameters."""
    config = RecorderConfig.defaults()
    # base_segment_size = 5.0
    # base_start_window = 2.0
    # base_termination_silence = 0.8

    bucket = SpeechBucket(
        name="test",
        display_name="Test",
        segment_size=0.4,   # 0.4 × 5.0 = 2.0
        start_window=3.0,   # 3.0 × 2.0 = 6.0
        termination_silence=6.25  # 6.25 × 0.8 = 5.0
    )

    params = bucket.get_absolute_params(config)

    assert params['segment_size'] == 2.0
    assert params['start_window'] == 6.0
    assert params['termination_silence'] == 5.0


def test_invalid_bucket_name():
    """Test validation catches invalid bucket name."""
    with pytest.raises(ValueError, match="Invalid bucket name"):
        SpeechBucket(
            name="invalid bucket!",  # Spaces and special chars not allowed
            display_name="Invalid"
        )


def test_empty_display_name():
    """Test validation catches empty display_name."""
    with pytest.raises(ValueError, match="display_name cannot be empty"):
        SpeechBucket(
            name="test",
            display_name=""
        )


def test_negative_segment_size():
    """Test validation catches negative segment_size."""
    with pytest.raises(ValueError, match="segment_size must be positive"):
        SpeechBucket(
            name="test",
            display_name="Test",
            segment_size=-1.0
        )


def test_negative_start_window():
    """Test validation catches negative start_window."""
    with pytest.raises(ValueError, match="start_window must be positive"):
        SpeechBucket(
            name="test",
            display_name="Test",
            start_window=-1.0
        )


def test_negative_termination_silence():
    """Test validation catches negative termination_silence."""
    with pytest.raises(ValueError, match="termination_silence must be positive"):
        SpeechBucket(
            name="test",
            display_name="Test",
            termination_silence=-1.0
        )


def test_valid_names_with_underscores_and_hyphens():
    """Test that underscores and hyphens are allowed in names."""
    bucket1 = SpeechBucket(name="test_bucket", display_name="Test")
    bucket2 = SpeechBucket(name="test-bucket", display_name="Test")
    bucket3 = SpeechBucket(name="test_bucket_123", display_name="Test")

    assert bucket1.name == "test_bucket"
    assert bucket2.name == "test-bucket"
    assert bucket3.name == "test_bucket_123"
