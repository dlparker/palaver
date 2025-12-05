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

## Phase 1: Foundation - MQTT Adapter and Event UUIDs ✅ COMPLETED

### 1.1 Add UUID to AudioEvent Base Class ✅
- [x] **File**: `src/palaver/recorder/async_vad_recorder.py`
- [x] Import `uuid` and `field` from dataclasses
- [x] Modify `AudioEvent` dataclass (around line 52):
  ```python
  @dataclass
  class AudioEvent:
      timestamp: float
      event_id: str = field(default_factory=lambda: str(uuid.uuid4()), kw_only=True)
  ```
- [x] Test: Run existing tests to ensure nothing breaks with new field
  ```bash
  uv run pytest tests/test_recorder_events.py -v
  ```

### 1.2 Create MQTT Module Structure ✅
- [x] Create directory: `src/palaver/mqtt/`
- [x] Create file: `src/palaver/mqtt/__init__.py` (exports MQTTAdapter, MQTTPublisher)
- [x] Create file: `src/palaver/mqtt/client.py`
- [x] Create file: `src/palaver/mqtt/mqtt_adapter.py`

### 1.3 Implement MQTT Client Wrapper ✅
- [x] **File**: `src/palaver/mqtt/client.py`
- [x] Import `asyncio_mqtt.Client`
- [x] Implement `MQTTPublisher` class:
  - `__init__(broker, port, qos)` - defaults: localhost, 1883, qos=1
  - `async connect()` - connect to broker
  - `async disconnect()` - cleanup
  - `async publish(topic, payload, retain=False)` - publish with QoS 1, no retention
- [x] Add dependency to `pyproject.toml`:
  ```toml
  dependencies = [
      # ... existing ...
      "asyncio-mqtt>=0.16.2",
  ]
  ```
- [x] Install: `uv pip install -e .`

### 1.4 Implement MQTT Adapter ✅
- [x] **File**: `src/palaver/mqtt/mqtt_adapter.py`
- [x] Import event types from `palaver.recorder.async_vad_recorder`
- [x] Implement `MQTTAdapter` class:
  - `__init__(mqtt_client, session_id)` - store client and session_id
  - Track state: `current_state`, `current_bucket`, `command_doc_type`
  - Store segment durations from `SpeechEnded` events (for enrichment)
  - `async handle_event(event)` - main dispatcher
  - `async _publish_segment(event)` - handle `TranscriptionComplete`
  - `async _publish_command_completion(event)` - handle `CommandCompleted`
  - `_update_state(state, command_type=None)` - state transitions

### 1.5 Segment Message Publishing ✅
- [x] **In `MQTTAdapter._publish_segment()`**:
- [x] Build message with fields:
  - `event_id` (from event)
  - `timestamp` (from event)
  - `session_id` (from self)
  - `segment_index`, `text`, `success`, `processing_time_sec` (from event)
  - `duration_sec` (lookup from stored SpeechEnded events)
  - `session_state`: {state, command_type, current_bucket}
- [x] Topic: `palaver/session/{session_id}/segment`
- [x] Convert to JSON and publish

### 1.6 Command Completion Message Publishing ✅
- [x] **In `MQTTAdapter._publish_command_completion()`**:
- [x] Build message with fields:
  - `event_id`, `timestamp`, `session_id`
  - `command_type`, `output_files` (from event)
  - `bucket_contents` (added to CommandCompleted event)
  - `duration_sec` (calculate from command start time - pending Phase 2)
- [x] Topic: `palaver/session/{session_id}/command/completed`
- [x] Convert to JSON and publish

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

### 2.6 Cleanup Segment Files After CommandDoc Completion
- [ ] **Add debug configuration flag**:
  - [ ] **File**: `src/palaver/config/recorder_config.py`
  - [ ] Add field: `keep_segment_files: bool = False` (debug flag)
  - [ ] Update `config.example.yaml` with comment explaining the flag
