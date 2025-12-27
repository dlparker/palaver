# Future Tests

This file documents test ideas that were deferred or planned but not yet implemented.

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
