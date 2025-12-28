#!/usr/bin/env python
"""
tests/test_event_router.py
Unit tests for EventRouter - direct testing without HTTP/FastAPI stack
"""

import pytest
import asyncio
import numpy as np
from dataclasses import dataclass
from typing import Set, Dict, Any

from palaver.fastapi.event_router import EventRouter
from palaver.scribe.audio_events import (
    AudioChunkEvent,
    AudioSpeechStartEvent,
    AudioSpeechStopEvent,
)
from palaver.scribe.text_events import TextEvent
from palaver.scribe.draft_events import DraftStartEvent, DraftEndEvent, Draft, TextMark
from palaver.stage_markers import Stage, stage


@stage(Stage.PROTOTYPE, track_coverage=True)
class MockWebSocket:
    """Mock websocket for testing EventRouter without FastAPI."""

    def __init__(self):
        self.sent_messages = []
        self.closed = False

    async def send_json(self, data: Dict[str, Any]):
        """Record messages sent through websocket."""
        self.sent_messages.append(data)

    async def close(self, code: int = 1000, reason: str = ""):
        """Mock close."""
        self.closed = True


@stage(Stage.PROTOTYPE, track_coverage=True)
@pytest.mark.asyncio
async def test_event_router_register_unregister():
    """Test client registration and unregistration."""
    router = EventRouter()
    ws1 = MockWebSocket()
    ws2 = MockWebSocket()

    # Register clients
    await router.register_client(ws1, {"all"})
    await router.register_client(ws2, {"TextEvent", "DraftStartEvent"})

    assert len(router.clients) == 2
    assert router.clients[ws1] == {"all"}
    assert router.clients[ws2] == {"TextEvent", "DraftStartEvent"}

    # Unregister client
    await router.unregister_client(ws1)
    assert len(router.clients) == 1
    assert ws1 not in router.clients

    # Unregister non-existent client (should not error)
    await router.unregister_client(ws1)
    assert len(router.clients) == 1


@stage(Stage.PROTOTYPE, track_coverage=True)
@pytest.mark.asyncio
async def test_event_router_all_subscription():
    """Test 'all' subscription excludes AudioChunkEvent by default."""
    router = EventRouter()
    ws = MockWebSocket()

    await router.register_client(ws, {"all"})

    # Send various events
    audio_start = AudioSpeechStartEvent(
        source_id="test_source",
        stream_start_time=0.0,
        timestamp=0.0,
        silence_period_ms=800,
        vad_threshold=0.5,
        sampling_rate=16000,
        speech_pad_ms=1500
    )
    audio_chunk = AudioChunkEvent(
        source_id="test_source",
        stream_start_time=0.0,
        timestamp=0.1,
        data=np.array([0.1, 0.2, 0.3]),
        duration=0.01,
        sample_rate=16000,
        channels=1,
        blocksize=160,
        datatype="float32",
        in_speech=True
    )
    text_event = TextEvent(
        text="test",
        audio_source_id="test_source",
        audio_start_time=0.0,
        audio_end_time=1.0
    )

    await router.on_audio_event(audio_start)
    await router.on_audio_event(audio_chunk)
    await router.on_text_event(text_event)

    # Verify: should receive start and text, but NOT chunk (even with in_speech=True)
    assert len(ws.sent_messages) == 2
    assert ws.sent_messages[0]["event_type"] == "AUDIO_SPEECH_START"  # Enum value
    assert ws.sent_messages[1]["event_type"] == "TextEvent"  # Class name for TextEvent


@stage(Stage.PROTOTYPE, track_coverage=True)
@pytest.mark.asyncio
async def test_event_router_explicit_chunk_subscription():
    """Test explicit AudioChunkEvent subscription."""
    router = EventRouter()
    ws = MockWebSocket()

    await router.register_client(ws, {"all", "AudioChunkEvent"})

    # Send chunk with in_speech=True
    audio_chunk = AudioChunkEvent(
        source_id="test_source",
        stream_start_time=0.0,
        timestamp=0.1,
        data=np.array([0.1, 0.2, 0.3]),
        duration=0.01,
        sample_rate=16000,
        channels=1,
        blocksize=160,
        datatype="float32",
        in_speech=True
    )

    await router.on_audio_event(audio_chunk)

    # Should receive chunk because explicitly subscribed
    assert len(ws.sent_messages) == 1
    assert ws.sent_messages[0]["event_type"] == "AUDIO_CHUNK"  # Enum value


