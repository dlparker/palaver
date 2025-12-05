# MQTT Integration Implementation Checklist

## Context

**Goal**: Add MQTT message emission for real-time transcription events and command completion.

**Architecture**: Event Callback Adapter pattern
- Create `src/palaver/mqtt/` module with adapter that subscribes to existing event system
- MQTT adapter receives events via `event_callback` parameter of `AsyncVADRecorder`
- Publishes two message types: segment transcriptions and command completions
- Non-invasive design - MQTT can be enabled/disabled via configuration

**Key Requirements**:
- Local MQTT broker: `localhost:1883`
- QoS 1 (at least once delivery)
- No message retention
- No TLS/authentication
- Add UUID to all events for tracking
- Session ID uses timestamp (matches `sessions/YYYYMMDD_HHMMSS/`)

**Full Plan**: See `/home/dparker/.claude/plans/eventual-riding-firefly.md`

---

## Phase 1: Foundation - MQTT Adapter and Event UUIDs

### 1.1 Add UUID to AudioEvent Base Class
- [ ] **File**: `src/palaver/recorder/async_vad_recorder.py`
- [ ] Import `uuid` and `field` from dataclasses
- [ ] Modify `AudioEvent` dataclass (around line 52):
  ```python
  @dataclass
  class AudioEvent:
      timestamp: float
      event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
  ```
- [ ] Test: Run existing tests to ensure nothing breaks with new field
  ```bash
  uv run pytest tests/test_recorder_events.py -v
  ```

### 1.2 Create MQTT Module Structure
- [ ] Create directory: `src/palaver/mqtt/`
- [ ] Create file: `src/palaver/mqtt/__init__.py` (can be empty)
- [ ] Create file: `src/palaver/mqtt/client.py`
- [ ] Create file: `src/palaver/mqtt/mqtt_adapter.py`

### 1.3 Implement MQTT Client Wrapper
- [ ] **File**: `src/palaver/mqtt/client.py`
- [ ] Import `asyncio_mqtt.Client`
- [ ] Implement `MQTTPublisher` class:
  - `__init__(broker, port, qos)` - defaults: localhost, 1883, qos=1
  - `async connect()` - connect to broker
  - `async disconnect()` - cleanup
  - `async publish(topic, payload, retain=False)` - publish with QoS 1, no retention
- [ ] Add dependency to `pyproject.toml`:
  ```toml
  dependencies = [
      # ... existing ...
      "asyncio-mqtt>=0.16.2",
  ]
  ```
- [ ] Install: `uv pip install -e .`

### 1.4 Implement MQTT Adapter
- [ ] **File**: `src/palaver/mqtt/mqtt_adapter.py`
- [ ] Import event types from `palaver.recorder.async_vad_recorder`
- [ ] Implement `MQTTAdapter` class:
  - `__init__(mqtt_client, session_id)` - store client and session_id
  - Track state: `current_state`, `current_bucket`, `command_doc_type`
  - Store segment durations from `SpeechEnded` events (for enrichment)
  - `async handle_event(event)` - main dispatcher
  - `async _publish_segment(event)` - handle `TranscriptionComplete`
  - `async _publish_command_completion(event)` - handle `CommandCompleted`
  - `_update_state(state, command_type=None)` - state transitions

### 1.5 Segment Message Publishing
- [ ] **In `MQTTAdapter._publish_segment()`**:
- [ ] Build message with fields:
  - `event_id` (from event)
  - `timestamp` (from event)
  - `session_id` (from self)
  - `segment_index`, `text`, `success`, `processing_time_sec` (from event)
  - `duration_sec` (lookup from stored SpeechEnded events)
  - `session_state`: {state, command_type, current_bucket}
- [ ] Topic: `palaver/session/{session_id}/segment`
- [ ] Convert to JSON and publish

### 1.6 Command Completion Message Publishing
- [ ] **In `MQTTAdapter._publish_command_completion()`**:
- [ ] Build message with fields:
  - `event_id`, `timestamp`, `session_id`
  - `command_type`, `output_files` (from event)
  - `bucket_contents` (need to add to CommandCompleted event - see Phase 2)
  - `duration_sec` (calculate from command start time)
- [ ] Topic: `palaver/session/{session_id}/command/completed`
- [ ] Convert to JSON and publish

---

## Phase 2: Command Workflow Events

**Current Gap**: Events `CommandDetected`, `BucketStarted`, `BucketFilled`, `CommandCompleted` are defined in `async_vad_recorder.py` but not emitted by `TextProcessor`.

### 2.1 Extend TextProcessor State Tracking
- [ ] **File**: `src/palaver/recorder/text_processor.py`
- [ ] Add to `__init__` (around line 40-65):
  ```python
  # NEW: Command workflow tracking
  self.current_command = None  # CommandDoc instance
  self.current_bucket_index = 0
  self.bucket_contents = {}  # Dict[bucket_name, text]
  self.bucket_start_times = {}  # Dict[bucket_name, timestamp]
  self.command_start_time = None
  ```

