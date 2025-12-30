# VTT Server Modes

The EventNetServer (FastAPI-based) supports three operational modes for handling audio transcription pipelines. Each mode provides different capabilities for audio input and event processing.

## Overview

The server modes are defined by the `ServerMode` enum and configured at server instantiation:

- **Direct Mode**: Local audio input with event streaming
- **Remote Mode**: Remote audio input via WebSocket with event streaming
- **Rescan Mode**: Remote event input with buffered re-transcription (IN PROGRESS)

All modes:
- Run the standard audio processing pipeline (Downsampler → VAD → Whisper → Command Detection)
- Offer event streaming via WebSocket endpoints
- Support draft recording via `SQLDraftRecorder`

## Direct Mode

**Purpose**: Process local audio sources and stream events to remote clients.

### Architecture

```
Local Audio Source (MicListener/FileListener)
    ↓
Standard Pipeline (Downsampler → VAD → Whisper → Commands)
    ↓
Event Streaming (WebSocket)
    ↓
Remote Clients
```

### Characteristics

- Audio source: `MicListener` or `FileListener` configured locally
- Pipeline: Full local transcription pipeline
- WebSocket output: Streams all pipeline events (AudioEvents, TextEvents, DraftEvents, etc.)
- Primary use case: Running transcription server on a local machine with microphone

### Configuration Example

```python
audio_listener = MicListener(chunk_duration=0.03)
server = EventNetServer(
    audio_listener,
    pipeline_config=pipeline_config,
    draft_recorder=draft_recorder,
    port=8000,
    mode=ServerMode.direct
)
```

### Verification Status

✓ Basic verification completed using `scripts/vtt_server.py`

## Remote Mode

**Purpose**: Accept audio from remote sources and stream transcription events back.

### Architecture

```
Remote Client
    ↓ (sends AudioEvents via WebSocket)
NetListener (receives AudioEvents)
    ↓
Standard Pipeline (Downsampler → VAD → Whisper → Commands)
    ↓
Event Streaming (WebSocket)
    ↓
Remote Clients (may include original sender)
```

### Characteristics

- Audio source: `NetListener` configured to receive AudioEvents via WebSocket
- Pipeline: Full local transcription pipeline
- WebSocket input: Accepts `AudioEvent` instances (AudioStartEvent, AudioChunkEvent, etc.)
- WebSocket output: Streams all pipeline events
- Primary use case: Separation of audio hardware host from transcription host

### Key Differences from Direct Mode

- Uses `NetListener` instead of `MicListener`
- Audio originates from remote client, not local hardware
- Enables separation of audio capture and transcription processing

### Configuration Example

```python
audio_listener = NetListener(audio_url, chunk_duration=0.03)
server = EventNetServer(
    audio_listener,
    pipeline_config=pipeline_config,
    draft_recorder=draft_recorder,
    port=8000,
    mode=ServerMode.remote
)
```

### Verification Status

✓ Basic verification completed using `scripts/vtt_server.py`

## Rescan Mode (PHASE 1 COMPLETE)

**Purpose**: Re-transcribe audio segments with higher-quality models after initial draft completion.

### Architecture

```
Remote Client (running Direct/Remote mode server)
    ↓ (sends ALL event types via WebSocket)
NetListener (receives all event types)
    ↓
Rescanner (buffers audio, responds to DraftEvents)
    ↓ (feeds buffered + live audio)
Standard Pipeline (with high-quality model config)
    ↓ (local events)
RescannerLocal (adapter for local pipeline events)
    ↓
Rescanner.on_local_* methods
    ↓
DraftRescanEvent generation
    ↓
Event Streaming (WebSocket)
```

### Characteristics

- Audio source: `NetListener` configured to receive ALL event types (AudioEvents, TextEvents, DraftEvents)
- Special component: `Rescanner` wraps NetListener and acts as audio listener for the pipeline
- Pipeline: Configured with higher-quality Whisper model and extended VAD parameters
- Buffering: Uses `AudioRingBuffer` (30 second rolling window) to maintain audio history
- Event-driven triggering: Responds to `DraftStartEvent` and `DraftEndEvent` from remote source
- Dual event streams: Processes remote events AND local pipeline events separately

### Rescanner Behavior

The `Rescanner` component (src/palaver/fastapi/event_server.py:130-264) manages two operational states:

#### 1. Buffering State (No Active Draft)

- Collects incoming `AudioChunkEvent` instances into `AudioRingBuffer` (30 second window)
- Ring buffer maintains rolling window of recent audio
- Does NOT feed events to local pipeline
- Waits for `DraftStartEvent` from remote to trigger processing

#### 2. Processing State (Active Draft)

**Triggered by remote `DraftStartEvent`:**

1. Examines `draft.audio_start_time` from the remote draft
2. Extracts relevant audio segment from `AudioRingBuffer`:
   - Calls `pre_draft_buffer.get_from(min_time)` to retrieve buffered chunks
   - Starts from the draft's audio_start_time (includes pre-buffered audio)
3. Feeds extracted AudioEvents to local pipeline:
   - Emits all buffered `AudioChunkEvent` instances in sequence
   - Clears the ring buffer after extraction
4. Switches to pass-through mode:
   - Continues feeding incoming `AudioChunkEvent` instances to local pipeline
   - Tracks `last_chunk` timestamp for completion detection
   - Maintains real-time processing until draft completion

**Triggered by remote `DraftEndEvent`:**

1. Waits for local draft completion (up to 15 seconds):
   - Polls `current_local_draft.end_text` every 10ms
   - Uses adaptive bump strategy: flushes Whisper buffer if needed
   - Falls back to `force_end()` if timeout exceeded