@stage(Stage.PROTOTYPE, track_coverage=True)
@pytest.mark.asyncio
async def test_event_router_in_speech_filtering():
    """Test that AudioChunkEvent with in_speech=False is filtered."""
    router = EventRouter()
    ws = MockWebSocket()

    await router.register_client(ws, {"all", "AudioChunkEvent"})

    # Send chunk with in_speech=False (silence)
    silence_chunk = AudioChunkEvent(
        source_id="test_source",
        stream_start_time=0.0,
        timestamp=0.1,
        data=np.array([0.01, 0.01, 0.01]),
        duration=0.01,
        sample_rate=16000,
        channels=1,
        blocksize=160,
        datatype="float32",
        in_speech=False
    )

    # Send chunk with in_speech=True (speech)
    speech_chunk = AudioChunkEvent(
        source_id="test_source",
        stream_start_time=0.0,
        timestamp=0.2,
        data=np.array([0.5, 0.6, 0.7]),
        duration=0.01,
        sample_rate=16000,
        channels=1,
        blocksize=160,
        datatype="float32",
        in_speech=True
    )

    await router.on_audio_event(silence_chunk)
    await router.on_audio_event(speech_chunk)

    # Should only receive speech chunk (silence filtered out)
    assert len(ws.sent_messages) == 1
    assert ws.sent_messages[0]["event_type"] == "AUDIO_CHUNK"  # Enum value
    assert ws.sent_messages[0]["in_speech"] is True


@stage(Stage.PROTOTYPE, track_coverage=True)
@pytest.mark.asyncio
async def test_event_router_specific_subscription():
    """Test specific event type subscription (not 'all')."""
    router = EventRouter()
    ws = MockWebSocket()

    await router.register_client(ws, {"TextEvent", "DraftStartEvent"})

    # Send various events
    text_event = TextEvent(
        text="test",
        audio_source_id="test_source",
        audio_start_time=0.0,
        audio_end_time=1.0
    )
    audio_start = AudioSpeechStartEvent(
        source_id="test_source",
        stream_start_time=0.0,
        timestamp=0.0,
        silence_period_ms=800,
        vad_threshold=0.5,
        sampling_rate=16000,
        speech_pad_ms=1500
    )

    await router.on_text_event(text_event)
    await router.on_audio_event(audio_start)

    # Should only receive TextEvent (subscribed), not AudioSpeechStartEvent
    assert len(ws.sent_messages) == 1
    assert ws.sent_messages[0]["event_type"] == "TextEvent"


@stage(Stage.PROTOTYPE, track_coverage=True)
@pytest.mark.asyncio
async def test_event_router_serialization_numpy():
    """Test event serialization with numpy arrays."""
    router = EventRouter()
    ws = MockWebSocket()

    await router.register_client(ws, {"AudioChunkEvent"})

    # Send chunk with numpy data
    chunk = AudioChunkEvent(
        source_id="test_source",
        stream_start_time=0.0,
        timestamp=0.1,
        data=np.array([0.1, 0.2, 0.3], dtype=np.float32),
        duration=0.01,
        sample_rate=16000,
        channels=1,
        blocksize=160,
        datatype="float32",
        in_speech=True
    )

    await router.on_audio_event(chunk)

    # Verify numpy array converted to list
    assert len(ws.sent_messages) == 1
    msg = ws.sent_messages[0]
    assert isinstance(msg["data"], list)
    assert msg["data"] == pytest.approx([0.1, 0.2, 0.3], abs=1e-5)


