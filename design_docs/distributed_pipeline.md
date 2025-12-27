# Distributed Pipeline & Web UI

**Status**: Design Phase
**Date**: 2025-12-26

## Overview

Add WebSocket-based event forwarding and FastAPI web UI to enable distributed pipeline processing across multiple machines. This allows specialized hardware to handle different pipeline stages while maintaining event-driven architecture.

## Motivation

### Current State
- Pipeline runs on single machine with microphone
- All processing (VAD, transcription, LLM) happens locally
- No visibility into draft state or pipeline configuration
- Optimized for responsiveness, not quality

### Desired State
- **Distributed processing**: Different machines handle different stages
- **Quality vs. Speed tradeoff**: Quick local transcription + high-quality re-processing
- **Web-based visibility**: See configuration, track drafts through pipeline
- **Event forwarding**: Any pipeline component can run anywhere
- **Future-ready**: Foundation for categorization, filing, document management

## Architecture

### Core Concept: Event Broadcasting via WebSocket

```
┌─────────────────────────────────────────────────────────────┐
│                     FastAPI Server                          │
│  - WebSocket event hub                                      │
│  - Web UI endpoints                                         │
│  - Configuration management                                 │
└─────────────────────────────────────────────────────────────┘
        ↕ WebSocket               ↕ WebSocket           ↕ WebSocket
┌──────────────────┐      ┌──────────────────┐    ┌──────────────────┐
│   Machine 1      │      │   Machine 2      │    │   Web Browser    │
│   (Microphone)   │      │   (GPU Server)   │    │   (UI)           │
│                  │      │                  │    │                  │
│ - MicListener    │      │ - FileListener   │    │ - Draft viewer   │
│ - VAD            │      │ - Whisper (large)│    │ - Approval UI    │
│ - Whisper (tiny) │      │ - LLM client     │    │ - Config viewer  │
│ - Emit events → │      │ - Listen events ←│    │ - Status dash    │
└──────────────────┘      └──────────────────┘    └──────────────────┘
```

### Event Flow Examples

#### Example 1: Multi-Stage Transcription

```
Machine 1 (Laptop with mic):
1. User speaks → MicListener captures audio
2. VAD detects speech → AudioSpeechStart/Stop
3. Whisper (tiny, fast) → TextEvent
4. DraftMaker → DraftEndEvent
5. SQLDraftRecorder saves → draft.wav + draft.db
6. → Emit DraftEndEvent via WebSocket

Machine 2 (Desktop with GPU):
7. ← Receive DraftEndEvent via WebSocket
8. Load draft.wav from shared storage
9. Whisper (large, slow) → Better transcription
10. Create revised Draft with improved text
11. → Emit DraftRevisionEvent via WebSocket

Either Machine 1 or 2:
12. ← Receive DraftRevisionEvent
13. Send to LLM → DraftChangeEvent
14. → Emit DraftChangeEvent via WebSocket

Browser UI:
15. ← Receive DraftChangeEvent
16. Display suggestions for user approval
17. User approves → Send approval message
18. → Create/emit DraftRevisionEvent (final)
```

#### Example 2: Voice Command Categorization

```
Machine 1:
- "Rupert, this is a todo item" → Draft with category hint

Machine 2:
- LLM analyzes draft → Suggests category: "todo"
- Emits DraftChangeEvent with category suggestion

Browser UI:
- User approves category
- Draft filed into appropriate system (org-mode, task tracker, etc.)
```

## Technical Design

### FastAPI Server Components

#### 1. WebSocket Event Hub

```python
# Pseudo-code structure

class EventHub:
    """Central WebSocket hub for event broadcasting

    Simple in-memory event hub for private network use.
    No authentication, no persistence - events are replayed from SQLDraftRecorder.
    """

    def __init__(self):
        self.connections: list[WebSocket] = []
        self.event_history: deque[DraftEvent] = deque(maxlen=100)  # Keep last 100 events

    async def connect(self, websocket: WebSocket):
        """Register new WebSocket client (no auth required)"""
        await websocket.accept()
        self.connections.append(websocket)
        # Send recent event history for context
        for event in self.event_history:
            await websocket.send_json(serialize_event(event))

    async def broadcast(self, event: DraftEvent):
        """Broadcast event to all connected clients"""
        self.event_history.append(event)
        event_json = serialize_event(event)
        # Simple broadcast - no ACK, no retry
        for connection in self.connections:
            try:
                await connection.send_json(event_json)
            except Exception:
                # Client disconnected, will be cleaned up later
                pass

    async def receive(self, websocket: WebSocket):
        """Receive events from clients and broadcast"""
        while True:
            data = await websocket.receive_json()
            event = deserialize_event(data)
            await self.broadcast(event)
```

