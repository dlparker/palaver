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

## Rescan Mode (IN PROGRESS)

**Purpose**: Re-transcribe audio segments with higher-quality models after initial draft completion.

### Architecture

```
Remote Client (running Direct/Remote mode server)
    ↓ (sends ALL event types via WebSocket)
NetListener (receives all event types)
    ↓
RescanListener (buffers audio, responds to DraftEvents)
    ↓
Standard Pipeline (with high-quality model config)
    ↓
DraftRevisionEvent generation
    ↓
Event Streaming (WebSocket)
```

### Characteristics

- Audio source: `NetListener` configured to receive ALL event types (not just AudioEvents)
- Special component: `RescanListener` acts as audio listener for the pipeline
- Pipeline: Configured with higher-quality Whisper model and extended VAD parameters
- Buffering: Uses `AudioRingBuffer` to maintain audio history
- Event-driven triggering: Responds to `DraftStartEvent` and `DraftEndEvent`

### RescanListener Behavior

The `RescanListener` component manages two operational states:

#### 1. Buffering State (No Active Draft)

- Collects incoming `AudioChunkEvent` instances into `AudioRingBuffer`
- Ring buffer maintains rolling window of recent audio
- Does NOT feed events to pipeline
- Waits for `DraftStartEvent` to trigger processing

#### 2. Processing State (Active Draft)

**Triggered by `DraftStartEvent`:**

1. Examines `DraftStartEvent.start_matched_events` to identify audio stream boundaries
2. Extracts relevant audio segment from `AudioRingBuffer`:
   - Finds beginning of AudioStream from start_matched_events
   - Retrieves buffered audio chunks from that point forward
3. Feeds extracted AudioEvents to pipeline:
   - Emits `AudioStartEvent` (if needed)
   - Emits buffered `AudioChunkEvent` instances in sequence
4. Switches to pass-through mode:
   - Continues feeding incoming `AudioChunkEvent` instances to pipeline
   - Maintains real-time processing until draft completion

**Triggered by `DraftEndEvent`:**

1. Stops feeding AudioEvents to pipeline
2. Returns to buffering state
3. Executes revision collection logic:
   - Locates locally completed transcription
   - Packages transcription into `DraftRevisionEvent`
   - Emits event for streaming

### Configuration Differences

Rescan mode typically uses enhanced transcription settings:

```python
# Example rescan configuration
model = Path("models/multilang_whisper_large3_turbo.ggml")  # Larger model

pipeline_config = PipelineConfig(
    model_path=model,
    vad_silence_ms=3000,      # Extended silence threshold
    vad_speech_pad_ms=1000,   # Extended speech padding
    seconds_per_scan=15,      # Longer transcription windows
    # ... other config
)
```

### Current Implementation Status

- ✗ `RescanListener` component: Not yet implemented
- ✗ `AudioRingBuffer` integration: Not yet implemented
- ✗ Draft revision collection logic: Not yet implemented
- ? Additional steps: To be defined

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
| AudioEvents | Generated locally | Received via WebSocket | Received via WebSocket, buffered by RescanListener |
| TextEvents | Generated by local pipeline | Generated by local pipeline | Generated by local pipeline |
| DraftEvents | Generated by pipeline/recorder | Generated by pipeline/recorder | **Consumed** by RescanListener to trigger processing |
| DraftRevisionEvents | N/A | N/A | **Generated** by RescanListener after re-transcription |

## Implementation Files

- Server implementation: `src/palaver/fastapi/event_server.py`
- Mode enum: `ServerMode` in `event_server.py`
- Script interface: `scripts/vtt_server.py`
- NetListener: `src/palaver/scribe/audio/net_listener.py`
- RescanListener: **Not yet implemented** (planned location TBD)
- AudioRingBuffer: **Not yet implemented** (planned location TBD)

## Future Work

Rescan mode completion requires:

1. Implement `RescanListener` component with state machine (buffering vs processing)
2. Implement or integrate `AudioRingBuffer` for audio history
3. Implement draft revision collection logic
4. Define and implement remaining rescan workflow steps
5. Integration testing with multi-tier transcription workflow
6. Performance tuning for buffer sizes and model switching