@stage(Stage.PROTOTYPE, track_coverage=True)
@pytest.mark.asyncio
async def test_event_router_serialization_nested_dataclass():
    """Test event serialization with nested dataclasses."""
    router = EventRouter()
    ws = MockWebSocket()

    await router.register_client(ws, {"DraftEndEvent"})

    # Create draft with nested structure
    start_mark = TextMark(start=0, end=11, text="open draft ")
    draft = Draft(
        start_text=start_mark,
        full_text="Test draft text"
    )

    end_event = DraftEndEvent(draft=draft)

    await router.on_draft_event(end_event)

    # Verify nested dataclass serialized correctly
    assert len(ws.sent_messages) == 1
    msg = ws.sent_messages[0]
    assert msg["event_type"] == "DraftEndEvent"
    assert isinstance(msg["draft"], dict)
    assert msg["draft"]["full_text"] == "Test draft text"


@stage(Stage.PROTOTYPE, track_coverage=True)
@pytest.mark.asyncio
async def test_event_router_multiple_clients():
    """Test routing to multiple clients with different subscriptions."""
    router = EventRouter()
    ws1 = MockWebSocket()
    ws2 = MockWebSocket()
    ws3 = MockWebSocket()

    await router.register_client(ws1, {"all"})
    await router.register_client(ws2, {"TextEvent"})
    await router.register_client(ws3, {"AudioSpeechStartEvent", "AudioSpeechStopEvent"})

    # Send events
    text_event = TextEvent(
        text="test",
        audio_source_id="test_source",
        audio_start_time=0.0,
        audio_end_time=1.0
    )
    audio_start = AudioSpeechStartEvent(
        source_id="test_source",
        stream_start_time=0.0,
        timestamp=0.0,
        silence_period_ms=800,
        vad_threshold=0.5,
        sampling_rate=16000,
        speech_pad_ms=1500
    )

    await router.on_text_event(text_event)
    await router.on_audio_event(audio_start)

    # ws1 (all): should get both
    assert len(ws1.sent_messages) == 2

    # ws2 (TextEvent only): should get text event
    assert len(ws2.sent_messages) == 1
    assert ws2.sent_messages[0]["event_type"] == "TextEvent"

    # ws3 (audio events only): should get audio start
    assert len(ws3.sent_messages) == 1
    assert ws3.sent_messages[0]["event_type"] == "AUDIO_SPEECH_START"  # Enum value


@stage(Stage.PROTOTYPE, track_coverage=True)
@pytest.mark.asyncio
async def test_event_router_dead_client_cleanup():
    """Test that failed sends remove dead clients."""
    router = EventRouter()
    ws_good = MockWebSocket()
    ws_bad = MockWebSocket()

    # Make ws_bad fail on send
    async def failing_send(data):
        raise Exception("Connection broken")

    ws_bad.send_json = failing_send

    await router.register_client(ws_good, {"all"})
    await router.register_client(ws_bad, {"all"})

    assert len(router.clients) == 2

    # Send event (should trigger cleanup of ws_bad)
    text_event = TextEvent(
        text="test",
        audio_source_id="test_source",
        audio_start_time=0.0,
        audio_end_time=1.0
    )

    await router.on_text_event(text_event)

    # ws_bad should be removed, ws_good should remain
    assert len(router.clients) == 1
    assert ws_good in router.clients
    assert ws_bad not in router.clients

    # ws_good should have received the message
    assert len(ws_good.sent_messages) == 1


@stage(Stage.PROTOTYPE, track_coverage=True)
def test_event_router_prebuffer_disabled():
    """Test EventRouter with pre_buffer_seconds=0 disables buffering."""
    router = EventRouter(pre_buffer_seconds=0)
    assert router._pre_buffer is None


@stage(Stage.PROTOTYPE, track_coverage=True)
def test_event_router_prebuffer_enabled():
    """Test EventRouter with pre_buffer_seconds>0 creates buffer."""
    router = EventRouter(pre_buffer_seconds=1.5)
    assert router._pre_buffer is not None
    assert router._pre_buffer.max_seconds == 1.5


