"""
tests_fast/test_simulated_recorder.py
Fast tests using simulated mode (no actual audio/transcription)

These tests verify downstream text processing without the overhead
of VAD, audio recording, or whisper transcription.
"""

import pytest
from pathlib import Path
import shutil

from palaver.recorder.vad_recorder import main


@pytest.fixture
def cleanup_sessions():
    """Cleanup sessions directory after test"""
    yield
    sessions_dir = Path("sessions")
    if sessions_dir.exists():
        shutil.rmtree(sessions_dir)


class TestSimulatedMode:
    """Tests for simulated mode (fast, no audio/transcription)"""

    def test_basic_simulated_mode(self, cleanup_sessions, capsys):
        """Test basic simulated mode execution"""
        segments = [
            ("Hello, this is a test.", 2.0),
            ("This is segment two.", 1.5),
            ("Final segment here.", 1.8),
        ]

        session_dir = main(mode="simulated", simulated_segments=segments)

        # Verify session directory was created
        assert session_dir.exists()
        assert session_dir.parent == Path("sessions")

        # Verify manifest was created
        manifest_path = session_dir / "manifest.json"
        assert manifest_path.exists()

        import json
        manifest = json.loads(manifest_path.read_text())
        assert manifest["total_segments"] == 3
        assert manifest["input_source"]["type"] == "simulated"
        assert len(manifest["segments"]) == 3

        # Verify transcript files were created
        transcript_raw = session_dir / "transcript_raw.txt"
        transcript_incremental = session_dir / "transcript_incremental.txt"
        assert transcript_raw.exists()
        assert transcript_incremental.exists()

        # Verify transcript content
        raw_content = transcript_raw.read_text()
        assert "Hello, this is a test." in raw_content
        assert "This is segment two." in raw_content
        assert "Final segment here." in raw_content

    def test_command_detection_start_note(self, cleanup_sessions, capsys):
        """Test 'start new note' command detection in simulated mode"""
        segments = [
            ("Start a new note", 1.5),
            ("My Important Title", 2.0),
            ("This is the body of my note with lots of content.", 3.0),
        ]

        session_dir = main(mode="simulated", simulated_segments=segments)

        # Capture output to verify command detection
        captured = capsys.readouterr()
        output = captured.out + captured.err

        # Verify command was detected
        assert "📝 NEW NOTE DETECTED" in output
        assert "📌 TITLE:" in output
        assert "My Important Title" in output
        assert "[Simulated] Mode change requested: long_note" in output

        # Verify transcript content
        transcript_raw = session_dir / "transcript_raw.txt"
        raw_content = transcript_raw.read_text()
        assert "Start a new note" in raw_content
        assert "My Important Title" in raw_content
        assert "This is the body of my note" in raw_content

    def test_multiple_notes_workflow(self, cleanup_sessions, capsys):
        """Test multiple note commands in sequence"""
        segments = [
            ("Some initial speech", 1.5),
            ("Start new note", 1.5),
            ("First Note Title", 2.0),
            ("First note body text", 3.0),
            ("More regular speech", 1.5),
            ("Start a new note", 1.5),
            ("Second Note Title", 2.0),
            ("Second note body content", 3.0),
        ]

        session_dir = main(mode="simulated", simulated_segments=segments)

        # Capture output
        captured = capsys.readouterr()
        output = captured.out + captured.err

        # Verify both notes were detected
        assert output.count("📝 NEW NOTE DETECTED") == 2
        assert "First Note Title" in output
        assert "Second Note Title" in output

        # Verify transcript
        transcript_raw = session_dir / "transcript_raw.txt"
        raw_content = transcript_raw.read_text()
        assert "First Note Title" in raw_content
        assert "Second Note Title" in raw_content

    def test_fuzzy_command_matching(self, cleanup_sessions, capsys):
        """Test fuzzy matching of 'start new note' command"""
        # Test various phrasings that should match
        test_cases = [
            ("start new note", 1.5),  # Exact (no "a")
            ("start a new note", 1.5),  # With "a"
            ("Start the new note", 1.5),  # With "the"
            ("Clerk, start new note", 1.5),  # With clerk prefix
        ]

        for i, (command, duration) in enumerate(test_cases):
            segments = [
                (command, duration),
                (f"Title {i}", 2.0),
                (f"Body {i}", 3.0),
            ]

            session_dir = main(mode="simulated", simulated_segments=segments)

            # Verify command was detected
            captured = capsys.readouterr()
            output = captured.out + captured.err
            assert "📝 NEW NOTE DETECTED" in output, f"Failed for command: {command}"

            # Cleanup for next iteration
            shutil.rmtree(Path("sessions"))

    def test_simulated_mode_validation(self):
        """Test that simulated mode requires simulated_segments"""
        with pytest.raises(ValueError, match="simulated_segments required"):
            main(mode="simulated", simulated_segments=None)

    def test_empty_segments_list(self, cleanup_sessions):
        """Test handling of empty segments list"""
        segments = []
        session_dir = main(mode="simulated", simulated_segments=segments)

        # Should complete without error
        assert session_dir.exists()

        # Manifest should show 0 segments
        import json
        manifest_path = session_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        assert manifest["total_segments"] == 0
        assert len(manifest["segments"]) == 0

    def test_long_transcripts(self, cleanup_sessions):
        """Test handling of very long transcript text"""
        long_text = "This is a very long segment. " * 100  # ~3000 chars
        segments = [
            ("start new note", 1.5),
            ("Long Content Note", 2.0),
            (long_text, 30.0),
        ]

        session_dir = main(mode="simulated", simulated_segments=segments)

        # Verify long text was processed correctly
        transcript_raw = session_dir / "transcript_raw.txt"
        raw_content = transcript_raw.read_text()
        assert long_text in raw_content

    def test_special_characters_in_transcript(self, cleanup_sessions):
        """Test handling of special characters in transcribed text"""
        segments = [
            ("Text with special chars: @#$%^&*()", 2.0),
            ("Unicode: 你好 🎉 Ñoño", 2.0),
            ("Quotes: \"double\" and 'single'", 2.0),
        ]

        session_dir = main(mode="simulated", simulated_segments=segments)

        # Verify special characters preserved
        transcript_raw = session_dir / "transcript_raw.txt"
        raw_content = transcript_raw.read_text()
        assert "@#$%^&*()" in raw_content
        assert "你好" in raw_content
        assert "🎉" in raw_content

    def test_manifest_structure(self, cleanup_sessions):
        """Test manifest JSON structure for simulated mode"""
        segments = [
            ("First", 1.5),
            ("Second", 2.0),
            ("Third", 1.8),
        ]

        session_dir = main(mode="simulated", simulated_segments=segments)

        import json
        manifest_path = session_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text())

        # Verify required fields
        assert "session_start_utc" in manifest
        assert "samplerate" in manifest
        assert "total_segments" in manifest
        assert "input_source" in manifest
        assert "segments" in manifest

        # Verify input_source metadata
        assert manifest["input_source"]["type"] == "simulated"
        assert manifest["input_source"]["source"] == "simulated_segments"

        # Verify segment structure
        for i, seg_info in enumerate(manifest["segments"]):
            assert seg_info["index"] == i
            assert seg_info["file"] is None  # No WAV files in simulated mode
            assert "duration_sec" in seg_info
            assert isinstance(seg_info["duration_sec"], (int, float))


class TestSimulatedModePerformance:
    """Performance tests to verify simulated mode is fast"""

    def test_simulated_mode_is_fast(self, cleanup_sessions):
        """Verify simulated mode completes quickly"""
        import time

        # Create 20 segments (would take minutes with real transcription)
        segments = [(f"Segment {i} text", 2.0) for i in range(20)]

        start_time = time.time()
        session_dir = main(mode="simulated", simulated_segments=segments)
        elapsed = time.time() - start_time

        # Should complete in under 2 seconds (vs ~40+ seconds for real transcription)
        assert elapsed < 2.0, f"Simulated mode took {elapsed:.2f}s (expected < 2s)"

        # Verify all segments were processed
        import json
        manifest_path = session_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        assert manifest["total_segments"] == 20


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
