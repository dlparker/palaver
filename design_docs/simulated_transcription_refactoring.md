# Simulated Transcription & Architecture Refactoring

**Status**: Design Phase
**Created**: 2025-12-04
**Goal**: Enable fast testing of downstream text processing by adding simulated transcription mode

---

## Problem Statement

Current testing requires:
1. **Real audio processing** (VAD, resampling, etc.)
2. **Real transcription** (whisper-cli, multiprocess workers, ~2-5s per segment)
3. **Total test time**: ~20+ seconds per test run

**Impact**:
- Slow development cycle for command detection, mode switching, and future text processing features
- Cannot easily unit test text processing logic in isolation
- Difficult to test edge cases (specific transcription patterns, timing, etc.)

**Proposed Solution**:
Add a **Simulated Transcription** mode that bypasses audio/transcription and directly injects text segments, enabling:
- Fast tests (milliseconds instead of seconds)
- Deterministic text inputs for testing
- Easy edge case testing
- Rapid iteration on downstream text processing

---

## Three Operating Modes

### 1. **Live Microphone Mode** (Existing)
- Audio: Real-time microphone input
- VAD: Active (Silero VAD)
- Transcription: Real (whisper-cli)
- Use Case: Production, manual testing

### 2. **File Playback Mode** (Existing)
- Audio: Pre-recorded WAV file
- VAD: Active (Silero VAD)
- Transcription: Real (whisper-cli)
- Use Case: Integration testing, debugging VAD behavior

### 3. **Simulated Mode** (NEW)
- Audio: None (or synthetic timing events)
- VAD: Bypassed
- Transcription: Simulated (pre-defined text)
- Use Case: Fast unit tests, text processing tests, command detection tests

---

## Proposed Architecture

### Current Architecture Issues

**`vad_recorder.py` is monolithic** (~600 lines):
- Audio input handling
- VAD processing
- Transcription coordination
- Result collection
- Command detection
- Mode switching
- Session/manifest management

**Hard to test** individual components in isolation.

### Proposed Module Structure

```
src/palaver/recorder/
├── audio_sources.py          # Audio input abstraction (existing, extend)
├── transcription.py          # NEW: Transcription abstraction
├── text_processor.py         # NEW: Text processing, command detection
├── session.py                # NEW: Session management, manifest
├── vad_recorder.py           # REFACTOR: Main coordinator (simplified)
└── action_phrases.py         # Existing: Command matching
```

---

## Detailed Module Design

### 1. `audio_sources.py` (Extend Existing)

**Current**: `AudioSource` protocol with `DeviceAudioSource` and `FileAudioSource`

**Add**: `SimulatedAudioSource`

```python
class SimulatedAudioSource(AudioSource):
    """
    Simulated audio source that yields pre-defined segments with timing.

    Does not produce actual audio data, but triggers segment callbacks
    with simulated timing to test downstream processing.
    """

    def __init__(self, segments: List[SimulatedSegment], realtime: bool = False):
        """
        Args:
            segments: List of (text, duration_sec) tuples
            realtime: If True, simulate timing delays; if False, run immediately
        """
        self.segments = segments
        self.realtime = realtime

    def start_streaming(self, callback):
        """
        Trigger segment callbacks without actual audio processing.

        For each segment:
        1. Wait for duration (if realtime=True)
        2. Call callback with segment event (contains index, not audio)
        3. Move to next segment
        """
        pass
```

**Design Note**: SimulatedAudioSource doesn't produce audio chunks. Instead, it triggers "segment complete" events directly, bypassing VAD.

---

### 2. `transcription.py` (NEW)

**Purpose**: Abstract transcription away from audio processing

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

@dataclass
class TranscriptionResult:
    """Result of transcribing a segment."""
    segment_index: int
    text: str
    success: bool
    duration_sec: float
    error_msg: Optional[str] = None

class Transcriber(ABC):
    """Base class for transcription backends."""

    @abstractmethod
    def transcribe(self, segment_index: int, audio_path: Path) -> TranscriptionResult:
        """Transcribe a single segment."""
        pass

    @abstractmethod
    def start(self):
        """Initialize transcription backend (workers, models, etc.)."""
        pass

    @abstractmethod
    def stop(self):
        """Cleanup transcription backend."""
        pass


