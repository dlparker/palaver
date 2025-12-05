"""Tests for CommandDoc base class and SimpleNote implementation."""

import pytest
from pathlib import Path
from palaver.commands import CommandDoc, SpeechBucket, SimpleNote


def test_simple_note_command_phrase():
    """Test SimpleNote command phrase."""
    note = SimpleNote()
    assert note.command_phrase == "start new note"


def test_simple_note_speech_buckets():
    """Test SimpleNote has correct buckets."""
    note = SimpleNote()
    buckets = note.speech_buckets

    assert len(buckets) == 2
    assert buckets[0].name == "note_title"
    assert buckets[0].display_name == "Note Title"
    assert buckets[1].name == "note_body"
    assert buckets[1].display_name == "Note Body"


def test_simple_note_validate_buckets():
    """Test SimpleNote bucket validation passes."""
    note = SimpleNote()
    note.validate_buckets()  # Should not raise


def test_simple_note_render(tmp_path):
    """Test SimpleNote render creates markdown file."""
    note = SimpleNote()

    bucket_contents = {
        "note_title": "My Important Meeting",
        "note_body": "We discussed project timeline and deliverables."
    }

    output_files = note.render(bucket_contents, tmp_path)

    assert len(output_files) == 1
    assert output_files[0].exists()
    assert output_files[0].name == "note_0001_my_important_meeting.md"

    content = output_files[0].read_text()
    assert "# My Important Meeting" in content
    assert "We discussed project timeline and deliverables." in content


def test_simple_note_render_multiple_notes(tmp_path):
    """Test multiple notes create incrementing filenames."""
    note = SimpleNote()

    # First note
    files1 = note.render(
        {"note_title": "First Note", "note_body": "Body 1"},
        tmp_path
    )
    assert files1[0].name == "note_0001_first_note.md"

    # Second note
    files2 = note.render(
        {"note_title": "Second Note", "note_body": "Body 2"},
        tmp_path
    )
    assert files2[0].name == "note_0002_second_note.md"

    # Third note
    files3 = note.render(
        {"note_title": "Third Note", "note_body": "Body 3"},
        tmp_path
    )
    assert files3[0].name == "note_0003_third_note.md"


def test_simple_note_render_slugification(tmp_path):
    """Test title slugification for filename."""
    note = SimpleNote()

    bucket_contents = {
        "note_title": "My Meeting: Project X (2024)!",
        "note_body": "Content"
    }

    output_files = note.render(bucket_contents, tmp_path)
    assert "my_meeting_project_x_2024" in output_files[0].name


def test_simple_note_render_long_title(tmp_path):
    """Test very long titles are truncated in filename."""
    note = SimpleNote()

    long_title = "A" * 100  # 100 character title
    bucket_contents = {
        "note_title": long_title,
        "note_body": "Content"
    }

    output_files = note.render(bucket_contents, tmp_path)
    # Slug should be limited to 50 chars
    # Extract slug part (after "note_NNNN_")
    filename = output_files[0].stem
    parts = filename.split('_', 2)  # Split into ['note', 'NNNN', 'slug']
    slug = parts[2] if len(parts) > 2 else parts[1]
    assert len(slug) <= 50


def test_simple_note_render_empty_title(tmp_path):
    """Test empty title defaults to 'Untitled'."""
    note = SimpleNote()

    bucket_contents = {
        "note_title": "",
        "note_body": "Content"
    }

    output_files = note.render(bucket_contents, tmp_path)
    assert "untitled" in output_files[0].name

    content = output_files[0].read_text()
    assert "# Untitled" in content


def test_simple_note_render_missing_title():
    """Test render raises error if note_title bucket missing."""
    note = SimpleNote()

    bucket_contents = {
        "note_body": "Content"
    }

    with pytest.raises(ValueError, match="Missing required bucket: note_title"):
        note.render(bucket_contents, Path("/tmp"))


def test_simple_note_render_missing_body():
    """Test render raises error if note_body bucket missing."""
    note = SimpleNote()

    bucket_contents = {
        "note_title": "Title"
    }

    with pytest.raises(ValueError, match="Missing required bucket: note_body"):
        note.render(bucket_contents, Path("/tmp"))


def test_command_doc_validate_duplicate_names():
    """Test CommandDoc validation catches duplicate bucket names."""

    class BadCommandDoc(CommandDoc):
        @property
        def command_phrase(self):
            return "test command"

        @property
        def speech_buckets(self):
            return [
                SpeechBucket(name="duplicate", display_name="First"),
                SpeechBucket(name="duplicate", display_name="Second"),
            ]

        def render(self, bucket_contents, output_dir):
            return []

    doc = BadCommandDoc()

    with pytest.raises(ValueError, match="Duplicate bucket names"):
        doc.validate_buckets()


def test_command_doc_validate_duplicate_display_names():
    """Test CommandDoc validation catches duplicate display_names."""

    class BadCommandDoc(CommandDoc):
        @property
        def command_phrase(self):
            return "test command"

        @property
        def speech_buckets(self):
            return [
                SpeechBucket(name="first", display_name="Same Name"),
                SpeechBucket(name="second", display_name="Same Name"),
            ]

        def render(self, bucket_contents, output_dir):
            return []

    doc = BadCommandDoc()

    with pytest.raises(ValueError, match="Duplicate display_names"):
        doc.validate_buckets()


def test_command_doc_repr():
    """Test CommandDoc string representation."""
    note = SimpleNote()
    repr_str = repr(note)

    assert "SimpleNote" in repr_str
    assert "start new note" in repr_str
    assert "note_title" in repr_str
    assert "note_body" in repr_str
