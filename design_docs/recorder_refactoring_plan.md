# Recorder Refactoring and Testing Plan

## Project Context

**Problem**: The new async recorder backend (`recorder_backend_async.py`) hangs when started via the Textual UI (`recorder_tui.py`). The hang is severe enough to require killing the terminal.

**Working Baseline**: The original `vad_recorder.py` works correctly and serves as our reference implementation.

**Goal**: Refactor both recorders to support testing with pre-recorded audio files, add comprehensive logging, fix the async backend, and ensure the TUI works reliably.

## High-Level Checklist

- [x] **Phase 1**: Refactor `vad_recorder.py` to support audio file input ✅ COMPLETED
- [~] **Phase 2**: Create and validate test for `vad_recorder.py` using `tests/audio_samples/note1.wav` 🔄 IN PROGRESS (Tasks 2.1-2.2 done)
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
- [ ] **CRITICAL**: Explicitly verify "start new note" → long note mode (5s silence) workflow:
  - Does the recorder detect "start new note" command in transcription?
  - Does it switch to 5-second silence threshold?
  - Does it properly end the note after 5 seconds of silence?
  - **NOTE**: This feature was tested with microphone input (2025-12-03) and did NOT work correctly - the note did not end after 5 seconds of silence. If file-based test works, we can use it to debug the microphone version.
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

## Progress Report - 2025-12-03 End of Day

### Phase 1: ✅ COMPLETED

**Objective**: Refactor `vad_recorder.py` to support dual input (device OR file)

#### What Was Accomplished

**Task 1.1: Input Abstraction Design** ✅
- Designed `AudioSource` protocol with context manager support
- Created separate `DeviceAudioSource` and `FileAudioSource` classes
- Maintains same callback interface for both sources
- Design approved and documented in `src/palaver/recorder/audio_sources.py`

**Task 1.2: WAV File Reader Implementation** ✅
- Implemented `FileAudioSource` class (audio_sources.py:79-189)
- Features:
  - Automatic resampling (supports 22050 Hz → 48000 Hz)
  - Mono → stereo conversion
  - Real-time playback simulation (sleep between chunks)
  - Support for 16-bit and 32-bit WAV files
  - Background thread matches sounddevice model
- Error handling for missing files and invalid formats

**Task 1.3: Main Recording Loop Modification** ✅
- Modified `vad_recorder.py` to use audio source abstraction
- Added `--input` command-line argument
- Created `create_audio_source()` factory function
- Backward compatible: microphone still works as before
- Created `run_vad_recorder.sh` wrapper script using `uv`

**Task 1.4: Session Management Updates** ✅
- Manifest now includes `input_source` metadata:
  - `type`: "device" or "file"
  - `source`: device name or file path
- Allows distinguishing test sessions from production recordings

**Files Created/Modified**:
- ✅ `src/palaver/recorder/audio_sources.py` (new, 244 lines)
- ✅ `src/palaver/recorder/vad_recorder.py` (modified, added file support)
- ✅ `run_vad_recorder.sh` (new, wrapper script)

**Testing**:
- ✅ Device input tested and working (no regression)
- ✅ Import and command-line interface verified
- ⚠️ Discovered issue: microphone long note mode doesn't terminate correctly (5s silence not detected)

---

### Phase 2: 🔄 IN PROGRESS (Task 2.2 completed)

**Objective**: Create and validate test for `vad_recorder.py` using test audio file

#### What Was Accomplished

**Task 2.1: Examine Test Audio File** ✅
- Examined `tests/audio_samples/note1.wav`:
  - 22050 Hz, mono, 16-bit, 9.01 seconds
  - RMS level 4435 (good signal)
- Reviewed `piper.sh` to understand content
- **Discovery**: Original file used `--sentence-silence 1` (insufficient for testing 5s threshold)
- Documented file properties in `tests/audio_samples/README.md`
- Created expected behavior document: `tests/audio_samples/note1_expected_behavior.md`

**Task 2.2: Create Initial Test** ✅
- Created `tests/test_vad_recorder_file.py`
- Features:
  - Uses `monkeypatch` to mock stdin (no manual interaction)
  - Validates manifest, transcript files, segments
  - Checks for "start a new note" command in transcript
  - Expects 3-5 segments (ideally 4)
  - Displays results for manual verification
- Test successfully ran and passed

**Files Created**:
- ✅ `tests/test_vad_recorder_file.py` (new, test implementation)
- ✅ `tests/audio_samples/README.md` (new, documentation)
- ✅ `tests/audio_samples/note1_expected_behavior.md` (new, expected behavior)

**Issues Discovered**:
- ⚠️ Microphone input: long note mode doesn't end after 5s silence (observed during device testing)
- ⚠️ File vs microphone behavior may differ (file has perfect digital silence, microphone has ambient noise)

---