class WhisperTranscriber(Transcriber):
    """
    Real transcription using whisper-cli with multiprocess workers.

    Moves existing transcription worker logic here.
    """

    def __init__(self, num_workers: int = 2, model_path: str = None):
        self.num_workers = num_workers
        self.model_path = model_path
        self.job_queue = None
        self.result_queue = None
        self.workers = []

    def start(self):
        """Start worker processes."""
        # Move existing worker startup logic here
        pass

    def transcribe(self, segment_index: int, audio_path: Path) -> TranscriptionResult:
        """Queue job and wait for result."""
        # Async queuing mechanism
        pass


class SimulatedTranscriber(Transcriber):
    """
    Simulated transcription that returns pre-defined text.

    For testing downstream text processing without actual transcription.
    """

    def __init__(self, transcripts: Dict[int, str]):
        """
        Args:
            transcripts: Map of segment_index -> text
                         e.g., {0: "start new note", 1: "My Title", 2: "Body text"}
        """
        self.transcripts = transcripts

    def start(self):
        """No-op for simulated mode."""
        pass

    def stop(self):
        """No-op for simulated mode."""
        pass

    def transcribe(self, segment_index: int, audio_path: Path) -> TranscriptionResult:
        """
        Return pre-defined text immediately (no actual transcription).

        Args:
            segment_index: Which segment to transcribe
            audio_path: Ignored (no actual audio processing)

        Returns:
            TranscriptionResult with pre-defined text
        """
        text = self.transcripts.get(segment_index, "[no transcript defined]")
        return TranscriptionResult(
            segment_index=segment_index,
            text=text,
            success=True,
            duration_sec=0.0  # Instant
        )
```

**Key Insight**: In simulated mode, we still need to save WAV files (even if empty/dummy) to maintain the segment index consistency, OR we refactor to not require WAV files at all (better).

---

### 3. `text_processor.py` (NEW)

**Purpose**: Extract all text processing logic from vad_recorder.py

```python
from pathlib import Path
from typing import Optional, Callable
from palaver.recorder.action_phrases import LooseActionPhrase
from palaver.recorder.transcription import TranscriptionResult

class TextProcessor:
    """
    Processes transcribed text segments and detects commands.

    Handles:
    - Incremental transcript writing
    - Command detection (ActionPhrase matching)
    - Title capture
    - Mode switching callbacks
    - State machine for note workflow
    """

    def __init__(self,
                 session_dir: Path,
                 mode_change_callback: Optional[Callable[[str], None]] = None):
        self.session_dir = session_dir
        self.mode_change_callback = mode_change_callback

        # State machine
        self.waiting_for_title = False
        self.current_note_title = None

        # Action phrases
        self.start_note_phrase = LooseActionPhrase(
            pattern="start new note",
            threshold=0.66,
            ignore_prefix=r'^(clerk|lurk|clark|plurk),?\s*'
        )

        # Files
        self.transcript_path = session_dir / "transcript_raw.txt"
        self.incremental_path = session_dir / "transcript_incremental.txt"

        # Results tracking
        self.results = {}

    def process_result(self, result: TranscriptionResult):
        """
        Process a transcription result.

        1. Store result
        2. Write incremental update
        3. Check for commands
        4. Update state machine
        5. Trigger callbacks
        """
        self.results[result.segment_index] = result
        self._write_incremental(result)
        self._check_commands(result)

    def _write_incremental(self, result: TranscriptionResult):
        """Write incremental transcript update."""
        # Move existing logic from ResultCollector
        pass

    def _check_commands(self, result: TranscriptionResult):
        """
        Check for commands in transcribed text.

        State machine:
        1. Normal -> "start new note" detected -> waiting_for_title
        2. waiting_for_title -> next segment -> capture title -> long_note mode
        3. long_note -> segment ends -> queue return to normal
        """
        if not result.success or not self.mode_change_callback:
            return

        # State 1: Check for "start new note" command
        match_score = self.start_note_phrase.match(result.text)

        if not self.waiting_for_title and match_score > 0:
            self.waiting_for_title = True
            print("\n" + "="*70)
            print("📝 NEW NOTE DETECTED")
            print(f"   Command matched: {result.text}")
            print("Please speak the title for this note...")
            print("="*70 + "\n")

        # State 2: Capture the title
        elif self.waiting_for_title:
            self.waiting_for_title = False
            self.current_note_title = result.text

            # Switch to long note mode
            self.mode_change_callback("long_note")
            print("\n" + "="*70)
            print(f"📌 TITLE: {result.text}")
            print("🎙️  LONG NOTE MODE ACTIVATED")
            print("Silence threshold: 5 seconds (continue speaking...)")
            print("="*70 + "\n")

    def finalize(self):
        """Write final transcript."""
        # Write transcript_raw.txt with ordered results
        pass
