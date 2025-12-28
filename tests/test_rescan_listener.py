#!/usr/bin/env python
"""
tests/test_rescan_listener.py
Unit tests for RescanListener - Story 008: Rescan Mode
"""

import pytest
import asyncio
import numpy as np
from unittest.mock import Mock, AsyncMock, MagicMock, patch

from palaver.fastapi.rescan_listener import RescanListener, RescanState
from palaver.scribe.audio_events import AudioChunkEvent
from palaver.scribe.draft_events import DraftStartEvent, DraftEndEvent, Draft, TextMark
from palaver.stage_markers import Stage, stage


@stage(Stage.PROTOTYPE, track_coverage=True)
class MockWhisperWrapper:
    """Mock WhisperWrapper for testing."""

    def __init__(self):
        self.received_events = []
        self.model_path = "models/ggml-large3_turbo.bin"

    async def on_audio_event(self, event):
        """Record audio events."""
        self.received_events.append(event)


@stage(Stage.PROTOTYPE, track_coverage=True)
class MockDraftMaker:
    """Mock DraftMaker for testing."""

    def __init__(self):
        self.listeners = []

    def add_event_listener(self, listener):
        """Add listener to receive draft events."""
        self.listeners.append(listener)

    async def emit_draft_end(self, draft):
        """Simulate emitting a DraftEndEvent."""
        event = DraftEndEvent(draft=draft)
        for listener in self.listeners:
            await listener.on_draft_event(event)


@stage(Stage.PROTOTYPE, track_coverage=True)
@pytest.mark.asyncio
async def test_rescan_listener_initialization():
    """Test RescanListener initialization."""
    whisper = MockWhisperWrapper()
    draft_maker = MockDraftMaker()

    listener = RescanListener(
        audio_source_url="ws://test:8000/events",
        revision_target="http://test:8000/api/revisions",
        local_whisper=whisper,
        local_draft_maker=draft_maker,
        buffer_seconds=60.0,
    )

    assert listener.state == RescanState.IDLE
    assert listener.current_draft_id is None
    assert listener.audio_buffer is not None
    assert listener.buffer_seconds == 60.0


@stage(Stage.PROTOTYPE, track_coverage=True)
@pytest.mark.asyncio
async def test_state_transition_idle_to_collecting():
    """Test state transition from IDLE to COLLECTING on DraftStartEvent."""
    whisper = MockWhisperWrapper()
    draft_maker = MockDraftMaker()

    listener = RescanListener(
        audio_source_url="ws://test:8000/events",
        revision_target="http://test:8000/api/revisions",
        local_whisper=whisper,
        local_draft_maker=draft_maker,
    )

    # Create remote draft start event (dict format)
    draft_dict = {
        "draft_id": "test-draft-123",
        "audio_start_time": 1234567890.0,
        "full_text": "Test draft",
    }
    event_dict = {
        "_event_type": "DraftStartEvent",
        "draft": draft_dict,
        "author_uri": "ws://test:8000/events/audio/v1",  # Remote source (contains audio_source_url)
    }

    # Handle the event
    await listener.on_draft_event(event_dict)

    # Verify state transition
    assert listener.state == RescanState.COLLECTING
    assert listener.current_draft_id == "test-draft-123"
    assert listener.current_draft_start_time == 1234567890.0


@stage(Stage.PROTOTYPE, track_coverage=True)
@pytest.mark.asyncio
async def test_audio_buffering():
    """Test audio buffering during COLLECTING state."""
    import time

    whisper = MockWhisperWrapper()
    draft_maker = MockDraftMaker()

    listener = RescanListener(
        audio_source_url="ws://test:8000/events",
        revision_target="http://test:8000/api/revisions",
        local_whisper=whisper,
        local_draft_maker=draft_maker,
    )

    # Transition to COLLECTING state
    listener.state = RescanState.COLLECTING

    # Create audio chunk event (dict format) with current timestamp
    # AudioRingBuffer prunes events older than max_seconds from current time
    now = time.time()
    chunk_dict = {
        "_event_type": "AudioChunkEvent",
        "timestamp": now,
        "duration": 0.03,
        "data": [[0.1, 0.2, 0.3]],  # Serialized numpy array as list
        "sample_rate": 16000,
        "channels": 1,
        "source_id": "test-source",
        "stream_start_time": now - 1.0,
    }

    # Buffer the chunk
    await listener.on_audio_event(chunk_dict)

    # Verify buffering
    assert len(listener.audio_buffer.buffer) == 1
    assert listener.audio_buffer.buffer[0].timestamp == pytest.approx(now, abs=0.1)


