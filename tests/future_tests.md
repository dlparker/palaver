# Future Tests

This file documents test ideas that were deferred or planned but not yet implemented.

---

## EventRouter Pre-Buffer Integration Tests (Story 005, Tasks 5-6 - Deferred)

**Original Task IDs**: palaver-xkb, palaver-daj
**Status**: Deferred - comprehensive unit tests provide adequate coverage for Prototype stage
**Stage**: Prototype

### Task 5: Integration Test with Event Streaming (palaver-xkb)

#### Overview

Integration test combining EventRouter pre-buffering with websocket client and event streaming protocol.

#### Test Scope

1. **Setup**
   - Create EventNetServer with pre_buffer_seconds=1.0
   - Add event streaming router
   - Connect websocket test client

2. **Pre-Buffer Testing**
   - Send AudioChunkEvents with in_speech=False (silence)
   - Verify silence chunks NOT received by client (buffered)
   - Send AudioSpeechStartEvent
   - Verify client receives: buffered silence chunks → speech start event
   - Verify correct chronological order

3. **Multiple Speech Segments**
   - Send silence → speech start → silence → speech start
   - Verify buffer cleared between segments
   - Verify no duplicate chunks

4. **Disabled Pre-Buffer**
   - Create EventRouter with pre_buffer_seconds=0
   - Verify silence chunks filtered out (not buffered or sent)
   - Verify speech start arrives without pre-buffer

#### Why Deferred

- Comprehensive unit tests already cover all pre-buffer logic (96% EventRouter coverage)
- Unit tests verify:
  - Buffer creation/disabled states
  - Silence buffering behavior
  - Emission order (buffered → speech start)
  - Buffer clearing
  - force_send mechanism
- Integration adds HTTP/websocket layer complexity without testing new logic
- TestClient websocket limitations (synchronous, timing issues)

#### When to Implement

Consider implementing when:
- Promoting to MVP stage (need higher integration confidence)
- Bugs found in websocket interaction with pre-buffering
- Performance issues with buffer emission timing
- Issues with concurrent clients receiving buffered events
- Need to validate buffer behavior with actual VAD timing

### Task 6: Manual Verification with Live Server (palaver-daj)

#### Overview

Manual testing with live server, real audio input, and websocket client to verify pre-buffering improves speech capture quality.

#### Test Procedure

1. **Setup Live Server**
   ```bash
   # Start server with pre-buffering enabled (default)
   uv run scripts/server.py --model models/ggml-base.en.bin
   ```

2. **Connect WebSocket Client**
   ```bash
   # Use existing test client
   uv run scripts/test_client.py --subscribe all,AudioChunkEvent
   ```

3. **Test Speech Capture**
   - Speak into microphone with short phrases
   - Observe AudioChunkEvents received before AudioSpeechStartEvent
   - Note if initial syllables are captured (vs cut off)

4. **Compare With/Without Pre-Buffer**

   **With pre-buffering (pre_buffer_seconds=1.0):**
   - Expected: Client receives ~1 second of silence before speech
   - Expected: Initial syllables fully captured
   - Expected: Improved transcription accuracy

   **Without pre-buffering (pre_buffer_seconds=0):**
   - Expected: Client receives AudioSpeechStartEvent immediately
   - Expected: Initial syllables may be cut off
   - Expected: Transcription may miss first words

5. **Verify Transcription Quality**
   - Compare Whisper transcription results with/without pre-buffering
   - Document improvement in capturing speech starts
   - Note any edge cases or timing issues

#### Why Deferred

- Core pre-buffer functionality proven with unit tests
- WhisperWrapper already uses identical AudioRingBuffer pattern successfully
- No server deployment yet to test against
- Manual testing requires actual microphone and audio setup
- Transcription quality comparison requires baseline data

#### When to Implement

Implement when:
- Deploying server for actual use
- Reports of speech start being cut off
- Comparing transcription quality with baseline
- Tuning pre_buffer_seconds duration (default 1.0s)
- Testing with different VAD configurations
- Need empirical data for pre-buffer effectiveness

#### Expected Results

Based on WhisperWrapper experience (which uses same AudioRingBuffer pattern):
- Pre-buffering should capture 200-300ms of audio before VAD triggers
- This compensates for VAD latency (detection ~200-300ms after speech starts)
- Initial syllables and words should be fully captured
- Transcription quality should improve, especially for short phrases
- No noticeable latency increase for end users