@stage(Stage.PROTOTYPE, track_coverage=True)
@pytest.mark.asyncio
async def test_event_router_prebuffer_buffers_silence():
    """Test that silence chunks (in_speech=False) are buffered, not routed."""
    import time
    router = EventRouter(pre_buffer_seconds=1.0)
    ws = MockWebSocket()

    await router.register_client(ws, {"all", "AudioChunkEvent"})

    # Use realistic timestamp to avoid pruning in tests
    now = time.time()

    # Send silence chunk (in_speech=False)
    silence_chunk = AudioChunkEvent(
        source_id="test_source",
        stream_start_time=now,
        timestamp=now,
        data=np.array([0.01, 0.01, 0.01]),
        duration=0.01,
        sample_rate=16000,
        channels=1,
        blocksize=160,
        datatype="float32",
        in_speech=False
    )

    await router.on_audio_event(silence_chunk)

    # Should be buffered, not sent to client
    assert len(ws.sent_messages) == 0
    assert router._pre_buffer.has_data()
    assert len(router._pre_buffer.buffer) == 1


@stage(Stage.PROTOTYPE, track_coverage=True)
@pytest.mark.asyncio
async def test_event_router_prebuffer_emits_before_speech_start():
    """Test buffered chunks emitted before AudioSpeechStartEvent."""
    import time
    router = EventRouter(pre_buffer_seconds=1.0)
    ws = MockWebSocket()

    await router.register_client(ws, {"all", "AudioChunkEvent"})

    now = time.time()

    # Send 3 silence chunks
    for i in range(3):
        silence_chunk = AudioChunkEvent(
            source_id="test_source",
            stream_start_time=now,
            timestamp=now + i * 0.01,
            data=np.array([0.01, 0.01, 0.01]),
            duration=0.01,
            sample_rate=16000,
            channels=1,
            blocksize=160,
            datatype="float32",
            in_speech=False
        )
        await router.on_audio_event(silence_chunk)

    # Verify buffered, not sent
    assert len(ws.sent_messages) == 0
    assert len(router._pre_buffer.buffer) == 3

    # Send speech start event
    speech_start = AudioSpeechStartEvent(
        source_id="test_source",
        stream_start_time=now,
        timestamp=now + 0.3,
        silence_period_ms=800,
        vad_threshold=0.5,
        sampling_rate=16000,
        speech_pad_ms=1500
    )

    await router.on_audio_event(speech_start)

    # Should have received: 3 buffered chunks + speech start = 4 messages
    assert len(ws.sent_messages) == 4
    # First 3 are the buffered chunks
    assert ws.sent_messages[0]["event_type"] == "AUDIO_CHUNK"
    assert ws.sent_messages[1]["event_type"] == "AUDIO_CHUNK"
    assert ws.sent_messages[2]["event_type"] == "AUDIO_CHUNK"
    # Last is the speech start event
    assert ws.sent_messages[3]["event_type"] == "AUDIO_SPEECH_START"

    # Buffer should be cleared
    assert not router._pre_buffer.has_data()


@stage(Stage.PROTOTYPE, track_coverage=True)
@pytest.mark.asyncio
async def test_event_router_prebuffer_cleared_after_emission():
    """Test buffer is cleared after emission to prevent duplicates."""
    import time
    router = EventRouter(pre_buffer_seconds=1.0)
    ws = MockWebSocket()

    await router.register_client(ws, {"all", "AudioChunkEvent"})

    now = time.time()

    # Send silence chunk
    silence_chunk = AudioChunkEvent(
        source_id="test_source",
        stream_start_time=now,
        timestamp=now,
        data=np.array([0.01, 0.01, 0.01]),
        duration=0.01,
        sample_rate=16000,
        channels=1,
        blocksize=160,
        datatype="float32",
        in_speech=False
    )
    await router.on_audio_event(silence_chunk)

    # Send first speech start
    speech_start1 = AudioSpeechStartEvent(
        source_id="test_source",
        stream_start_time=now,
        timestamp=now + 0.1,
        silence_period_ms=800,
        vad_threshold=0.5,
        sampling_rate=16000,
        speech_pad_ms=1500
    )
    await router.on_audio_event(speech_start1)

    # Should have 2 messages (1 buffered chunk + speech start)
    assert len(ws.sent_messages) == 2
    assert not router._pre_buffer.has_data()

    # Send another silence chunk
    silence_chunk2 = AudioChunkEvent(
        source_id="test_source",
        stream_start_time=now,
        timestamp=now + 0.2,
        data=np.array([0.01, 0.01, 0.01]),
        duration=0.01,
        sample_rate=16000,
        channels=1,
        blocksize=160,
        datatype="float32",
        in_speech=False
    )
    await router.on_audio_event(silence_chunk2)

    # Send second speech start
    speech_start2 = AudioSpeechStartEvent(
        source_id="test_source",
        stream_start_time=now,
        timestamp=now + 0.3,
        silence_period_ms=800,
        vad_threshold=0.5,
        sampling_rate=16000,
        speech_pad_ms=1500
    )
    await router.on_audio_event(speech_start2)

    # Should have 4 messages total (2 from first segment, 2 from second)
    # NOT 5 (which would mean first chunk sent twice)
    assert len(ws.sent_messages) == 4
    assert ws.sent_messages[2]["event_type"] == "AUDIO_CHUNK"  # Second buffered chunk
    assert ws.sent_messages[3]["event_type"] == "AUDIO_SPEECH_START"  # Second speech start