- [ ] **Implement segment cleanup in `TextProcessor._complete_command()`**:
  - [ ] Track which segment indices were used in the CommandDoc
  - [ ] After `render()` completes successfully
  - [ ] If `not config.keep_segment_files`:
    - Delete segment WAV files that were used in the CommandDoc
    - Example: `sessions/YYYYMMDD_HHMMSS/seg_0000.wav`, `seg_0001.wav`, etc.
  - [ ] Keep segments if `keep_segment_files=True` (for debugging)
- [ ] **Alternative**: Cleanup in `CommandCompleted` event handler
  - Could move cleanup logic to event handler instead of TextProcessor
  - Keeps TextProcessor focused on text processing
  - Event handler would need access to session directory and segment list

---

## Phase 3: Configuration and Integration (Partial - CLI complete, TUI pending)

### 3.1 Add MQTT Configuration ✅
- [x] **File**: `src/palaver/config/recorder_config.py`
- [x] Add to `RecorderConfig` dataclass (around line 14):
  ```python
  # MQTT Configuration (local broker, no retention, QoS 1)
  mqtt_enabled: bool = False
  mqtt_broker: str = "localhost"
  mqtt_port: int = 1883
  mqtt_qos: int = 1
  mqtt_topic_prefix: str = "palaver"
  ```

### 3.2 Wire MQTT into CLI Recorder ✅
- [x] **File**: `scripts/direct_recorder.py`
- [x] Import MQTT modules:
  ```python
  from palaver.mqtt.mqtt_adapter import MQTTAdapter
  from palaver.mqtt.client import MQTTPublisher
  from palaver.config.recorder_config import RecorderConfig
  ```
