# Recorder Refactoring and Testing Plan

## Project Context

**Problem**: The new async recorder backend (`recorder_backend_async.py`) hangs when started via the Textual UI (`recorder_tui.py`). The hang is severe enough to require killing the terminal.

**Working Baseline**: The original `vad_recorder.py` works correctly and serves as our reference implementation.

**Goal**: Refactor both recorders to support testing with pre-recorded audio files, add comprehensive logging, fix the async backend, and ensure the TUI works reliably.

## High-Level Checklist

- [ ] **Phase 1**: Refactor `vad_recorder.py` to support audio file input
- [ ] **Phase 2**: Create and validate test for `vad_recorder.py` using `tests/audio_samples/note1.wav`
- [ ] **Phase 3**: Add comprehensive logging to `recorder_backend_async.py`
- [ ] **Phase 4**: Refactor `recorder_backend_async.py` to support audio file input and create tests
- [ ] **Phase 5**: Fix async backend issues and validate with tests
- [ ] **Phase 6**: Update and test `recorder_tui.py` with working backend

---

## Phase 1: Refactor vad_recorder.py for Dual Input Support

**Objective**: Modify `vad_recorder.py` to accept either live microphone input OR a pre-recorded WAV file, controlled by initialization parameters.

### Task 1.1: Design the Input Abstraction
- [ ] Review current audio callback implementation in `vad_recorder.py:336-386`
- [ ] Design an input source abstraction that can:
  - Read from sounddevice InputStream (current behavior)
  - Read from WAV file in chunks matching CHUNK_SIZE
  - Maintain the same callback interface for VAD processing
- [ ] Document the design approach (what classes/functions will change)
- [ ] **Review Point**: Present design for approval

### Task 1.2: Implement WAV File Reader
- [ ] Create a new class/function to read WAV files in chunks
  - Must respect RECORD_SR (48000 Hz) sample rate
  - Must produce chunks of CHUNK_SIZE samples
  - Must handle resampling if WAV file has different sample rate
  - Should convert stereo to mono if needed (use left channel)
- [ ] Add error handling for invalid WAV files
- [ ] **Review Point**: Present implementation for approval

### Task 1.3: Modify Main Recording Loop
- [ ] Add command-line argument or parameter to specify input source:
  - `--input-device hw:1,0` (default, current behavior)
  - `--input-file path/to/file.wav`
- [ ] Modify `main()` function to select input source based on parameter
- [ ] Ensure audio_callback receives data in the same format regardless of source
- [ ] **Review Point**: Test changes with microphone input to ensure no regression

### Task 1.4: Update Session Management
- [ ] Ensure session directory is created regardless of input source
- [ ] Update manifest.json to record input source (device vs file)
- [ ] Add metadata about source file if using file input
- [ ] **Review Point**: Verify session metadata is correct

---

## Phase 2: Test vad_recorder.py with Audio File

**Objective**: Create a robust test using `tests/audio_samples/note1.wav` and iterate until results are satisfactory.