@stage(Stage.PROTOTYPE, track_coverage=True)
@pytest.mark.asyncio
async def test_event_router_prebuffer_no_buffer_when_disabled():
    """Test no buffering occurs when pre_buffer_seconds=0."""
    router = EventRouter(pre_buffer_seconds=0)
    ws = MockWebSocket()

    await router.register_client(ws, {"all", "AudioChunkEvent"})

    # Send silence chunk (should be ignored, not buffered or sent)
    silence_chunk = AudioChunkEvent(
        source_id="test_source",
        stream_start_time=0.0,
        timestamp=0.1,
        data=np.array([0.01, 0.01, 0.01]),
        duration=0.01,
        sample_rate=16000,
        channels=1,
        blocksize=160,
        datatype="float32",
        in_speech=False
    )
    await router.on_audio_event(silence_chunk)

    # Should not be buffered (no buffer) or sent (silence filtered)
    assert len(ws.sent_messages) == 0

    # Send speech start
    speech_start = AudioSpeechStartEvent(
        source_id="test_source",
        stream_start_time=0.0,
        timestamp=0.2,
        silence_period_ms=800,
        vad_threshold=0.5,
        sampling_rate=16000,
        speech_pad_ms=1500
    )
    await router.on_audio_event(speech_start)

    # Should only get speech start, no buffered chunks
    assert len(ws.sent_messages) == 1
    assert ws.sent_messages[0]["event_type"] == "AUDIO_SPEECH_START"


@stage(Stage.PROTOTYPE, track_coverage=True)
@pytest.mark.asyncio
async def test_event_router_author_uri_disabled():
    """Test that author_uri is None when server_uri not configured (Story 007)."""
    router = EventRouter()  # No server_uri
    ws = MockWebSocket()
    await router.register_client(ws, {"all"})

    # Send various events
    audio_event = AudioSpeechStartEvent(
        source_id="test_source",
        stream_start_time=0.0,
        timestamp=0.0,
        silence_period_ms=300,
        vad_threshold=0.5,
        sampling_rate=16000,
        speech_pad_ms=300
    )
    await router.on_audio_event(audio_event)

    text_event = TextEvent(text="test")
    await router.on_text_event(text_event)

    draft = Draft(start_text=TextMark(0, 10, "test start"))
    draft_event = DraftStartEvent(draft=draft)
    await router.on_draft_event(draft_event)

    # All events should have author_uri = None in serialized output
    assert len(ws.sent_messages) == 3
    for msg in ws.sent_messages:
        assert msg.get("author_uri") is None


