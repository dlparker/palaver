# Tests - Fast & Unit Tests

This directory contains **fast and unit tests** that use simulated mode and mocking to test logic without audio recording or transcription overhead.

## Why Fast Tests?

**Problem**: Real transcription tests are slow:
- Audio file processing: ~30 seconds per test
- Whisper transcription: ~10-20 seconds per segment
- Integration test suite: minutes

**Solution**: Simulated mode bypasses audio/VAD/transcription:
- No audio recording or playback
- No VAD processing
- No whisper-cli calls
- Pre-defined transcriptions delivered instantly

**Result**: 100x faster tests (~9 seconds for comprehensive test suite)

## Test Organization

### `tests/` (This Directory)
Fast unit/integration tests using simulated mode:
- Command detection and matching
- State machine workflows
- Text processing logic
- Edge cases and error handling
- Async recorder functionality

**Run time**: ~9 seconds for all tests

### `tests_slow/`
Integration tests with real audio/transcription:
- VAD behavior with audio files
- End-to-end transcription accuracy
- Audio source abstractions
- Real whisper-cli integration

**Run time**: Several minutes

## Running Tests

```bash
# Run only fast tests (recommended during development)
uv run pytest tests/ -v

# Run only slow/integration tests
uv run pytest tests_slow/ -v

# Run all tests (both fast and slow)
uv run pytest

# Run tests by marker
uv run pytest -m fast          # Only fast tests
uv run pytest -m "not slow"    # Skip slow tests
```

## Writing Fast Tests

### Basic Pattern

```python
from palaver.recorder.vad_recorder import main

def test_my_feature(cleanup_sessions):
    """Test description"""

    # Define test scenario with (text, duration_sec) tuples
    simulated_segments = [
        ("First segment text", 2.0),
        ("Second segment text", 1.5),
        ("Third segment text", 3.0),
    ]

    # Run in simulated mode
    session_dir = main(mode="simulated", simulated_segments=simulated_segments)

    # Verify results
    transcript = (session_dir / "transcript_raw.txt").read_text()
    assert "First segment text" in transcript
    assert "Second segment text" in transcript
```

### Testing Command Detection

```python
def test_note_command_detection(cleanup_sessions, capsys):
    """Test 'start new note' command"""

    simulated_segments = [
        ("start a new note", 1.5),      # Command
        ("My Note Title", 2.0),          # Title (next segment)
        ("Body text for note", 3.0),     # Body content
    ]

    session_dir = main(mode="simulated", simulated_segments=simulated_segments)

    # Verify command was detected
    captured = capsys.readouterr()
    output = captured.out + captured.err

    assert "📝 NEW NOTE DETECTED" in output
    assert "📌 TITLE: My Note Title" in output
    assert "🎙️  LONG NOTE MODE ACTIVATED" in output

    # Verify transcript
    transcript = (session_dir / "transcript_raw.txt").read_text()
    assert "My Note Title" in transcript
```

### Testing Multiple Workflows

```python
def test_multiple_notes(cleanup_sessions, capsys):
    """Test multiple note commands in sequence"""

    simulated_segments = [
        ("Some speech", 1.5),
        ("Start new note", 1.5),         # First note command
        ("First Note Title", 2.0),
        ("First note body", 3.0),
        ("More speech", 1.5),
        ("start a new note", 1.5),       # Second note command
        ("Second Note Title", 2.0),
        ("Second note body", 3.0),
    ]

    session_dir = main(mode="simulated", simulated_segments=simulated_segments)

    # Verify both notes detected
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert output.count("📝 NEW NOTE DETECTED") == 2
```

### Testing Fuzzy Matching

```python
def test_command_variations(cleanup_sessions, capsys):
    """Test that command matching handles variations"""

    # All these should match "start new note"
    test_cases = [
        "start new note",           # Exact
        "start a new note",         # With filler word
        "start the new note",       # Different filler
        "Clerk, start new note",    # With prefix artifact
    ]

    for command in test_cases:
        segments = [
            (command, 1.5),
            ("Title", 2.0),
            ("Body", 3.0),
        ]

        session_dir = main(mode="simulated", simulated_segments=segments)

        # Verify command detected
        captured = capsys.readouterr()
        assert "📝 NEW NOTE DETECTED" in (captured.out + captured.err)
```

### Testing Edge Cases