### Task 2.1: Examine Test Audio File
- [ ] Inspect `tests/audio_samples/note1.wav`:
  - Sample rate
  - Duration
  - Number of channels
  - Content (what's being said, if known)
- [ ] Document expected behavior (how many segments should be detected)
- [ ] **Review Point**: Confirm understanding of test file

### Task 2.2: Create Initial Test
- [ ] Write `tests/test_vad_recorder_file.py`
- [ ] Test should:
  - Initialize vad_recorder with file input mode
  - Process `note1.wav`
  - Capture output session directory
  - Assert expected number of segments created
  - Assert transcript files exist
  - Return session directory for inspection
- [ ] Use pytest fixtures for cleanup
- [ ] **Review Point**: Review test structure before implementation

### Task 2.3: Run Test and Analyze Results
- [ ] Run test and collect:
  - Number of segments detected
  - Segment durations
  - Transcription results (from transcript_raw.txt)
  - Any errors or warnings
- [ ] Compare against expected behavior
- [ ] **Review Point**: Review results and decide if adjustments needed

### Task 2.4: Iterate on VAD Parameters (if needed)
- [ ] If segments are not detected correctly, adjust:
  - VAD_THRESHOLD (currently 0.5)
  - MIN_SILENCE_MS (currently 800ms)
  - MIN_SEG_SEC (currently 1.2s)
  - SPEECH_PAD_MS (currently 1300ms)
- [ ] Document why adjustments were made
- [ ] Re-run test after each adjustment
- [ ] **Review Point**: Approve parameter changes

### Task 2.5: Validate Transcription Pipeline
- [ ] Verify transcription workers process all segments
- [ ] Check transcript_raw.txt has all expected segments
- [ ] Verify no segments marked as [transcription pending or failed]
- [ ] **Review Point**: Confirm test passes reliably

---

## Phase 3: Add Logging to recorder_backend_async.py

**Objective**: Add comprehensive Python logging throughout the async backend to diagnose the hanging issue.

### Task 3.1: Design Logging Strategy
- [ ] Identify critical points to log:
  - Backend initialization
  - Recording start/stop
  - VAD state changes
  - Audio callback invocations (with rate limiting)
  - Event emissions
  - Worker process lifecycle
  - Queue operations
  - Coroutine scheduling from audio thread
  - Result collection loop
- [ ] Choose logging levels (DEBUG, INFO, WARNING, ERROR)
- [ ] Decide on logging format (include timestamps, thread/process IDs)
- [ ] **Review Point**: Approve logging strategy

### Task 3.2: Add Logging to Initialization
- [ ] Add logger to `AsyncRecorderBackend.__init__` (line 204)
- [ ] Log VAD loading in `_ensure_vad_loaded` (line 254)
- [ ] Log VAD creation in `_create_vad` (line 268)
- [ ] Log event loop capture in `start_recording` (line 462)
- [ ] **Review Point**: Verify initialization logging

### Task 3.3: Add Logging to Audio Callback Path
- [ ] Add throttled logging to `_audio_callback` (line 310)
  - Log every Nth invocation to avoid spam
  - Log speech start/end events
  - Log VAD mode changes
- [ ] Log coroutine scheduling in `_schedule_coro` (line 305)
- [ ] Log segment saving in `_save_and_queue_segment` (line 354)
- [ ] **Review Point**: Test that audio callback logging isn't excessive

### Task 3.4: Add Logging to Worker and Result Collection
- [ ] Add logging to `transcription_worker` (line 138)
  - Log worker startup/shutdown
  - Log job retrieval
  - Log transcription start/completion
- [ ] Add logging to `_result_collector_loop` (line 391)
  - Log loop start/stop
  - Log result retrieval
  - Log command detection
- [ ] **Review Point**: Verify worker lifecycle is visible

### Task 3.5: Add Logging to Event System
- [ ] Log all events in `_emit_event` (line 243)
- [ ] Log event callback success/failure
- [ ] Distinguish async vs sync callback invocations
- [ ] **Review Point**: Confirm event flow is traceable

---

## Phase 4: Refactor recorder_backend_async.py for Dual Input

**Objective**: Apply the same file input support to the async backend, then create tests.

### Task 4.1: Port Input Abstraction from Phase 1
- [ ] Review the input abstraction design from Task 1.1
- [ ] Adapt it for async/await patterns
- [ ] Design how file reading will integrate with:
  - `_audio_callback` (line 310)
  - Event loop and coroutine scheduling
  - The audio thread concept
- [ ] **Review Point**: Approve async input design

### Task 4.2: Implement Async WAV File Reader
- [ ] Create async file reader that:
  - Reads WAV in chunks matching CHUNK_SIZE
  - Simulates real-time audio delivery (respects chunk timing)
  - Calls the audio callback with chunks
  - Runs in a separate task
- [ ] Handle case where file is shorter than a recording session
- [ ] Add cleanup when file ends
- [ ] **Review Point**: Review implementation

### Task 4.3: Modify start_recording for Dual Input
- [ ] Add parameter to `start_recording`: `input_source: Union[str, Path]`
  - If string starting with "hw:": use device
  - If Path or string file path: use file
- [ ] Conditionally create either:
  - InputStream (current behavior, lines 497-506)
  - File reader task (new behavior)
- [ ] Ensure event loop and callback mechanism work for both paths
- [ ] **Review Point**: Test with device input to ensure no regression

### Task 4.4: Create Initial Test for Async Backend
- [ ] Write `tests/test_recorder_backend_async_file.py`
- [ ] Test should:
  - Create AsyncRecorderBackend with event callback
  - Call `await backend.start_recording(input_source="tests/audio_samples/note1.wav")`
  - Collect events in a list
  - Wait for processing to complete
  - Stop recording
  - Assert expected events were received
  - Assert expected segments created
- [ ] **Review Point**: Review test before running

### Task 4.5: Run Test with Full Logging
- [ ] Run test with DEBUG logging enabled
- [ ] Capture all log output
- [ ] Identify where the hang occurs (if it still happens)
- [ ] **Review Point**: Analyze logs together to diagnose issue

---

## Phase 5: Fix Async Backend Issues

**Objective**: Use logs and tests to identify and fix the hanging issue in the async backend.

### Task 5.1: Diagnose Hanging Issue
- [ ] Analyze logs from Task 4.5 to identify:
  - Last successful log message before hang
  - Any asyncio warnings or errors
  - Queue states when hang occurs
  - Worker process states
  - Coroutine scheduling issues
- [ ] Form hypothesis about root cause
- [ ] **Review Point**: Discuss hypothesis and proposed fix

### Task 5.2: Common Async Issues to Check
- [ ] Check if event loop is blocked:
  - Is `_audio_callback` doing blocking I/O? (should be sync/fast)
  - Are we properly using `run_in_executor` for blocking ops?
- [ ] Check `_schedule_coro` usage (line 305):
  - Is the loop reference valid?
  - Are coroutines being scheduled correctly from audio thread?
- [ ] Check `_result_collector_loop` (line 391):
  - Is it polling correctly?
  - Could it exit prematurely?
- [ ] Check worker shutdown sequence (lines 531-544):
  - Are poison pills being sent correctly?
  - Are workers actually terminating?
- [ ] **Review Point**: Document findings

### Task 5.3: Implement Fixes
- [ ] Apply fixes based on diagnosis
- [ ] Add additional logging around fixed areas
- [ ] Add assertions or validation where appropriate
- [ ] **Review Point**: Review fixes before testing

### Task 5.4: Re-run Tests
- [ ] Run `tests/test_recorder_backend_async_file.py`
- [ ] Verify test passes
- [ ] Check logs to ensure smooth execution
- [ ] Run test multiple times to check for race conditions
- [ ] **Review Point**: Confirm test reliability

### Task 5.5: Test with Live Microphone
- [ ] Create simple script to test async backend with real microphone
- [ ] Record a short session (10-15 seconds)
- [ ] Verify segments detected and transcribed
- [ ] Verify clean shutdown
- [ ] **Review Point**: Confirm device input works

---

## Phase 6: Update and Test recorder_tui.py

**Objective**: Integrate the fixed async backend with the Textual UI and ensure it works reliably.

### Task 6.1: Review TUI Integration Points
- [ ] Review how TUI initializes backend (line 231)
- [ ] Review how TUI handles events (line 269)
- [ ] Review how TUI calls start/stop (lines 361-366)
- [ ] Identify any potential issues with Textual's event loop vs asyncio
- [ ] **Review Point**: Discuss integration concerns

### Task 6.2: Add Logging to TUI
- [ ] Add logging to `RecorderApp` initialization
- [ ] Log all recorder events received in `handle_recorder_event` (line 269)
- [ ] Log action invocations (start/stop/quit)
- [ ] Log any Textual-specific lifecycle events
- [ ] **Review Point**: Verify TUI logging strategy

### Task 6.3: Test TUI with File Input (if supported)
- [ ] If we want TUI to support file input, add command-line option
- [ ] Otherwise, create a test harness that:
  - Initializes TUI
  - Programmatically triggers recording
  - Uses file input in backend
  - Captures events
  - Verifies UI updates
- [ ] **Review Point**: Decide on testing approach

### Task 6.4: Test TUI with Live Microphone
- [ ] Launch TUI manually
- [ ] Start recording with SPACE
- [ ] Speak test phrases
- [ ] Verify:
  - Recording starts without hanging
  - UI updates show speech detection
  - Transcriptions appear in real-time
  - Stop works cleanly
  - Session files are created
- [ ] **Review Point**: Demo working TUI

### Task 6.5: Test "Start New Note" Flow
- [ ] In TUI, say "start new note"
- [ ] Verify notification appears
- [ ] Say a note title
- [ ] Verify:
  - Title is captured
  - Long note mode activates
  - Silence threshold changes to 5 seconds
  - Can dictate extended note
  - Mode returns to normal after note ends
- [ ] **Review Point**: Confirm note workflow works

### Task 6.6: Stress Testing
- [ ] Run TUI for extended period (5+ minutes)
- [ ] Multiple start/stop cycles
- [ ] Multiple notes in one session
- [ ] Verify no memory leaks or resource issues
- [ ] Verify clean shutdown
- [ ] **Review Point**: Confirm stability

---

## Success Criteria

1. **vad_recorder.py** can process both live audio and WAV files
2. **Test suite** reliably validates recorder behavior with known audio
3. **Logging** provides clear visibility into async backend execution
4. **recorder_backend_async.py** does not hang and processes audio correctly
5. **recorder_tui.py** provides a stable, responsive interface
6. **"Start new note"** workflow functions as designed
7. **All tests pass** consistently

---

## Notes and Considerations

- **Backward Compatibility**: Ensure that existing behavior (microphone recording) still works after each phase
- **Test Isolation**: Tests should not interfere with each other (use separate session directories)
- **Async Patterns**: Be careful with blocking operations in async code; always use `run_in_executor` for I/O
- **Resource Cleanup**: Ensure workers, queues, and streams are properly closed in all code paths
- **Textual Event Loop**: Textual runs its own asyncio event loop; ensure our backend integrates cleanly

---

## Current File Locations

- Working baseline: `src/palaver/recorder/vad_recorder.py`
- Async backend: `src/palaver/recorder/recorder_backend_async.py`
- Textual UI: `src/palaver/tui/recorder_tui.py`
- Existing test: `tests/test_recorder.py` (currently hangs)
- Test audio: `tests/audio_samples/note1.wav`

---

## Revision History

- **2025-12-03**: Initial plan created based on project requirements