2. Creates `DraftRescanEvent`:
   - Contains both `original_draft` (from remote) and `draft` (from local rescan)
   - Logged but not yet persisted or streamed
3. Returns to buffering state:
   - Clears `current_draft`, `current_local_draft`, `texts`, `last_chunk`
   - Resumes buffering incoming audio chunks

### RescannerLocal Adapter

The `RescannerLocal` class (event_server.py:106-129) separates local pipeline events from remote events:

- Implements `ScribeAPIListener` to receive local pipeline events
- Routes events to `Rescanner.on_local_*` methods:
  - `on_local_draft_event`: Tracks local draft creation/completion
  - `on_local_text_event`: Collects transcription results
  - `on_local_audio_event`: Currently unused (placeholder)

This separation allows the Rescanner to distinguish between:
- **Remote events** from NetListener (original transcription)
- **Local events** from local pipeline (rescan transcription)

### Configuration Differences

Rescan mode typically uses enhanced transcription settings:

```python
# Example rescan configuration
model = Path("models/multilang_whisper_large3_turbo.ggml")  # Larger model

pipeline_config = PipelineConfig(
    model_path=model,
    vad_silence_ms=3000,      # Extended silence threshold (vs 800ms default)
    vad_speech_pad_ms=1000,   # Extended speech padding (vs 1500ms default)
    seconds_per_scan=10,      # Longer transcription windows (vs 2s default)
    # ... other config
)
```

### Current Implementation Status

**Phase 1 Complete:**
- ✓ `Rescanner` component implemented (event_server.py:130-264)
- ✓ `AudioRingBuffer` integration working (30s rolling window)
- ✓ Draft boundary detection and audio extraction working
- ✓ Local pipeline integration via `RescannerLocal` adapter
- ✓ `DraftRescanEvent` generation working
- ✓ Test script working (scripts/rescan.py with BlockAudioRecorder)

**Known Issues / Improvements Needed:**
- DraftRescanEvent is created but not persisted to database
- DraftRescanEvent is not streamed to remote clients
- No error recovery if audio buffer doesn't contain full draft
- Timeout handling in DraftEndEvent is functional but could be more robust
- No metrics/logging for rescan quality comparison

### Use Case

Rescan mode enables a two-tier transcription workflow:

1. **Initial transcription** (Direct/Remote mode):
   - Fast, lightweight model for real-time feedback
   - Generates initial drafts with acceptable latency

2. **Quality rescanning** (Rescan mode):
   - High-quality model processes same audio segments
   - Generates improved transcriptions as revisions
   - Occurs asynchronously without blocking initial response

This allows balancing real-time responsiveness with transcription accuracy.

## Mode Selection

Mode is selected via `ServerMode` enum at server construction:

```python
from palaver.fastapi.event_server import EventNetServer, ServerMode

server = EventNetServer(
    audio_listener,
    pipeline_config=pipeline_config,
    draft_recorder=draft_recorder,
    port=8000,
    mode=ServerMode.direct    # or ServerMode.remote, ServerMode.rescan
)
```

The `scripts/vtt_server.py` script determines mode based on command-line arguments:
- `--audio-url` provided → Remote mode
- `--rescan` flag → Rescan mode (requires --audio-url)
- Neither flag → Direct mode

## Event Flow Comparison

| Event Type | Direct Mode | Remote Mode | Rescan Mode |
|------------|-------------|-------------|-------------|
| AudioEvents | Generated locally | Received via WebSocket | Received via WebSocket, buffered by Rescanner |
| TextEvents | Generated by local pipeline | Generated by local pipeline | Received from remote (ignored), Generated locally during rescan |
| DraftEvents | Generated by pipeline/recorder | Generated by pipeline/recorder | Received from remote (**triggers** rescan), Generated locally during rescan |
| DraftRescanEvents | N/A | N/A | **Generated** by Rescanner after re-transcription (Phase 1: logged only) |

## Implementation Files

- Server implementation: `src/palaver/fastapi/event_server.py`
  - `EventNetServer` class: Main server with mode selection (lines 281-352)
  - `ServerMode` enum: Mode selection (lines 266-278)
  - `Rescanner` class: Rescan mode implementation (lines 130-264)
  - `RescannerLocal` class: Local event adapter (lines 106-129)
  - `NormalListener` class: Direct/Remote mode implementation (lines 36-105)
- Script interfaces:
  - `scripts/vtt_server.py` - Direct/Remote mode server
  - `scripts/rescan.py` - Rescan mode test script
- Audio components:
  - `src/palaver/scribe/audio/net_listener.py` - NetListener for remote audio
  - `src/palaver/scribe/audio_events.py` - AudioRingBuffer class (lines 95-148)
- Event definitions:
  - `src/palaver/scribe/draft_events.py` - DraftRescanEvent (lines 116-118)

## Future Work

### Phase 2: Event Distribution & Persistence

1. Stream `DraftRescanEvent` to remote clients via EventSender
2. Persist `DraftRescanEvent` to SQLDraftRecorder
3. Add revision tracking/history to database schema
4. Implement conflict resolution for multiple rescans of same draft

### Phase 3: Robustness & Observability

1. Add error recovery when audio buffer doesn't contain full draft
2. Improve timeout handling and add configurable timeout values
3. Add metrics for rescan quality comparison (WER, character diff, etc.)
4. Add logging for rescan performance (latency, buffer usage, etc.)
5. Handle edge cases: overlapping drafts, very long drafts (>30s buffer)

### Phase 4: Production Hardening

1. Integration testing with multi-tier transcription workflow (Direct→Rescan)
2. Performance tuning for buffer sizes and model switching
3. Add health checks and monitoring endpoints
4. Document operational procedures and tuning guidelines