### 2.2 Implement Command Detection
- [ ] **In `TextProcessor._process_result()`** (around line 130-236):
- [ ] Check if text matches command phrase (use existing `start_note_phrase.match()`)
- [ ] If match:
  - Create `SimpleNote()` instance (import from `palaver.commands.simple_note`)
  - Set `current_command`, reset `current_bucket_index = 0`
  - Emit `CommandDetected` event
  - Emit `BucketStarted` for first bucket
  - Store `bucket_start_times[bucket.name] = time.time()`
  - Return early

### 2.3 Implement Bucket Accumulation
- [ ] **In `TextProcessor._process_result()`**:
- [ ] After command detection, check `if self.current_command:`
- [ ] Get current bucket: `bucket = self.current_command.speech_buckets[self.current_bucket_index]`
- [ ] Accumulate text to `self.bucket_contents[bucket.name]`
- [ ] Check if bucket complete (need to determine from VAD mode change callback)
- [ ] When complete:
  - Calculate duration: `time.time() - self.bucket_start_times[bucket.name]`
  - Emit `BucketFilled` event
  - Increment `current_bucket_index`
  - If more buckets: emit `BucketStarted` for next bucket
  - If all done: call `self._complete_command()`

### 2.4 Implement Command Completion
- [ ] **Add method `TextProcessor._complete_command()`**:
- [ ] Call `output_files = self.current_command.render(self.bucket_contents, self.session_dir)`
- [ ] Import `CommandCompleted` from `async_vad_recorder`
- [ ] Modify `CommandCompleted` dataclass to include `bucket_contents`:
  ```python
  @dataclass
  class CommandCompleted(AudioEvent):
      command_doc_type: str
      output_files: List[Path]
      bucket_contents: Dict[str, str]  # ADD THIS FIELD
  ```
- [ ] Emit `CommandCompleted` event with bucket_contents
- [ ] Reset state: `current_command = None`, clear dicts

### 2.5 Bucket Completion Detection
- [ ] **Challenge**: How does TextProcessor know when bucket is complete?
- [ ] **Current approach**: VAD mode change callback triggers on silence
- [ ] **Options**:
  1. Listen for `VADModeChanged` events in TextProcessor
  2. Add callback parameter for bucket completion
  3. Count segments and match to bucket's expected chunk count
- [ ] **Decision needed**: Choose and implement bucket completion logic
- [ ] **Recommended**: Listen for VADModeChanged(mode="normal") after long_note mode

---

## Phase 3: Configuration and Integration

### 3.1 Add MQTT Configuration
- [ ] **File**: `src/palaver/config/recorder_config.py`
- [ ] Add to `RecorderConfig` dataclass (around line 14):
  ```python
  # MQTT Configuration (local broker, no retention, QoS 1)
  mqtt_enabled: bool = False
  mqtt_broker: str = "localhost"
  mqtt_port: int = 1883
  mqtt_qos: int = 1
  mqtt_topic_prefix: str = "palaver"
  ```

### 3.2 Wire MQTT into CLI Recorder
- [ ] **File**: `scripts/direct_recorder.py`
- [ ] Import MQTT modules:
  ```python
  from palaver.mqtt.mqtt_adapter import MQTTAdapter
  from palaver.mqtt.client import MQTTPublisher
  from palaver.config.recorder_config import RecorderConfig
  ```
- [ ] After creating recorder, before `start_recording()`:
  ```python
  # Setup MQTT if enabled
  mqtt_adapter = None
  if config.mqtt_enabled:
      mqtt_client = MQTTPublisher(
          broker=config.mqtt_broker,
          port=config.mqtt_port,
          qos=config.mqtt_qos
      )
      await mqtt_client.connect()

      session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
      mqtt_adapter = MQTTAdapter(mqtt_client, session_id)

  # Pass to recorder
  recorder = AsyncVADRecorder(
      event_callback=mqtt_adapter.handle_event if mqtt_adapter else None
  )
  ```
- [ ] Add cleanup on shutdown:
  ```python
  if mqtt_adapter:
      await mqtt_adapter.mqtt_client.disconnect()
  ```

### 3.3 Wire MQTT into TUI
- [ ] **File**: `src/palaver/tui/recorder_tui.py`
- [ ] Similar integration as CLI recorder
- [ ] In `RecorderApp.__init__()`, setup MQTT if config.mqtt_enabled
- [ ] Chain callbacks: TUI needs events AND MQTT needs events
- [ ] **Options**:
  1. Create wrapper that calls both callbacks
  2. Have MQTT adapter forward events to TUI callback
- [ ] **Recommended**: Wrapper function that calls both

### 3.4 Create Example Configuration File
- [ ] Create file: `config.example.yaml`
- [ ] Include MQTT settings:
  ```yaml
  # MQTT Configuration
  mqtt_enabled: false
  mqtt_broker: localhost
  mqtt_port: 1883
  mqtt_qos: 1
  mqtt_topic_prefix: palaver
  ```