### Critical Discovery: Test Audio Generation Problem

#### The Problem

**Issue**: Piper's `--sentence-silence` parameter applies **uniformly** to all sentences.

**Impact**: Cannot create test files with mixed silence patterns like:
- Short silence (1s) between body sentences (natural speech)
- Long silence (6s) at end to trigger note termination

**Why This Matters**:
- Testing requires precise silence control for VAD thresholds
- Need different silence durations for different interaction types
- Pattern needed for creating tests for **many future interaction types** beyond notes

#### The Solution: Test Audio Generation Toolkit

**Design Decision**: Two-stage generation process
1. Generate speech with Piper (uniform short silences OR no silence)
2. Manipulate WAV files to add precise silence where needed

**Tools Created**:

1. **`tools/wav_utils.py`** - Core WAV manipulation utility (273 lines)
   - `append_silence()`: Add silence to end of file
   - `concatenate_wavs()`: Join multiple WAV files with precise silence control
   - `read_wav()`, `write_wav()`: Low-level WAV I/O
   - `create_silence()`: Generate digital silence
   - Command-line interface and Python API

2. **`tools/generate_note_test.sh`** - Simple test file generator
   - Generates speech with Piper (1s between sentences)
   - Appends 6s final silence using wav_utils
   - One-command creation of properly structured test files

3. **`tools/generate_test_audio_example.py`** - Advanced patterns (172 lines)
   - Example: Single note workflow
   - Example: Multi-note workflow
   - Example: Custom interaction patterns
   - Templates for creating new test scenarios

4. **`tools/README.md`** - Comprehensive documentation (312 lines)
   - Usage patterns and examples
   - Design guidelines for test files
   - VAD testing guidelines (normal 0.8s vs long 5s thresholds)
   - Tips for debugging audio files
   - Future enhancement ideas

**Usage Examples**:

```bash
# Simple: append silence to existing file
python tools/wav_utils.py append input.wav output.wav --silence 6.0

# Advanced: concatenate with precise silence control
python tools/wav_utils.py concat seg1.wav seg2.wav seg3.wav \
    -o output.wav --silence 1.0 1.0 6.0
```

```python
# Python API for programmatic generation
from tools.wav_utils import concatenate_wavs

concatenate_wavs(
    input_wavs=["command.wav", "title.wav", "body1.wav", "body2.wav"],
    output_wav="test.wav",
    silence_between=[1.0, 1.0, 1.0, 6.0]  # Precise control
)
```

**Files Created**:
- ✅ `tools/wav_utils.py` (273 lines)
- ✅ `tools/generate_note_test.sh` (bash script)
- ✅ `tools/generate_test_audio_example.py` (172 lines)
- ✅ `tools/README.md` (312 lines, comprehensive guide)

**Benefits**:
- ✅ Precise control over silence duration for VAD testing
- ✅ Reusable pattern for all future interaction types
- ✅ Programmatic test generation (can create variations)
- ✅ Documented and extensible

---

### Understanding Note Body End Detection

**Key Discovery**: Note body end is detected **purely by VAD silence**, NOT by transcription content.

**The Workflow** (documented in `design_docs/note_body_detection_explanation.md`):

1. **Normal mode**: 0.8s silence threshold
2. **"start new note" detected** in transcription → queue switch to long_note mode
3. **Next segment**: capture title, apply mode switch
4. **Long note mode**: 5s silence threshold
5. **VAD detects 5s+ silence** → segment ends
6. **Automatic check**: "in long_note mode?" → YES → queue switch back to normal
7. **Next segment**: normal mode restored

**Critical Code** (vad_recorder.py:373-379):
```python
if vad_mode == "long_note":
    switch_vad_mode("normal")  # Automatic after ANY segment in long mode
```

**Implications**:
- ⚠️ **Current Issue**: Switches back to normal after EVERY segment in long note mode
- ⚠️ Cannot speak multiple body paragraphs with natural pauses
- ⚠️ File input works (perfect digital silence), microphone may not (ambient noise)

**File Created**:
- ✅ `design_docs/note_body_detection_explanation.md` (comprehensive workflow explanation)

---

### Updated File Tree

```
palaver/
├── src/palaver/recorder/
│   ├── vad_recorder.py (MODIFIED - dual input support)
│   ├── audio_sources.py (NEW - input abstraction)
│   ├── recorder_backend_async.py (unchanged)
│   └── ...
├── tests/
│   ├── test_vad_recorder_file.py (NEW - file input test)
│   ├── test_recorder.py (existing, still hangs)
│   └── audio_samples/
│       ├── note1.wav (test file)
│       ├── README.md (NEW - documentation)
│       └── note1_expected_behavior.md (NEW - expected results)
├── tools/ (NEW DIRECTORY)
│   ├── wav_utils.py (NEW - WAV manipulation)
│   ├── generate_note_test.sh (NEW - simple generator)
│   ├── generate_test_audio_example.py (NEW - advanced patterns)
│   └── README.md (NEW - comprehensive guide)
├── design_docs/
│   ├── recorder_refactoring_plan.md (this file)
│   └── note_body_detection_explanation.md (NEW - workflow doc)
└── run_vad_recorder.sh (NEW - uv wrapper)
```