@stage(Stage.PROTOTYPE, track_coverage=True)
@pytest.mark.asyncio
async def test_process_rescan_audio_extraction():
    """Test process_rescan extracts correct audio segment."""
    import time

    whisper = MockWhisperWrapper()
    draft_maker = MockDraftMaker()

    listener = RescanListener(
        audio_source_url="ws://test:8000/events",
        revision_target="http://test:8000/api/revisions",
        local_whisper=whisper,
        local_draft_maker=draft_maker,
    )

    # Helper to create AudioChunkProxy
    class AudioChunkProxy:
        def __init__(self, event_dict):
            self.event_dict = event_dict
            self.timestamp = event_dict.get('timestamp')
            self.duration = event_dict.get('duration', 0)
        def __getattr__(self, name):
            return self.event_dict.get(name)

    # Use current time for timestamps (AudioRingBuffer prunes old events)
    now = time.time()

    # Add some audio chunks to buffer
    for i in range(5):
        chunk_dict = {
            "_event_type": "AudioChunkEvent",
            "timestamp": now + i * 0.03,
            "duration": 0.03,
            "data": [[0.1] * 100],  # 100 samples
            "sample_rate": 16000,
            "channels": 1,
            "source_id": "test-source",
            "stream_start_time": now,
        }
        listener.audio_buffer.add(AudioChunkProxy(chunk_dict))

    # Create draft end event
    draft_dict = {
        "draft_id": "test-draft",
        "audio_start_time": now + 0.03,  # Start from second chunk
        "audio_end_time": now + 0.09,    # End at fourth chunk
        "full_text": "Test",
    }
    event_dict = {
        "draft": draft_dict,
    }

    # Process rescan
    await listener.process_rescan(event_dict, listener.audio_buffer)

    # Verify whisper received chunks (should be chunks 1-3: timestamps now+0.03, now+0.06, now+0.09)
    assert len(whisper.received_events) == 3
    assert whisper.received_events[0].timestamp == pytest.approx(now + 0.03, abs=0.001)
    assert whisper.received_events[2].timestamp == pytest.approx(now + 0.09, abs=0.001)


@stage(Stage.PROTOTYPE, track_coverage=True)
@pytest.mark.asyncio
async def test_revision_submission():
    """Test revision submission to remote server."""
    whisper = MockWhisperWrapper()
    draft_maker = MockDraftMaker()

    listener = RescanListener(
        audio_source_url="ws://test:8000/events",
        revision_target="http://test:8000/api/revisions",
        local_whisper=whisper,
        local_draft_maker=draft_maker,
    )

    # Setup state for revision submission
    listener.state = RescanState.RESCANNING
    listener.current_draft_id = "original-draft-123"

    # Mock HTTP client
    mock_response = Mock()
    mock_response.status_code = 201
    mock_response.json.return_value = {
        "revision_id": "revision-456",
        "original_draft_id": "original-draft-123",
        "stored": True,
    }
    mock_response.raise_for_status = Mock()

    listener.http_client.post = AsyncMock(return_value=mock_response)

    # Create local draft end event
    draft = Draft(
        start_text=TextMark(0, 5, "Start"),
        end_text=TextMark(10, 15, "break"),
        full_text="Start a new note. This is improved text. break break break",
        draft_id="local-draft-789",
    )
    event = DraftEndEvent(draft=draft, author_uri=None)  # Local event (no author_uri)

    # Handle rescan result
    await listener.handle_rescan_result(event)

    # Verify HTTP POST was called
    assert listener.http_client.post.called
    call_args = listener.http_client.post.call_args
    assert call_args[0][0] == "http://test:8000/api/revisions"

    # Verify payload
    payload = call_args[1]["json"]
    assert payload["original_draft_id"] == "original-draft-123"
    assert "revised_draft" in payload
    assert payload["metadata"]["source"] == "whisper_reprocess"

    # Verify state transition back to IDLE
    assert listener.state == RescanState.IDLE
    assert listener.current_draft_id is None


@stage(Stage.PROTOTYPE, track_coverage=True)
@pytest.mark.asyncio
async def test_concurrent_draft_rejection():
    """Test that concurrent drafts are rejected (Prototype limitation)."""
    whisper = MockWhisperWrapper()
    draft_maker = MockDraftMaker()

    listener = RescanListener(
        audio_source_url="ws://test:8000/events",
        revision_target="http://test:8000/api/revisions",
        local_whisper=whisper,
        local_draft_maker=draft_maker,
    )

    # Start first draft
    draft_dict_1 = {
        "draft_id": "draft-1",
        "audio_start_time": 1000.0,
    }
    event_dict_1 = {
        "_event_type": "DraftStartEvent",
        "draft": draft_dict_1,
        "author_uri": "ws://test:8000/events/audio/v1",  # Remote source
    }
    await listener.on_draft_event(event_dict_1)
    assert listener.state == RescanState.COLLECTING

    # Attempt to start second draft (should be rejected)
    draft_dict_2 = {
        "draft_id": "draft-2",
        "audio_start_time": 1001.0,
    }
    event_dict_2 = {
        "_event_type": "DraftStartEvent",
        "draft": draft_dict_2,
        "author_uri": "ws://test:8000/events/audio/v1",  # Remote source
    }
    await listener.on_draft_event(event_dict_2)

    # Verify second draft was rejected
    assert listener.state == RescanState.COLLECTING  # Still processing first draft
    assert listener.current_draft_id == "draft-1"  # Still on first draft