```

**Benefit**: This module can be tested independently with mock TranscriptionResults, no audio needed!

---

### 4. `session.py` (NEW - Optional)

**Purpose**: Session management, manifest writing

```python
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Dict, Any
import json

class Session:
    """
    Manages a recording session.

    Handles:
    - Session directory creation
    - Manifest generation
    - Metadata tracking
    """

    def __init__(self, base_dir: Path = Path("sessions")):
        self.base_dir = base_dir
        self.session_dir = None
        self.start_time = None
        self.metadata = {}

    def create(self) -> Path:
        """Create new session directory."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.session_dir = self.base_dir / timestamp
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.start_time = datetime.now(timezone.utc)
        return self.session_dir

    def add_metadata(self, key: str, value: Any):
        """Add metadata to session."""
        self.metadata[key] = value

    def write_manifest(self, segments: List[Dict], kept_indices: List[int]):
        """Write manifest.json."""
        manifest = {
            "session_start_utc": self.start_time.isoformat(),
            "total_segments": len(kept_indices),
            **self.metadata,
            "segments": [
                {
                    "index": i,
                    "file": f"seg_{i:04d}.wav",
                    **segments[i]
                }
                for i in kept_indices
            ]
        }
        manifest_path = self.session_dir / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2))
```

---

### 5. `vad_recorder.py` (REFACTOR - Simplified)

**Purpose**: Main coordinator, simplified to orchestrate components

```python
from palaver.recorder.audio_sources import create_audio_source
from palaver.recorder.transcription import Transcriber, WhisperTranscriber, SimulatedTranscriber
from palaver.recorder.text_processor import TextProcessor
from palaver.recorder.session import Session

def main(mode: str = "microphone",
         input_source: Optional[str] = None,
         transcriber: Optional[Transcriber] = None,
         simulated_segments: Optional[List[Tuple[str, float]]] = None):
    """
    Main recorder entry point.

    Args:
        mode: "microphone", "file", or "simulated"
        input_source: Path to audio file (for file mode)
        transcriber: Custom transcriber (for testing)
        simulated_segments: List of (text, duration) for simulated mode
    """

    # 1. Create session
    session = Session()
    session_dir = session.create()

    # 2. Setup audio source
    if mode == "simulated":
        audio_source = SimulatedAudioSource(simulated_segments)
        session.add_metadata("input_source", {"type": "simulated"})
    elif mode == "file":
        audio_source = FileAudioSource(input_source)
        session.add_metadata("input_source", {"type": "file", "source": input_source})
    else:  # microphone
        audio_source = DeviceAudioSource()
        session.add_metadata("input_source", {"type": "device"})

    # 3. Setup transcriber
    if transcriber is None:
        if mode == "simulated":
            # Extract text from simulated_segments
            transcripts = {i: text for i, (text, _) in enumerate(simulated_segments)}
            transcriber = SimulatedTranscriber(transcripts)
        else:
            transcriber = WhisperTranscriber(num_workers=2)

    # 4. Setup text processor
    text_processor = TextProcessor(
        session_dir=session_dir,
        mode_change_callback=lambda mode: switch_vad_mode(mode)
    )

    # 5. Start components
    transcriber.start()

    # 6. Run recording loop (simplified)
    if mode == "simulated":
        # No VAD, just process segments directly
        for i, (text, duration) in enumerate(simulated_segments):
            result = transcriber.transcribe(i, None)
            text_processor.process_result(result)
    else:
        # Real VAD processing (existing logic)
        run_vad_loop(audio_source, transcriber, text_processor)

    # 7. Cleanup
    transcriber.stop()
    text_processor.finalize()
    session.write_manifest(segments, kept_indices)
```

**Key Changes**:
- Component composition instead of monolithic structure
- Easy to inject mocks/fakes for testing
- Clear separation of concerns

---

## Testing Strategy

### Directory Structure

```
tests/                          # Existing, slow integration tests
├── test_vad_recorder_file.py   # ~20s (real VAD + transcription)
├── test_recorder.py            # Other slow tests
└── test_action_phrases.py      # ~0.1s (unit tests, keep here)

tests_fast/                     # NEW: Fast unit/integration tests
├── __init__.py
├── test_simulated_recorder.py  # Full workflow with simulation
├── test_text_processor.py      # Text processing in isolation
├── test_action_matching.py     # Command detection
├── test_transcription.py       # Transcriber implementations
└── fixtures.py                 # Shared test fixtures
```

### Test Execution

```bash
# Run only fast tests
uv run pytest tests_fast/ -v

# Run only slow tests
uv run pytest tests/test_vad_recorder_file.py tests/test_recorder.py -v

# Run all tests
uv run pytest tests/ tests_fast/ -v
```

### pytest.ini Configuration

```ini
[pytest]
# Fast tests are default
testpaths = tests_fast

# Markers for organizing tests
markers =
    slow: marks tests as slow (deselect with '-m "not slow"')
    fast: marks tests as fast
    integration: marks tests as integration tests
    unit: marks tests as unit tests
```

---

## Example: Fast Test with Simulation

```python
# tests_fast/test_simulated_recorder.py

from palaver.recorder.vad_recorder import main
from palaver.recorder.transcription import SimulatedTranscriber

def test_note_workflow_simulation():
    """Test complete note workflow with simulated transcription."""

    # Define test scenario
    simulated_segments = [
        ("clerk, start a new note", 2.0),
        ("Meeting Notes for Project Alpha", 2.5),
        ("We discussed the roadmap and decided to focus on feature X", 3.0),
    ]

    # Run recorder in simulated mode
    session_dir = main(
        mode="simulated",
        simulated_segments=simulated_segments
    )

    # Verify results
    transcript = (session_dir / "transcript_raw.txt").read_text()

    assert "start a new note" in transcript.lower()
    assert "Meeting Notes for Project Alpha" in transcript
    assert "roadmap" in transcript.lower()

    # Test runs in milliseconds, not 20+ seconds!
```

---

## Migration Plan

### Phase 1: Extract Components (No Breaking Changes)
**Goal**: Create new modules without changing existing behavior

**Tasks**:
1. Create `transcription.py` with `Transcriber` protocol and `WhisperTranscriber`
2. Create `text_processor.py` with `TextProcessor` class
3. Create `session.py` with `Session` class
4. Update `vad_recorder.py` to use new components internally (refactor, same interface)
5. Run existing tests to ensure no regressions

**Tests**: All existing tests should pass unchanged

**Duration**: 2-4 hours

---

### Phase 2: Add Simulated Mode
**Goal**: Implement SimulatedTranscriber and enable simulated mode

**Tasks**:
1. Implement `SimulatedTranscriber` in `transcription.py`
2. Add `SimulatedAudioSource` to `audio_sources.py`
3. Update `main()` to support `mode="simulated"` parameter
4. Handle simulated mode in recorder loop (skip VAD)

**Tests**: Create basic test in `tests_fast/test_simulated_recorder.py`

**Duration**: 1-2 hours

---

### Phase 3: Build Fast Test Suite
**Goal**: Create comprehensive fast tests

**Tasks**:
1. Create `tests_fast/` directory structure
2. Write `test_text_processor.py` - test command detection in isolation
3. Write `test_action_matching.py` - test action phrase matching with various inputs
4. Write `test_simulated_recorder.py` - end-to-end workflow tests
5. Update `pytest.ini` with markers and default testpaths

**Tests**: 20+ fast tests covering text processing logic

**Duration**: 2-3 hours

---

### Phase 4: Documentation & Polish
**Goal**: Update docs, add examples

**Tasks**:
1. Update `CLAUDE.md` with new architecture
2. Add simulated mode examples to README
3. Document fast testing approach in `tests_fast/README.md`
4. Add command reference for running fast vs slow tests

**Duration**: 1 hour

---

## Design Decisions & Rationale

### Why SimulatedAudioSource?

**Option A**: No audio source at all, just call text processor directly
**Option B**: SimulatedAudioSource that triggers segment events

**Chosen**: Option B

**Rationale**: Maintains consistency with real modes, tests the full coordination logic

---

### Why Extract TextProcessor?

**Benefit**: Can test command detection, state machine, and future text processing logic in isolation without any audio/transcription overhead.

**Example Future Use**: When adding more commands ("end note", "save note", "create task"), we can test all variations quickly:

```python
def test_all_note_commands():
    processor = TextProcessor(session_dir)

    # Test 20 different command variations in milliseconds
    for variation in command_variations:
        result = TranscriptionResult(text=variation, ...)
        processor.process_result(result)
        assert processor.waiting_for_title == True
```

---

### Why Separate tests/ and tests_fast/?

**Alternative**: Use pytest markers only (`@pytest.mark.slow`)

**Chosen**: Separate directories + markers

**Rationale**:
1. **Clear intent**: Developers know where to put new tests
2. **Easy filtering**: `pytest tests_fast/` is simpler than remembering marker syntax
3. **CI optimization**: Can run fast tests on every commit, slow tests less frequently
4. **Discoverability**: New contributors see two test suites and understand the distinction

---

## Future Enhancements

### 1. Multiple Commands Support
With TextProcessor extracted, easily add more commands:
- "end note" - Finish current note
- "cancel note" - Discard current note
- "save note as [name]" - Save with custom name
- "create task [description]" - Quick task capture

### 2. Command Chaining
"start a new note about the meeting then create a task to follow up with John"

### 3. Async Transcription
Current design supports swapping in async transcribers:
```python
class AsyncWhisperTranscriber(Transcriber):
    async def transcribe(self, segment_index, audio_path):
        # Non-blocking transcription
```

### 4. Transcript Post-Processing
```python
class TranscriptProcessor:
    def process(self, text: str) -> str:
        # Remove "Clerk," prefix
        # Fix common transcription errors
        # Apply custom transformations
```

### 5. Plugin Architecture
```python
text_processor.register_plugin(NoteCommandPlugin())
text_processor.register_plugin(TaskCommandPlugin())
text_processor.register_plugin(ReminderCommandPlugin())
```

---

## Risks & Mitigation

### Risk 1: Breaking Existing Tests
**Mitigation**: Phase 1 keeps same external interface, run tests continuously

### Risk 2: Over-Engineering
**Mitigation**: Each module has clear responsibility, only extract what's needed

### Risk 3: Simulated Mode Doesn't Match Real Behavior
**Mitigation**: Keep integration tests with real mode, use simulated only for text processing tests

### Risk 4: Complex Refactor Takes Too Long
**Mitigation**: Phased approach, each phase is independently valuable

---

## Success Criteria

**Phase 1 Success**:
- ✅ All existing tests pass
- ✅ Code is more modular
- ✅ No change in runtime behavior

**Phase 2 Success**:
- ✅ Simulated mode works end-to-end
- ✅ Can run basic note workflow test in <1 second

**Phase 3 Success**:
- ✅ 20+ fast tests covering text processing
- ✅ Fast test suite runs in <5 seconds total
- ✅ Easy to add new text processing tests

**Phase 4 Success**:
- ✅ Documentation is clear
- ✅ New developers understand architecture
- ✅ CI pipeline uses fast tests effectively

---

## Appendix: Key Interfaces

### TranscriptionResult
```python
@dataclass
class TranscriptionResult:
    segment_index: int
    text: str
    success: bool
    duration_sec: float
    error_msg: Optional[str] = None
```

### Transcriber Protocol
```python
class Transcriber(ABC):
    @abstractmethod
    def start(self): pass

    @abstractmethod
    def stop(self): pass

    @abstractmethod
    def transcribe(self, segment_index: int, audio_path: Path) -> TranscriptionResult: pass
```

### AudioSource Protocol (Existing)
```python
class AudioSource(Protocol):
    def start_streaming(self, callback: Callable[[bytes], None]): ...
    def stop_streaming(self): ...
```

### TextProcessor Interface
```python
class TextProcessor:
    def __init__(self, session_dir: Path, mode_change_callback: Callable): ...
    def process_result(self, result: TranscriptionResult): ...
    def finalize(self): ...
```

---

## Questions for Discussion

1. **Should we keep WAV files in simulated mode?**
   - Pro: Maintains consistency, manifest format unchanged
   - Con: Unnecessary overhead, could just skip segment files
   - **Recommendation**: Skip WAV files in simulated mode, update manifest to make `file` optional

2. **Should TextProcessor be synchronous or async?**
   - Current design is synchronous (simpler)
   - Future could support async for long-running text operations
   - **Recommendation**: Start synchronous, refactor to async only if needed

3. **Should we extract Session management now or later?**
   - Pro: Cleaner separation now
   - Con: Adds to refactor scope
   - **Recommendation**: Include in Phase 1, it's straightforward

4. **Test directory naming: `tests_fast/` or `tests_unit/`?**
   - `tests_fast/` - emphasizes speed (our main goal)
   - `tests_unit/` - emphasizes testing level
   - **Recommendation**: `tests_fast/` because some tests are integration-level but still fast due to simulation

---

**End of Design Document**
