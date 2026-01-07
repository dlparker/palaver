"""
tests/test_draft_http_endpoints.py
Test suite for draft HTTP endpoints and supporting utilities
"""

import pytest
import logging
from datetime import datetime, timezone
from pathlib import Path
import tempfile
from typing import Optional

from fastapi.testclient import TestClient
from sqlmodel import create_engine, Session

from palaver.utils.time_utils import parse_timestamp
from palaver_shared.serializers import draft_record_to_dict
from palaver.scribe.recorders.sql_drafts import SQLDraftRecorder, DraftRecord
from palaver_shared.draft_events import Draft

logger = logging.getLogger("test_draft_http_endpoints")


# ============================================================================
# Test Time Parsing Utility
# ============================================================================

def test_parse_timestamp_unix_float():
    """Test parsing Unix timestamp as float"""
    result = parse_timestamp("1704067200.5")
    assert result == 1704067200.5


def test_parse_timestamp_unix_int():
    """Test parsing Unix timestamp as integer"""
    result = parse_timestamp("1704067200")
    assert result == 1704067200.0


def test_parse_timestamp_iso_basic():
    """Test parsing ISO datetime string without timezone"""
    result = parse_timestamp("2024-01-01T00:00:00")
    # Convert to datetime to verify it's correct
    dt = datetime.fromtimestamp(result)
    assert dt.year == 2024
    assert dt.month == 1
    assert dt.day == 1


def test_parse_timestamp_iso_with_z():
    """Test parsing ISO datetime string with Z suffix (UTC)"""
    result = parse_timestamp("2024-01-01T00:00:00Z")
    # This should be treated as UTC
    dt = datetime.fromtimestamp(result, tz=timezone.utc)
    assert dt.year == 2024
    assert dt.month == 1
    assert dt.day == 1
    assert dt.hour == 0


def test_parse_timestamp_iso_with_timezone():
    """Test parsing ISO datetime string with timezone offset"""
    result = parse_timestamp("2024-01-01T00:00:00+00:00")
    dt = datetime.fromtimestamp(result, tz=timezone.utc)
    assert dt.year == 2024
    assert dt.month == 1
    assert dt.day == 1


def test_parse_timestamp_invalid_format():
    """Test that invalid format raises ValueError with helpful message"""
    with pytest.raises(ValueError) as exc_info:
        parse_timestamp("not-a-timestamp")

    assert "Invalid timestamp format" in str(exc_info.value)
    assert "not-a-timestamp" in str(exc_info.value)


def test_parse_timestamp_empty_string():
    """Test that empty string raises ValueError"""
    with pytest.raises(ValueError):
        parse_timestamp("")


# ============================================================================
# Test DraftRecord Serialization
# ============================================================================

def test_draft_record_to_dict():
    """Test converting DraftRecord to dict"""
    # Create a DraftRecord instance
    record = DraftRecord(
        draft_id="test-uuid-123",
        timestamp=1704067200.5,
        full_text="Test draft text",
        classname="<class 'palaver.scribe.draft_events.Draft'>",
        directory_path="/path/to/draft",
        parent_draft_id=None,
        created_at=datetime(2024, 1, 1, 12, 0, 0)
    )

    result = draft_record_to_dict(record)

    assert result["draft_id"] == "test-uuid-123"
    assert result["timestamp"] == 1704067200.5
    assert result["full_text"] == "Test draft text"
    assert result["classname"] == "<class 'palaver.scribe.draft_events.Draft'>"
    assert result["directory_path"] == "/path/to/draft"
    assert result["parent_draft_id"] is None
    assert result["created_at"] == "2024-01-01T12:00:00"


def test_draft_record_to_dict_none():
    """Test that None input returns None"""
    result = draft_record_to_dict(None)
    assert result is None


def test_draft_record_to_dict_with_parent():
    """Test converting DraftRecord with parent_draft_id"""
    record = DraftRecord(
        draft_id="child-uuid",
        timestamp=1704067300.0,
        full_text="Child draft",
        classname="<class 'palaver.scribe.draft_events.Draft'>",
        directory_path="/path/to/child",
        parent_draft_id="parent-uuid",
        created_at=datetime(2024, 1, 1, 12, 5, 0)
    )

    result = draft_record_to_dict(record)
    assert result["parent_draft_id"] == "parent-uuid"


# ============================================================================
# Test SQLDraftRecorder Query Methods
# ============================================================================

@pytest.fixture
def temp_dir():
    """Create a temporary directory for testing"""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def draft_recorder(temp_dir):
    """Create a SQLDraftRecorder with test database"""
    recorder = SQLDraftRecorder(
        output_dir=temp_dir,
        enable_file_storage=False
    )
    return recorder


@pytest.fixture
async def populated_db(draft_recorder):
    """Populate database with test drafts"""
    # Create test drafts with different timestamps
    drafts = [
        Draft(
            start_text="start",
            end_text="end",
            full_text=f"Draft {i}",
            timestamp=1704067200.0 + i * 100,  # 100 seconds apart
            draft_id=f"draft-uuid-{i}",
            parent_draft_id=None if i != 5 else "draft-uuid-2",  # Draft 5 has parent
            audio_start_time=1704067200.0 + i * 100,
            audio_end_time=1704067200.0 + i * 100 + 10
        )
        for i in range(10)
    ]

    # Add drafts to database
    for draft in drafts:
        await draft_recorder.add_draft(draft)

    return draft_recorder


