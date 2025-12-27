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