---

## Phase 4: Testing

### 4.1 Unit Tests - MQTT Adapter
- [ ] **File**: `tests/test_mqtt_adapter.py`
- [ ] Create `MockMQTTClient` class to capture publishes
- [ ] Test segment message format:
  - Create `TranscriptionComplete` event
  - Call `adapter.handle_event(event)`
  - Verify topic and payload structure
  - Check all required fields present
- [ ] Test command completion message format
- [ ] Test state transitions (idle → in_command → idle)
- [ ] Test session_state enrichment

### 4.2 Unit Tests - Event UUIDs
- [ ] **File**: `tests/test_recorder_events.py` (may need to create)
- [ ] Test that `AudioEvent` instances get unique `event_id`
- [ ] Test that UUIDs are different for different events
- [ ] Test that event_id survives dataclass operations (asdict, etc)

### 4.3 Integration Test - End-to-End MQTT
- [ ] **File**: `tests_slow/test_mqtt_recording.py`
- [ ] Requires running MQTT broker (mosquitto):
  ```bash
  # Install: sudo apt-get install mosquitto mosquitto-clients
  # Start: sudo systemctl start mosquitto
  ```
- [ ] Create MQTT subscriber to capture messages
- [ ] Run recorder with test audio file (`tests/audio_samples/note1.wav`)
- [ ] Verify messages published to correct topics
- [ ] Verify message sequence matches recording flow
- [ ] Mark as `@pytest.mark.slow`

### 4.4 Manual Testing
- [ ] Start local MQTT broker: `mosquitto -v`
- [ ] In separate terminal, subscribe to all palaver messages:
  ```bash
  mosquitto_sub -h localhost -t 'palaver/#' -v
  ```
- [ ] Enable MQTT in config: `mqtt_enabled: true`
- [ ] Run recorder: `uv run python scripts/direct_recorder.py`
- [ ] Record "start new note" / "Test Title" / "Test body"
- [ ] Verify MQTT messages in subscriber terminal
- [ ] Check message format matches plan

---

## Verification Checklist

After implementation, verify:

- [ ] Event UUIDs are generated for all events
- [ ] MQTT can be enabled/disabled via config
- [ ] Segment messages include: event_id, timestamp, session_id, text, duration, session_state
- [ ] Command completion messages include: event_id, timestamp, command_type, bucket_contents, output_files
- [ ] Topic structure: `palaver/session/{session_id}/segment` and `.../command/completed`
- [ ] QoS 1, no retention, local broker works
- [ ] Session ID matches directory timestamp format
- [ ] Existing tests still pass
- [ ] No MQTT errors when mqtt_enabled=false
- [ ] TUI and CLI both work with MQTT enabled

---

## Known Issues / TODOs

- [ ] **Bucket completion detection**: Need to decide on mechanism (see Phase 2.5)
- [ ] **Segment duration lookup**: MQTTAdapter needs to store SpeechEnded durations and lookup by segment_index
- [ ] **CommandCompleted.bucket_contents**: Need to add this field to event dataclass
- [ ] **Error handling**: Add try/except around MQTT publishes to prevent crashes
- [ ] **Reconnection**: If broker disconnects, should we auto-reconnect?
- [ ] **Documentation**: Update CLAUDE.md with MQTT integration info

---

## Files to Create/Modify

**New Files**:
- `src/palaver/mqtt/__init__.py`
- `src/palaver/mqtt/client.py`
- `src/palaver/mqtt/mqtt_adapter.py`
- `tests/test_mqtt_adapter.py`
- `tests_slow/test_mqtt_recording.py`
- `config.example.yaml`

**Modified Files**:
- `src/palaver/recorder/async_vad_recorder.py` (add event_id to AudioEvent, add bucket_contents to CommandCompleted)
- `src/palaver/recorder/text_processor.py` (implement command workflow)
- `src/palaver/config/recorder_config.py` (add MQTT config)
- `scripts/direct_recorder.py` (wire MQTT)
- `src/palaver/tui/recorder_tui.py` (wire MQTT)
- `pyproject.toml` (add asyncio-mqtt dependency)

---

## Quick Start Commands

```bash
# Install MQTT dependency
uv pip install -e .

# Start MQTT broker (separate terminal)
mosquitto -v

# Subscribe to messages (separate terminal)
mosquitto_sub -h localhost -t 'palaver/#' -v

# Run tests
uv run pytest tests/test_mqtt_adapter.py -v
uv run pytest tests_slow/test_mqtt_recording.py -v -m slow

# Run recorder with MQTT
# (enable in config first)
uv run python scripts/direct_recorder.py
```

---

## Progress Tracking

**Last Updated**: 2024-12-05

**Current Phase**: Not started

**Completed**:
- Planning
- Architecture design

**Next Steps**:
1. Add UUID to AudioEvent (Phase 1.1)
2. Create MQTT module structure (Phase 1.2)
3. Implement MQTT client wrapper (Phase 1.3)