---

### Phase 1 & 2 Summary Statistics

**Code Written**:
- 5 new Python files (944 lines total)
- 2 shell scripts
- 5 documentation files (1000+ lines)

**Tests Created**:
- 1 pytest test file (working)

**Tools Created**:
- Complete test audio generation toolkit
- Reusable for all future interaction types

**Documentation**:
- Input abstraction design
- Audio generation patterns
- Note detection workflow explained
- Test file expected behaviors

---

### Next Steps (Phase 2 continuation)

**Remaining Tasks**:

**Task 2.3: Run Test and Analyze Results** - READY
- Test framework exists
- Need to verify segment count and transcriptions
- **Critical**: Verify long note mode activation/deactivation
- Compare to microphone behavior

**Task 2.4: Iterate on VAD Parameters** - IF NEEDED
- Adjust thresholds based on test results
- May need to tune for file vs microphone differences

**Task 2.5: Validate Transcription Pipeline** - READY
- Test exists, can verify transcription quality
- Check for "Clerk," prefix filtering needs

**Blockers**: None

**Recommendations for Next Session**:
1. Generate fresh `note1.wav` using `tools/generate_note_test.sh`
2. Run `tests/test_vad_recorder_file.py` and analyze results
3. Verify long note mode workflow (the critical test)
4. If file test works but microphone doesn't, investigate ambient noise issues
5. Proceed to Phase 3 (logging in async backend)

---

### Tool Usage Instructions (For Reference)

#### Creating Test Audio Files

**Quick Method** (recommended for note workflow):
```bash
./tools/generate_note_test.sh
```

**Custom Silence Control**:
```bash
# Generate base audio with Piper
echo "Sentence 1. Sentence 2. Sentence 3." | \
    uv run piper --model models/en_US-lessac-medium.onnx \
                 --sentence-silence 1 \
                 --output_file base.wav

# Append 6 seconds of silence to end
python tools/wav_utils.py append base.wav final.wav --silence 6.0
```

**Multi-Segment with Precise Control**:
```python
from tools.wav_utils import concatenate_wavs

# Each segment generated separately
segments = ["seg1.wav", "seg2.wav", "seg3.wav", "seg4.wav"]

# Precise silence after each: 1s, 1s, 1s, 6s
concatenate_wavs(segments, "output.wav", silence_between=[1.0, 1.0, 1.0, 6.0])
```

**Programmatic Generation** (for test variations):
```python
import subprocess
from pathlib import Path
from tools.wav_utils import concatenate_wavs

def generate_segment(text: str, output: Path):
    """Generate single speech segment with Piper"""
    subprocess.run(
        ["uv", "run", "piper",
         "--model", "models/en_US-lessac-medium.onnx",
         "--sentence-silence", "0",  # No silence (we'll add manually)
         "--output_file", str(output)],
        input=text.encode()
    )

# Generate test scenario
phrases = ["Command.", "Title.", "Body 1.", "Body 2."]
temp_dir = Path("temp")
temp_dir.mkdir(exist_ok=True)

segment_files = []
for i, phrase in enumerate(phrases):
    output = temp_dir / f"seg{i}.wav"
    generate_segment(phrase, output)
    segment_files.append(output)

# Combine with custom silence pattern
concatenate_wavs(
    segment_files,
    "test.wav",
    silence_between=[1.0, 1.0, 1.0, 6.0]
)
```

**Design Guidelines** (from tools/README.md):

For VAD Testing:
- **Normal mode (0.8s threshold)**:
  - 0.5-0.7s silence: should NOT trigger segment end
  - 1.0-1.5s silence: SHOULD trigger segment end
  - Avoid testing at exactly 0.8s (flaky)

- **Long note mode (5.0s threshold)**:
  - 1.0-3.0s silence: should NOT trigger segment end
  - 6.0-8.0s silence: SHOULD trigger segment end
  - Avoid testing at exactly 5.0s (flaky)

For "Clerk," Prefix:
- Add to all segments to work around VAD speech-start quirk
- Document that filtering is expected in transcription processing
- Keep consistent in test files

**Reference Documentation**:
- Full guide: `tools/README.md`
- Examples: `tools/generate_test_audio_example.py`
- Note workflow: `design_docs/note_body_detection_explanation.md`

---

## Revision History

- **2025-12-03**: Initial plan created based on project requirements
- **2025-12-03 EOD**: Completed Phase 1 and partial Phase 2; discovered and solved test audio generation challenges