@stage(Stage.PROTOTYPE, track_coverage=True)
@pytest.mark.asyncio
async def test_event_router_author_uri_enabled():
    """Test that author_uri is stamped when server_uri configured (Story 007)."""
    server_uri = "http://192.168.100.213:8000"
    router = EventRouter(server_uri=server_uri)
    ws = MockWebSocket()
    await router.register_client(ws, {"all"})

    # Send AudioEvent (should get audio/v1)
    audio_event = AudioSpeechStartEvent(
        source_id="test_source",
        stream_start_time=0.0,
        timestamp=0.0,
        silence_period_ms=300,
        vad_threshold=0.5,
        sampling_rate=16000,
        speech_pad_ms=300
    )
    await router.on_audio_event(audio_event)

    # Send TextEvent (should get transcription/v1)
    text_event = TextEvent(text="test transcription")
    await router.on_text_event(text_event)

    # Send DraftEvent (should get drafts/v1)
    draft = Draft(start_text=TextMark(0, 10, "test start"))
    draft_event = DraftStartEvent(draft=draft)
    await router.on_draft_event(draft_event)

    # Verify author_uri was stamped correctly
    assert len(ws.sent_messages) == 3

    audio_msg = ws.sent_messages[0]
    assert audio_msg["event_type"] == "AUDIO_SPEECH_START"
    assert audio_msg["author_uri"] == f"{server_uri}/audio/v1"

    text_msg = ws.sent_messages[1]
    assert text_msg["event_type"] == "TextEvent"
    assert text_msg["author_uri"] == f"{server_uri}/transcription/v1"

    draft_msg = ws.sent_messages[2]
    assert draft_msg["event_type"] == "DraftStartEvent"
    assert draft_msg["author_uri"] == f"{server_uri}/drafts/v1"


@stage(Stage.PROTOTYPE, track_coverage=True)
@pytest.mark.asyncio
async def test_event_router_get_service_for_event():
    """Test _get_service_for_event helper method (Story 007)."""
    router = EventRouter()

    # Test AudioEvent → audio
    audio_event = AudioSpeechStartEvent(
        source_id="test",
        stream_start_time=0.0,
        silence_period_ms=300,
        vad_threshold=0.5,
        sampling_rate=16000,
        speech_pad_ms=300
    )
    assert router._get_service_for_event(audio_event) == "audio"

    # Test TextEvent → transcription
    text_event = TextEvent(text="test")
    assert router._get_service_for_event(text_event) == "transcription"

    # Test DraftEvent → drafts
    draft = Draft(start_text=TextMark(0, 10, "test"))
    draft_event = DraftStartEvent(draft=draft)
    assert router._get_service_for_event(draft_event) == "drafts"


@stage(Stage.PROTOTYPE, track_coverage=True)
@pytest.mark.asyncio
async def test_event_router_stamp_author_uri():
    """Test _stamp_author_uri helper method (Story 007)."""
    server_uri = "http://192.168.100.213:8000"
    router = EventRouter(server_uri=server_uri)

    # Test stamping AudioEvent
    audio_event = AudioSpeechStartEvent(
        source_id="test",
        stream_start_time=0.0,
        silence_period_ms=300,
        vad_threshold=0.5,
        sampling_rate=16000,
        speech_pad_ms=300
    )
    assert audio_event.author_uri is None
    router._stamp_author_uri(audio_event)
    assert audio_event.author_uri == f"{server_uri}/audio/v1"

    # Test stamping TextEvent
    text_event = TextEvent(text="test")
    assert text_event.author_uri is None
    router._stamp_author_uri(text_event)
    assert text_event.author_uri == f"{server_uri}/transcription/v1"

    # Test stamping DraftEvent
    draft = Draft(start_text=TextMark(0, 10, "test"))
    draft_event = DraftStartEvent(draft=draft)
    assert draft_event.author_uri is None
    router._stamp_author_uri(draft_event)
    assert draft_event.author_uri == f"{server_uri}/drafts/v1"


@stage(Stage.PROTOTYPE, track_coverage=True)
@pytest.mark.asyncio
async def test_event_router_author_uri_backward_compatible():
    """Test backward compatibility: server_uri=None works as before (Story 007)."""
    router = EventRouter(server_uri=None)
    ws = MockWebSocket()
    await router.register_client(ws, {"all"})

    # Send event
    text_event = TextEvent(text="backward compatible test")
    await router.on_text_event(text_event)

    # Should work without errors, author_uri=None
    assert len(ws.sent_messages) == 1
    assert ws.sent_messages[0]["author_uri"] is None