#### 2. Pipeline WebSocket Client

```python
class PipelineEventForwarder(DraftEventListener):
    """Forwards pipeline events to WebSocket server"""

    def __init__(self, websocket_url: str):
        self.websocket_url = websocket_url
        self.ws = None

    async def connect(self):
        """Connect to WebSocket server"""
        self.ws = await websockets.connect(self.websocket_url)
        # Start listener task for incoming events
        asyncio.create_task(self._listen())

    async def on_draft_event(self, event: DraftEvent):
        """Forward local events to server"""
        await self.ws.send(serialize_event(event))

    async def _listen(self):
        """Listen for remote events and emit locally"""
        async for message in self.ws:
            event = deserialize_event(message)
            # Emit to local pipeline listeners
            await self.emit_to_local_listeners(event)
```

#### 3. Web UI Endpoints

```python
# FastAPI routes

@app.get("/")
async def root():
    """Serve main UI"""
    return FileResponse("static/index.html")

@app.websocket("/ws/events")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for event stream"""
    await event_hub.connect(websocket)
    try:
        await event_hub.receive(websocket)
    except WebSocketDisconnect:
        event_hub.disconnect(websocket)

@app.get("/api/drafts")
async def list_drafts():
    """List all drafts across all recorders"""
    # Query all known SQLDraftRecorder databases
    return {"drafts": [...]}

@app.get("/api/drafts/{draft_id}")
async def get_draft(draft_id: str):
    """Get specific draft by UUID"""
    return {"draft": {...}}

@app.post("/api/drafts/{draft_id}/approve")
async def approve_changes(draft_id: str, approved: list[int]):
    """User approves suggested changes"""
    # Create DraftRevisionEvent
    # Broadcast via WebSocket
    return {"status": "approved"}

@app.get("/api/config")
async def get_config():
    """Get current pipeline configuration"""
    return {
        "machines": [...],
        "pipelines": [...],
        "recorders": [...]
    }
```

### Event Serialization

Need to serialize/deserialize dataclasses for WebSocket transport. Handle enums (RevisionSource) properly:

```python
import json
from dataclasses import asdict, is_dataclass
from palaver.scribe.draft_events import *

def serialize_event(event: DraftEvent) -> dict:
    """Convert event to JSON-serializable dict"""
    data = asdict(event)

    # Handle enums
    if isinstance(event, DraftRevisionEvent):
        data['revision_source'] = event.revision_source.value

    return {
        "event_type": event.__class__.__name__,
        "data": data
    }

def deserialize_event(data: dict) -> DraftEvent:
    """Reconstruct event from dict"""
    event_type = data["event_type"]
    event_data = data["data"]

    # Map class names to classes
    event_classes = {
        "DraftStartEvent": DraftStartEvent,
        "DraftEndEvent": DraftEndEvent,
        "DraftChangeEvent": DraftChangeEvent,
        "DraftRevisionEvent": DraftRevisionEvent,
    }

    # Handle enums
    if event_type == "DraftRevisionEvent":
        event_data['revision_source'] = RevisionSource(event_data['revision_source'])

    event_class = event_classes[event_type]
    return event_class(**event_data)
```

### Shared Storage

**Current approach (Phase 1)**: Network file share
- Mount same path on all machines (e.g., `/mnt/palaver_drafts`)
- Simple, low overhead, works great for 2-3 machines
- Example setup:
  ```bash
  # On desktop (NFS server)
  sudo exportfs -o rw,sync,no_subtree_check 192.168.1.0/24:/home/user/palaver_drafts

  # On laptop (NFS client)
  sudo mount 192.168.1.100:/home/user/palaver_drafts /mnt/palaver_drafts
  ```

**Future approach (Phase 2)**: HTTP file streaming
- Add FastAPI endpoint: `/api/files/{draft_id}/audio`
- System of record streams to interested parties
- Reduces sysadmin burden as system scales
- Enables web UI to play audio without file share

## Implementation Phases

### Phase 1: FastAPI Foundation
- [ ] Add FastAPI, uvicorn to dependencies
- [ ] Basic server with WebSocket endpoint
- [ ] Event serialization/deserialization
- [ ] Simple test: forward DraftEndEvent across WebSocket