---

## Full Server Integration Test (Story 004, Task 4 - Deferred)

**Original Task ID**: palaver-olk
**Status**: Deferred - current coverage (83%) adequate for Prototype stage
**Stage**: Prototype

### Overview

End-to-end integration test for complete EventNetServer with both routers (events + status), mock audio input, and draft recording verification.

### Test Scope

1. **Server Setup**
   - Create EventNetServer instance with real model path
   - Add both routers: `create_event_router()` and `create_status_router()`
   - Configure draft directory for recording

2. **Mock Audio Input** (pattern from `test_mic_mock_to_text.py`)
   - Use MockStream to simulate microphone input
   - Feed audio file through mocked MicListener
   - Ensure VAD triggers speech detection

3. **Websocket Client**
   - Connect websocket client to `/events` endpoint
   - Subscribe to event types: `["all"]` or specific types
   - Verify events flow through websocket as audio is processed

4. **Draft Completion Monitoring** (pattern from `test_file_audio_to_text.py`)
   - Monitor for `DraftEndEvent`
   - Verify draft file created in draft directory
   - Check draft contains expected transcription

5. **Status Endpoint Verification**
   - Poll `/status` endpoint during pipeline operation
   - Verify `pipeline_active: true`
   - Verify `connected_clients: 1` (websocket client registered)
   - Verify `draft_recording: true` (draft_dir configured)

6. **Graceful Shutdown**
   - Close websocket connection
   - Verify client unregistered (client count → 0)
   - Shutdown EventNetServer
   - Verify pipeline cleanup

### Test Pattern Example

```python
@stage(Stage.PROTOTYPE, track_coverage=True)
@pytest.mark.asyncio
async def test_full_server_integration_with_mock_audio():
    """Full integration: EventNetServer + routers + mock audio → draft."""

    # Setup
    model_path = Path("models/ggml-base.en.bin")
    draft_dir = Path(tmpdir) / "drafts"
    draft_dir.mkdir()

    # Create server with both routers
    server = EventNetServer(
        model_path=model_path,
        draft_dir=draft_dir,
        host="127.0.0.1",
        port=8765
    )
    server.add_router(create_event_router(server))
    server.add_router(create_status_router(server))

    # Start server in background
    async with server:
        # Connect websocket client
        async with websockets.connect("ws://127.0.0.1:8765/events") as ws:
            await ws.send(json.dumps({"subscribe": ["all"]}))

            # Feed mock audio (pattern from test_mic_mock_to_text.py)
            # ... MockStream setup ...

            # Collect events until DraftEndEvent
            draft_complete = False
            while not draft_complete:
                event = await ws.recv()
                event_data = json.loads(event)
                if event_data["event_type"] == "DraftEndEvent":
                    draft_complete = True

            # Verify status endpoint during operation
            async with httpx.AsyncClient() as client:
                response = await client.get("http://127.0.0.1:8765/status")
                status = response.json()
                assert status["pipeline_active"] is True
                assert status["connected_clients"] == 1
                assert status["draft_recording"] is True

        # After websocket close, verify client unregistered
        async with httpx.AsyncClient() as client:
            response = await client.get("http://127.0.0.1:8765/status")
            assert response.json()["connected_clients"] == 0

    # Verify draft file created
    drafts = list(draft_dir.glob("*.txt"))
    assert len(drafts) > 0
```

### Why Deferred

- Current test coverage (83%) adequate for Prototype stage
- Core components thoroughly tested in isolation:
  - EventRouter: 95% coverage (test_event_router.py)
  - Status router: 100% coverage (test_status_router.py)
  - Events router: 93% coverage (test_event_streaming.py)
- Integration patterns validated through existing tests
- Full end-to-end test complex with async websockets + TestClient limitations

### When to Implement

Consider implementing when:
- Promoting to MVP stage (need higher confidence in integration)
- Bugs found in server startup/shutdown sequence
- Issues with router interaction during pipeline operation
- Need to validate draft recording in server context
- Adding new routers that might interfere with existing functionality

### Dependencies

- Existing test patterns: `test_mic_mock_to_text.py`, `test_file_audio_to_text.py`
- WebSocket client library: `websockets` or `httpx` with websocket support
- AsyncIO test fixtures for server lifecycle management