async def test_get_all_drafts_paginated(populated_db):
    """Test pagination without filtering"""
    recorder = populated_db

    # Get first page
    drafts, total = recorder.get_all_drafts_paginated(limit=5, offset=0, order="asc")

    assert len(drafts) == 5
    assert total == 10
    assert drafts[0].full_text == "Draft 0"
    assert drafts[4].full_text == "Draft 4"

    # Get second page
    drafts, total = recorder.get_all_drafts_paginated(limit=5, offset=5, order="asc")

    assert len(drafts) == 5
    assert total == 10
    assert drafts[0].full_text == "Draft 5"
    assert drafts[4].full_text == "Draft 9"


async def test_get_all_drafts_paginated_desc(populated_db):
    """Test pagination with descending order"""
    recorder = populated_db

    drafts, total = recorder.get_all_drafts_paginated(limit=3, offset=0, order="desc")

    assert len(drafts) == 3
    assert total == 10
    assert drafts[0].full_text == "Draft 9"
    assert drafts[2].full_text == "Draft 7"


async def test_get_all_drafts_paginated_offset_beyond_total(populated_db):
    """Test pagination with offset beyond total"""
    recorder = populated_db

    drafts, total = recorder.get_all_drafts_paginated(limit=10, offset=20)

    assert len(drafts) == 0
    assert total == 10


async def test_get_drafts_since(populated_db):
    """Test filtering by timestamp"""
    recorder = populated_db

    # Get drafts since timestamp of draft 5
    since_ts = 1704067200.0 + 5 * 100
    drafts, total = recorder.get_drafts_since(
        since_timestamp=since_ts,
        limit=100,
        offset=0,
        order="asc"
    )

    assert len(drafts) == 5  # Drafts 5-9
    assert total == 5
    assert drafts[0].full_text == "Draft 5"
    assert drafts[4].full_text == "Draft 9"


async def test_get_drafts_since_with_pagination(populated_db):
    """Test filtering by timestamp with pagination"""
    recorder = populated_db

    since_ts = 1704067200.0 + 2 * 100  # From draft 2 onwards
    drafts, total = recorder.get_drafts_since(
        since_timestamp=since_ts,
        limit=3,
        offset=2,
        order="asc"
    )

    assert len(drafts) == 3
    assert total == 8  # Drafts 2-9
    assert drafts[0].full_text == "Draft 4"  # Offset by 2


async def test_get_drafts_since_no_matches(populated_db):
    """Test filtering with timestamp after all drafts"""
    recorder = populated_db

    since_ts = 1704067200.0 + 1000 * 100  # Far in the future
    drafts, total = recorder.get_drafts_since(since_timestamp=since_ts)

    assert len(drafts) == 0
    assert total == 0


async def test_get_draft_with_family(populated_db):
    """Test getting draft with parent and children"""
    recorder = populated_db

    # Draft 5 has parent (draft 2)
    draft, parent, children = recorder.get_draft_with_family("draft-uuid-5")

    assert draft is not None
    assert draft.full_text == "Draft 5"
    assert parent is not None
    assert parent.full_text == "Draft 2"
    assert parent.draft_id == "draft-uuid-2"


async def test_get_draft_with_family_no_parent(populated_db):
    """Test getting draft without parent"""
    recorder = populated_db

    # Draft 0 has no parent
    draft, parent, children = recorder.get_draft_with_family("draft-uuid-0")

    assert draft is not None
    assert draft.full_text == "Draft 0"
    assert parent is None


async def test_get_draft_with_family_not_found(populated_db):
    """Test getting non-existent draft"""
    recorder = populated_db

    draft, parent, children = recorder.get_draft_with_family("non-existent-uuid")

    assert draft is None
    assert parent is None
    assert children == []


# ============================================================================
# Test HTTP Endpoints (Integration Tests)
# ============================================================================
# Note: These tests would require a running FastAPI server instance
# For now, we'll create basic structure - full integration tests would need
# a test server fixture

@pytest.mark.skip(reason="Requires FastAPI server fixture - implement after server integration")
async def test_list_drafts_endpoint():
    """Test GET /drafts endpoint"""
    # TODO: Implement with FastAPI TestClient
    pass


@pytest.mark.skip(reason="Requires FastAPI server fixture - implement after server integration")
async def test_list_drafts_with_since_filter():
    """Test GET /drafts?since=timestamp endpoint"""
    # TODO: Implement with FastAPI TestClient
    pass


@pytest.mark.skip(reason="Requires FastAPI server fixture - implement after server integration")
async def test_get_draft_by_id_endpoint():
    """Test GET /drafts/{draft_id} endpoint"""
    # TODO: Implement with FastAPI TestClient
    pass


@pytest.mark.skip(reason="Requires FastAPI server fixture - implement after server integration")
async def test_get_draft_with_parent_endpoint():
    """Test GET /drafts/{draft_id}?include_parent=true endpoint"""
    # TODO: Implement with FastAPI TestClient
    pass
