#!/usr/bin/env python
"""
tests/test_sql_drafts_revisions.py
Unit tests for SQLDraftRecorder revision storage methods
"""

import pytest
import json
import tempfile
from pathlib import Path
from datetime import datetime

from palaver.scribe.recorders.sql_drafts import SQLDraftRecorder, DraftRecord, RevisionRecord
from palaver.scribe.draft_events import Draft, TextMark
from palaver.scribe.text_events import TextEvent
from palaver.stage_markers import Stage, stage


@stage(Stage.PROTOTYPE, track_coverage=True)
@pytest.fixture
async def recorder():
    """Create a SQLDraftRecorder with temporary directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        recorder = SQLDraftRecorder(Path(tmpdir))
        yield recorder


@stage(Stage.PROTOTYPE, track_coverage=True)
@pytest.fixture
async def sample_draft():
    """Create a sample Draft object for testing."""
    draft = Draft(
        draft_id="test-draft-123",
        timestamp=1735330000.0,
        start_text=TextMark(start=0, end=10, text="start note"),
        end_text=TextMark(start=50, end=60, text="end note"),
        full_text="start note this is a test draft end note",
        start_matched_events=[
            TextEvent(
                text="start note",
                audio_source_id="test_source",
                timestamp=1735330000.0,
                audio_start_time=1735330000.0,
                audio_end_time=1735330001.0
            )
        ],
        end_matched_events=[
            TextEvent(
                text="end note",
                audio_source_id="test_source",
                timestamp=1735330005.0,
                audio_start_time=1735330005.0,
                audio_end_time=1735330006.0
            )
        ]
    )
    return draft


@stage(Stage.PROTOTYPE, track_coverage=True)
@pytest.mark.asyncio
async def test_store_revision_success(recorder, sample_draft):
    """Test storing a revision successfully."""
    # First, store the original draft
    recorder._current_draft = sample_draft
    recorder._current_dir = recorder._output_dir / "test-draft"
    recorder._current_dir.mkdir()
    await recorder._save_to_database()

    # Create revised draft
    revised_draft = Draft(
        draft_id="test-draft-revised-456",
        timestamp=1735330010.0,
        start_text=TextMark(start=0, end=10, text="start note"),
        end_text=TextMark(start=50, end=70, text="end note"),
        full_text="start note this is an improved test draft end note",
        start_matched_events=sample_draft.start_matched_events,
        end_matched_events=sample_draft.end_matched_events
    )

    # Serialize revised draft
    from dataclasses import asdict
    revised_draft_dict = asdict(revised_draft)
    # Convert TextEvent objects to dicts
    revised_draft_dict['start_matched_events'] = [
        asdict(e) for e in revised_draft.start_matched_events
    ]
    revised_draft_dict['end_matched_events'] = [
        asdict(e) for e in revised_draft.end_matched_events
    ]
    revised_draft_json = json.dumps(revised_draft_dict)

    # Store revision
    metadata = {
        "model": "multilang_whisper_large3_turbo.ggml",
        "source": "rescan_server",
        "source_uri": "http://192.168.100.214:8765/transcription/v1"
    }

    revision_id = await recorder.store_revision(
        original_draft_id="test-draft-123",
        revised_draft_json=revised_draft_json,
        metadata=metadata
    )

    # Verify revision was stored
    assert revision_id is not None
    assert isinstance(revision_id, str)
    assert len(revision_id) > 0


@stage(Stage.PROTOTYPE, track_coverage=True)
@pytest.mark.asyncio
async def test_store_revision_draft_not_found(recorder):
    """Test storing a revision for non-existent draft raises ValueError."""
    metadata = {"model": "test_model"}

    with pytest.raises(ValueError, match="Original draft not found"):
        await recorder.store_revision(
            original_draft_id="nonexistent-draft-id",
            revised_draft_json='{"test": "data"}',
            metadata=metadata
        )


@stage(Stage.PROTOTYPE, track_coverage=True)
@pytest.mark.asyncio
async def test_get_revisions_empty(recorder, sample_draft):
    """Test getting revisions when none exist."""
    # Store original draft
    recorder._current_draft = sample_draft
    recorder._current_dir = recorder._output_dir / "test-draft"
    recorder._current_dir.mkdir()
    await recorder._save_to_database()

    # Get revisions
    revisions = await recorder.get_revisions("test-draft-123")

    assert revisions == []


@stage(Stage.PROTOTYPE, track_coverage=True)
@pytest.mark.asyncio
async def test_get_revisions_multiple(recorder, sample_draft):
    """Test getting multiple revisions ordered by created_at descending."""
    # Store original draft
    recorder._current_draft = sample_draft
    recorder._current_dir = recorder._output_dir / "test-draft"
    recorder._current_dir.mkdir()
    await recorder._save_to_database()

    # Store multiple revisions
    metadata1 = {"model": "model_v1", "source": "server1"}
    metadata2 = {"model": "model_v2", "source": "server2"}
    metadata3 = {"model": "model_v3", "source": "server3"}

    revision_id1 = await recorder.store_revision(
        original_draft_id="test-draft-123",
        revised_draft_json='{"full_text": "revision 1"}',
        metadata=metadata1
    )

    # Small delay to ensure different timestamps
    import asyncio
    await asyncio.sleep(0.01)

    revision_id2 = await recorder.store_revision(
        original_draft_id="test-draft-123",
        revised_draft_json='{"full_text": "revision 2"}',
        metadata=metadata2
    )

    await asyncio.sleep(0.01)

    revision_id3 = await recorder.store_revision(
        original_draft_id="test-draft-123",
        revised_draft_json='{"full_text": "revision 3"}',
        metadata=metadata3
    )

    # Get revisions
    revisions = await recorder.get_revisions("test-draft-123")

    assert len(revisions) == 3

    # Verify ordering (newest first)
    assert revisions[0].revision_id == revision_id3
    assert revisions[1].revision_id == revision_id2
    assert revisions[2].revision_id == revision_id1

    # Verify metadata
    assert revisions[0].model == "model_v3"
    assert revisions[0].source == "server3"
    assert revisions[1].model == "model_v2"
    assert revisions[1].source == "server2"
    assert revisions[2].model == "model_v1"
    assert revisions[2].source == "server1"


@stage(Stage.PROTOTYPE, track_coverage=True)
@pytest.mark.asyncio
async def test_revision_json_roundtrip(recorder, sample_draft):
    """Test that Draft JSON serialization/deserialization preserves data."""
    # Store original draft
    recorder._current_draft = sample_draft
    recorder._current_dir = recorder._output_dir / "test-draft"
    recorder._current_dir.mkdir()
    await recorder._save_to_database()

    # Create and serialize revised draft
    from dataclasses import asdict
    revised_draft_dict = asdict(sample_draft)
    revised_draft_dict['full_text'] = "modified text"
    revised_draft_dict['start_matched_events'] = [
        asdict(e) for e in sample_draft.start_matched_events
    ]
    revised_draft_dict['end_matched_events'] = [
        asdict(e) for e in sample_draft.end_matched_events
    ]
    revised_draft_json = json.dumps(revised_draft_dict)

    # Store revision
    metadata = {"model": "test_model"}
    revision_id = await recorder.store_revision(
        original_draft_id="test-draft-123",
        revised_draft_json=revised_draft_json,
        metadata=metadata
    )

    # Retrieve and deserialize
    revisions = await recorder.get_revisions("test-draft-123")
    assert len(revisions) == 1

    retrieved_draft_dict = json.loads(revisions[0].revised_draft_json)
    assert retrieved_draft_dict['full_text'] == "modified text"
    assert retrieved_draft_dict['draft_id'] == sample_draft.draft_id