- [x] After creating recorder, before `start_recording()`:
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

  # Create combined event handler
  async def combined_event_handler(event: AudioEvent):
      await event_logger(event)
      if mqtt_adapter:
          await mqtt_adapter.handle_event(event)

  # Pass to recorder
  recorder = AsyncVADRecorder(event_callback=combined_event_handler)
  ```
- [x] Add cleanup on shutdown:
  ```python
  if mqtt_client:
      await mqtt_client.disconnect()
  ```

### 3.3 Wire MQTT into TUI ✅
- [x] **File**: `src/palaver/tui/recorder_tui.py`
- [x] Similar integration as CLI recorder
- [x] In `RecorderApp.__init__()`, load config and create recorder with keep_segment_files
- [x] In `async on_mount()`, setup MQTT if config.mqtt_enabled
- [x] Chain callbacks: TUI needs events AND MQTT needs events
- [x] Implemented: Forward events to MQTT in `handle_recorder_event()`
- [x] Update MQTT adapter session_id when recording starts
- [x] Disconnect MQTT in `action_quit()`
- [x] Display MQTT status notification on connection

### 3.4 Create Example Configuration File ✅
- [x] Create file: `config.example.yaml`
- [x] Include all RecorderConfig settings with comments
- [x] Include MQTT settings:
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

## Phase 5: Voice Stop Command (Future Enhancement)

**Goal**: Add voice-activated recording stop to provide explicit user control and solve shutdown race conditions.

**Design**:
- Global default stop phrase in config
- CommandDoc can use default, specify custom, or opt out
- Solves race condition where transcription completes after text processor stops

### 5.1 Add Stop Phrase Configuration
- [ ] **File**: `src/palaver/config/recorder_config.py`
- [ ] Add field: `stop_phrase: str = "break break break"` (or "attention to command: stop")
- [ ] Add field: `stop_phrase_threshold: float = 80.0` (rapidfuzz similarity %)
- [ ] Update `config.example.yaml` with explanation and examples

### 5.2 Extend CommandDoc Base Class
- [ ] **File**: `src/palaver/commands/command_doc.py`
- [ ] Add abstract property: `stop_phrase: Optional[str]`
  - Return `None` to use global default from config
  - Return custom string to override
  - Return `""` (empty string) to disable stop detection for this command
- [ ] Document the three modes in docstring

### 5.3 Implement Stop Detection in TextProcessor
- [ ] **File**: `src/palaver/recorder/text_processor.py`
- [ ] Add stop phrase matcher (similar to `start_note_phrase`)
- [ ] In `_check_commands()` after bucket accumulation:
  ```python
  # Check for stop command (if in active command)
  if self.current_command is not None:
      stop_phrase = self._get_stop_phrase()  # CommandDoc custom or config default
      if stop_phrase and self._matches_stop_phrase(result.text, stop_phrase):
          # Complete current bucket
          self._complete_bucket(...)
          # Complete command workflow
          self._complete_command()
          # Trigger recording stop
          self.stop_recording_callback()
          return
  ```

### 5.4 Wire Stop Callback Through Stack
- [ ] Add `stop_recording_callback` parameter to `TextProcessor.__init__()`
- [ ] In `AsyncVADRecorder`, pass callback to TextProcessor:
  ```python
  stop_callback=lambda: asyncio.create_task(self.stop_recording())
  ```
- [ ] Handle thread-safety (callback called from text processor thread)

### 5.5 Update SimpleNote CommandDoc
- [ ] **File**: `src/palaver/commands/simple_note.py`
- [ ] Add `stop_phrase` property:
  ```python
  @property
  def stop_phrase(self) -> Optional[str]:
      return None  # Use global default from config
  ```

### 5.6 Benefits of This Approach
- **Solves Race Condition**: Stop command is transcribed first, ensuring all prior transcriptions complete
- **Explicit Control**: User signals "I'm done" instead of relying on silence timeouts
- **Flexible**: CommandDocs can customize or disable as needed
- **Better UX**: No awkward waiting for silence threshold
- **Clean Shutdown**: Command workflow completes before stop is initiated

### 5.7 Example Usage
```yaml
# config.yaml
stop_phrase: "break break break"
stop_phrase_threshold: 75.0  # Lower threshold for natural speech variations
```

**User says**: "This is my note body... break break break"
1. Segment transcribed: "This is my note body... break break break"
2. TextProcessor accumulates: "This is my note body..."
3. TextProcessor detects stop phrase
4. Completes note_body bucket
5. Calls `render()` to create note file
6. Deletes segment files (if configured)
7. Triggers recording stop
8. **All transcriptions complete ✅ No race condition!**

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

**Current Phase**: Phase 2 Complete ✅ - Command Workflow Implemented

**Completed**:
- Planning
- Architecture design
- **Phase 1**: Foundation - MQTT Adapter and Event UUIDs ✅
  - Added UUID field to AudioEvent base class
  - Created MQTT module structure (client.py, mqtt_adapter.py)
  - Implemented MQTTPublisher wrapper (migrated to aiomqtt 2.4.0)
  - Implemented MQTTAdapter with segment and command completion publishing
  - Added aiomqtt dependency
- **Phase 2**: Command Workflow Events ✅
  - Extended TextProcessor with command workflow state tracking
  - Implemented CommandDetected, BucketStarted, BucketFilled event emission
  - Implemented bucket accumulation and completion logic
  - Added notify_mode_changed() for bucket completion detection
  - Implemented _complete_command() with render() and CommandCompleted event
  - Added keep_segment_files config flag and segment cleanup
  - Wired through AsyncVADRecorder and CLI
- **Phase 3**: Configuration and Integration ✅
  - Added MQTT configuration to RecorderConfig ✅
  - Wired MQTT into CLI recorder ✅
  - Wired MQTT into TUI recorder ✅
  - Created config.example.yaml ✅
  - Updated CLI and TUI to load config.yaml from working directory ✅

**Known Issue**:
- Race condition in file playback mode: final transcription completes after text processor stops
- Solution planned: Phase 5 - Voice Stop Command (will solve race condition elegantly)

**Next Steps**:
1. Wire MQTT into TUI (Phase 3.3)
2. Create unit tests (Phase 4.1-4.3)
3. (Future) Implement Phase 5: Voice Stop Command