```python
def test_empty_segments_list(cleanup_sessions):
    """Test handling of empty segments"""
    segments = []
    session_dir = main(mode="simulated", simulated_segments=segments)

    # Should complete without error
    assert session_dir.exists()

    # Manifest should show 0 segments
    import json
    manifest = json.loads((session_dir / "manifest.json").read_text())
    assert manifest["total_segments"] == 0

def test_special_characters(cleanup_sessions):
    """Test handling of special characters in text"""
    segments = [
        ("Text with symbols: @#$%^&*()", 2.0),
        ("Unicode: 你好 🎉 Ñoño", 2.0),
    ]

    session_dir = main(mode="simulated", simulated_segments=segments)
    transcript = (session_dir / "transcript_raw.txt").read_text()

    assert "@#$%^&*()" in transcript
    assert "你好" in transcript
    assert "🎉" in transcript
```

## Performance Testing

Fast tests should complete quickly. Use performance assertions to catch regressions:

```python
def test_simulated_mode_is_fast(cleanup_sessions):
    """Verify simulated mode performance"""
    import time

    # 20 segments (would take ~40+ seconds with real transcription)
    segments = [(f"Segment {i}", 2.0) for i in range(20)]

    start_time = time.time()
    session_dir = main(mode="simulated", simulated_segments=segments)
    elapsed = time.time() - start_time

    # Should complete in under 2 seconds
    assert elapsed < 2.0, f"Too slow: {elapsed:.2f}s"
```

## Fixtures

### `cleanup_sessions`
Automatically removes `sessions/` directory after test:

```python
@pytest.fixture
def cleanup_sessions():
    """Cleanup sessions directory after test"""
    yield
    sessions_dir = Path("sessions")
    if sessions_dir.exists():
        shutil.rmtree(sessions_dir)
```

### `capsys`
Built-in pytest fixture for capturing stdout/stderr:

```python
def test_with_output_capture(capsys):
    # ... run code that prints ...

    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert "expected text" in output
```

## What to Test Fast vs Slow

### Test Fast (Simulated Mode)
✅ Command detection and matching
✅ State machine transitions
✅ Text processing logic
✅ Multiple command sequences
✅ Edge cases (empty, special chars, etc.)
✅ Fuzzy matching variations
✅ Transcript generation
✅ Manifest structure

### Test Slow (Real Audio)
✅ VAD segment detection
✅ Silence threshold behavior
✅ Audio file resampling
✅ Whisper transcription accuracy
✅ Real-world audio artifacts
✅ Device vs file input modes

## Best Practices

1. **Write fast tests first**: Verify logic with simulated mode before audio tests
2. **Use descriptive test names**: `test_multiple_notes_with_body_text`
3. **Test one thing**: Each test should verify a specific behavior
4. **Use assertions liberally**: Check output, transcripts, and manifests
5. **Clean up after tests**: Use `cleanup_sessions` fixture
6. **Document test intent**: Add docstrings explaining what's being tested

## Example Test File Structure

```python
"""
tests_fast/test_feature_name.py
Description of what this test file covers
"""

import pytest
from pathlib import Path
import shutil
from palaver.recorder.vad_recorder import main


@pytest.fixture
def cleanup_sessions():
    yield
    if Path("sessions").exists():
        shutil.rmtree("sessions")


class TestFeatureName:
    """Group related tests together"""

    def test_basic_case(self, cleanup_sessions):
        """Test the basic happy path"""
        # ...

    def test_edge_case(self, cleanup_sessions):
        """Test edge case behavior"""
        # ...

    def test_error_handling(self, cleanup_sessions):
        """Test error conditions"""
        # ...
```

## Performance Comparison

| Test Type | Test Count | Duration | Tests/Second |
|-----------|------------|----------|--------------|
| Fast (simulated) | 10 | ~9s | 1.1 |
| Slow (real audio) | 33 | ~281s | 0.12 |

**Speedup**: ~9x faster per test with simulated mode

## Future Enhancements

As more commands are added, fast tests will become increasingly valuable:

- "end note" command
- "cancel note" command
- "save note as [name]" command
- "create task [description]" command
- Command chaining and composition
- Multi-language support
- Custom action phrases

All of these can be tested rapidly with simulated mode before writing integration tests.

## See Also

- `design_docs/vad_recorder_async_refactoring_plan.md` - Async architecture refactoring
- `design_docs/simulated_transcription_refactoring.md` - Simulated mode design
- `CLAUDE.md` - Project documentation and conventions
- `tests_slow/` - Integration tests with real audio
- `src/palaver/recorder/async_vad_recorder.py` - Core async recorder implementation