### Phase 2: Pipeline Integration
- [ ] PipelineEventForwarder component
- [ ] Update SQLDraftRecorder to broadcast events
- [ ] Test: Run mic_to_text.py, see events in WebSocket

### Phase 3: Basic Web UI
- [ ] Simple HTML/JS dashboard
- [ ] Live event stream viewer
- [ ] Draft list with status
- [ ] Configuration display

### Phase 4: Approval Workflow
- [ ] DraftChangeEvent display in UI
- [ ] Checkbox UI for suggestion approval
- [ ] Emit DraftRevisionEvent on approval
- [ ] Apply changes and update display

### Phase 5: Multi-Machine Setup
- [ ] Configuration system for distributed setup
- [ ] Shared storage setup (NFS or MinIO)
- [ ] Test: Mic on laptop, LLM on desktop
- [ ] Documentation for distributed setup

### Phase 6: Advanced Features
- [ ] Draft categorization (LLM-assisted)
- [ ] Category-specific filing
- [ ] Search and filtering
- [ ] Export to org-mode, markdown, etc.
- [ ] Voice command workflow (e.g., "file this as a todo")

## Configuration

Example distributed setup config:

```yaml
# config.yaml

websocket_server:
  host: "192.168.1.100"  # Desktop
  port: 8000

shared_storage:
  type: "nfs"
  mount: "/mnt/palaver_drafts"

machines:
  laptop:
    role: "capture"
    components:
      - mic_listener
      - vad_filter
      - whisper_quick:
          model: "tiny.en"
      - draft_maker
      - sql_recorder
      - event_forwarder

  desktop:
    role: "processing"
    components:
      - event_receiver
      - whisper_quality:
          model: "large-v3"
          trigger: "DraftEndEvent"
      - llm_client:
          model: "llama3.1:8b"
          trigger: "DraftRevisionEvent"
      - event_forwarder

  browser:
    role: "ui"
    url: "http://192.168.1.100:8000"
```

## Design Decisions

### Scope & Scale
- **Private network only**: All machines on private network, no internet-facing deployment
- **Single user**: No multi-user concerns, simpler UX acceptable
- **2-3 machines initially**: Laptop, desktop, maybe tablet
- **No authentication needed**: Trust all machines on private network

### Event Replay
- **Limited replay is acceptable**: Only DraftEvent family needs replay
- **Replay from DB**: SQLDraftRecorder is source of truth for drafts
- **WebSocket history**: Keep recent events in memory (last 100?) for new clients
- **No persistence in WebSocket server**: Events persist in SQLite via recorders

### Revision Conflict Resolution
- **Priority system**: HUMAN > LLM > WHISPER_REPROCESS > UNKNOWN
- **RevisionSource enum**: Every DraftRevisionEvent declares its source
- **Simple heuristic**: When multiple revisions exist, keep highest priority
- **Human always wins**: Manual edits override any automated revision

### File Storage
- **Phase 1 (now)**: Network file share with same paths on all machines
  - Example: `/mnt/palaver_drafts` mounted on all machines
  - Simple, low overhead, works for 2-3 machines
- **Phase 2 (later)**: HTTP streaming via FastAPI
  - Add `/api/files/{draft_id}/audio` endpoint
  - System of record streams to interested parties
  - Reduces sysadmin burden as scale increases

## Future Vision

Once distributed pipeline + web UI are working:

1. **Voice-driven workflow**:
   - "Rupert, new todo" → Categorized as task
   - "Rupert, meeting notes" → Categorized as notes
   - "Rupert, email to John" → Filed for email composition

2. **Smart categorization**:
   - LLM analyzes content + voice hints
   - Suggests category and metadata
   - Auto-files approved drafts

3. **Integration with existing tools**:
   - Export to org-mode for TODOs
   - Create Jira tickets from voice
   - Draft emails in Gmail
   - Add calendar events

4. **Continuous improvement**:
   - Track which suggestions are approved/rejected
   - Fine-tune prompts based on user patterns
   - Learn user's categorization preferences

## References

- Event-driven architecture: See CLAUDE.md
- Draft events: `src/palaver/scribe/draft_events.py`
- Existing recorders: `src/palaver/scribe/recorders/sql_drafts.py`
- FastAPI WebSocket docs: https://fastapi.tiangolo.com/advanced/websockets/
