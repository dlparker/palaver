#!/usr/bin/env python
"""
tests/test_vad_recorder_file.py
Test VAD recorder with pre-recorded audio files
"""

import pytest
import sys
from pathlib import Path
from io import StringIO
import json

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from palaver.recorder import vad_recorder


class TestVADRecorderFile:
    """Test VAD recorder using file input"""

    @pytest.fixture
    def audio_file(self):
        """Path to test audio file"""
        return Path("tests/audio_samples/note1.wav")

    @pytest.fixture
    def cleanup_sessions(self):
        """Cleanup session directories after test"""
        yield
        # Note: We'll keep sessions for manual inspection during development
        # Uncomment to auto-cleanup:
        # sessions_dir = Path("sessions")
        # if sessions_dir.exists():
        #     shutil.rmtree(sessions_dir)

    def test_process_note1_file(self, audio_file, cleanup_sessions, monkeypatch, capsys):
        """
        Test processing note1.wav file through VAD recorder.

        Expected behavior:
        - File should be processed without errors
        - VAD should detect 4 speech segments (based on piper.sh design)
        - Segments should be saved as WAV files
        - Transcription should detect "start a new note" command
        - Title should be captured
        - Long note mode should activate (5s silence threshold)
        - Long note should end after 6s silence
        - Mode should restore to normal
        """
        # Verify test file exists
        assert audio_file.exists(), f"Test file not found: {audio_file}"

        print(f"\n{'='*70}")
        print(f"TESTING FILE INPUT: {audio_file}")
        print(f"Expected: 4 segments with long note mode workflow")
        print(f"{'='*70}\n")

        # Mock stdin to automatically provide Enter key presses
        # main() calls input() once: "Press Enter to start..."
        mock_input = StringIO("\n")
        monkeypatch.setattr('sys.stdin', mock_input)

        # Run recorder with file input
        vad_recorder.main(input_source=str(audio_file))

        # Find the session directory (most recent in sessions/)
        sessions_dir = Path("sessions")
        assert sessions_dir.exists(), "Sessions directory not created"

        session_dirs = sorted(sessions_dir.iterdir(), key=lambda p: p.stat().st_mtime)
        assert len(session_dirs) > 0, "No session directory created"

        session_dir = session_dirs[-1]  # Most recent
        print(f"\n📁 Session directory: {session_dir}")

        # Verify session structure
        assert session_dir.is_dir(), f"Session path is not a directory: {session_dir}"

        # Check for expected files
        manifest_path = session_dir / "manifest.json"
        transcript_raw_path = session_dir / "transcript_raw.txt"
        transcript_incremental_path = session_dir / "transcript_incremental.txt"

        assert manifest_path.exists(), "manifest.json not created"
        assert transcript_raw_path.exists(), "transcript_raw.txt not created"
        assert transcript_incremental_path.exists(), "transcript_incremental.txt not created"

        # Load and verify manifest
        with open(manifest_path) as f:
            manifest = json.load(f)

        print(f"\n📋 Manifest contents:")
        print(json.dumps(manifest, indent=2))

        assert "input_source" in manifest, "Manifest missing input_source"
        assert manifest["input_source"]["type"] == "file", "Input source type should be 'file'"
        assert str(audio_file) in manifest["input_source"]["source"], "Source path not recorded"
        assert "total_segments" in manifest, "Manifest missing total_segments"
        assert "segments" in manifest, "Manifest missing segments list"

        total_segments = manifest["total_segments"]
        print(f"\n📊 Total segments: {total_segments}")

        # Verify segment files exist
        for seg_info in manifest["segments"]:
            seg_file = session_dir / seg_info["file"]
            assert seg_file.exists(), f"Segment file missing: {seg_file}"
            print(f"  ✓ {seg_info['file']} ({seg_info['duration_sec']:.2f}s)")

        # Read and display transcript
        print(f"\n📝 Raw Transcript:")
        print("="*70)
        transcript_content = transcript_raw_path.read_text()
        print(transcript_content)
        print("="*70)

        print(f"\n📝 Incremental Transcript:")
        print("="*70)
        incremental_content = transcript_incremental_path.read_text()
        print(incremental_content)
        print("="*70)

        # Validate segment count
        # Expected: 4 segments based on piper.sh with --sentence-silence 6
        # 1. "Clerk, start a new note."
        # 2. "Clerk, This is the title."
        # 3. "Clerk, This is the body, first sentence."
        # 4. "Stop"
        assert total_segments > 0, "No segments were detected"

        # Note: Actual count may vary slightly based on VAD sensitivity
        # Accepting 3-5 segments as reasonable range
        assert 3 <= total_segments <= 5, \
            f"Unexpected segment count: {total_segments} (expected 3-5, ideally 4)"

        # Check for "start a new note" detection in transcript
        transcript_lower = incremental_content.lower()
        assert "start a new note" in transcript_lower or "start new note" in transcript_lower, \
            "Command 'start a new note' not found in transcript"

        # Check for title capture
        # Note: Due to "Clerk," prefix, exact match may vary
        # Just verify we got some transcription
        assert len(incremental_content) > 100, "Transcript seems too short"

        # Capture console output to verify long note workflow
        captured = capsys.readouterr()
        output = captured.out + captured.err

        # STRICT VERIFICATION: Long note mode workflow
        assert "📝 NEW NOTE DETECTED" in output, \
            "Command detection message not found in output"

        assert "🎙️  LONG NOTE MODE ACTIVATED" in output, \
            "Long note mode activation message not found"

        assert "📌 TITLE:" in output, \
            "Title capture message not found"

        assert "[VAD] Mode changed to: long_note" in output, \
            "VAD mode change to long_note not found"

        # Verify mode restoration was queued (indicates long note ended properly)
        assert "[VAD] Mode change queued: normal" in output or \
               "WILL RESTORE NORMAL MODE" in output, \
            "Mode restoration message not found (long note may not have ended properly)"

        # Success summary
        print(f"\n✅ TEST PASSED")
        print(f"   Session: {session_dir.name}")
        print(f"   Segments: {total_segments}")
        print(f"   Command detected: ✓")
        print(f"   Title captured: ✓")
        print(f"   Long note mode activated: ✓")
        print(f"   Mode restored: ✓")
        print(f"   Complete workflow verified!")

        return session_dir


if __name__ == "__main__":
    # Allow running test directly
    pytest.main([__file__, "-v", "-s"])
