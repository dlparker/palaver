# Text Processor CommandDoc Enhancement Plan

**Date:** 2025-12-04
**Status:** PLANNING
**Goal:** Transform text_processor from hardcoded "notes" to flexible CommandDoc system with configurable speech buckets

---

## Current Segment Control Design Review

### Architecture Overview

The current system has **three layers of segment control**:

#### Layer 1: Audio Chunking (Fixed, Hardware-Driven)
- **Chunk Size:** 30ms (0.03 seconds) @ 48kHz = 1,440 samples
- **Location:** `async_vad_recorder.py:32-33`
- **Controlled by:** Audio hardware callback rate
- **Purpose:** Minimal latency for VAD processing
- **User Control:** None (hardware constraint)

```python
CHUNK_SEC = 0.03
CHUNK_SIZE = int(CHUNK_SEC * RECORD_SR)  # 1440 samples
```

#### Layer 2: VAD Segment Detection (Dynamic, Speech-Driven)
- **Start:** When VAD detects speech above threshold
- **End:** When VAD detects silence exceeding `MIN_SILENCE_MS`
- **Duration:** Variable (typically 1-20 seconds)
- **Location:** `_audio_callback()` lines 513-578
- **Controlled by:** VAD threshold + silence duration
- **User Control:** Via VAD mode (normal vs long_note)

**Current VAD Modes:**
```python
# Normal mode
MIN_SILENCE_MS = 800          # 0.8 seconds silence triggers segment end
VAD_THRESHOLD = 0.5           # Speech detection sensitivity

# Long note mode
MIN_SILENCE_MS_LONG = 5000    # 5 seconds silence triggers segment end
VAD_THRESHOLD_LONG = 0.7      # Higher threshold (ignore ambient noise)
```

**How VAD Segments Work:**
1. Audio callback receives 30ms chunks continuously
2. Each chunk is downsampled to 16kHz and fed to VAD model
3. VAD returns `window` dict with `{"start": timestamp}` or `{"end": timestamp}`
4. On `start`: Create new segment, begin accumulating chunks
5. While speaking: Append each 30ms chunk to `self.segments[-1]` list
6. On `end`: Concatenate all chunks, check minimum duration (1.2s), emit `SpeechEnded` event

**Segment Accumulation** (line 580-584):
```python
# Accumulate audio while in speech
if self.in_speech:
    if not self.segments:
        self.segments.append([])
    self.segments[-1].append(chunk)  # Append 30ms chunk to current segment
```

**Key Insight:** VAD segments are **atomic units**. Once a segment ends, all accumulated chunks are concatenated into a single numpy array and sent for transcription as one job.

#### Layer 3: Transcription Processing (Per-Segment, Sequential)
- **Input:** Complete VAD segment (1-20 seconds of audio)
- **Process:** Whisper transcription (2-5 seconds latency)
- **Output:** Text string for entire segment
- **Location:** `text_processor.py`, triggered by `TranscriptionComplete` event
- **User Control:** None currently (all-or-nothing per segment)

**Current Flow:**
```
Segment #0 (2.3s) → Transcribe (3.1s) → "start a new note"
Segment #1 (1.8s) → Transcribe (2.4s) → "My Important Title"
Segment #2 (6.5s) → Transcribe (4.7s) → "This is the body of my note with lots of details..."
```

### The Problem With Current Design

**Issue:** User cannot monitor transcription progress during long segments.

**Example:** User speaks for 15 seconds in long_note mode (body of note). Current behavior:
1. User speaks continuously for 15s
2. User stops, waits 5s silence
3. Segment ends, entire 15s sent to transcription
4. User waits 5-8 seconds for transcription to complete
5. **Only then** does text appear

**User Experience:** 15s speaking + 5s silence + 7s transcription = **27 seconds** before seeing ANY text!

### Why segment_size Parameter is Needed

The `segment_size` parameter in `SpeechBucket` addresses this by **chunking long VAD segments into smaller transcription jobs**.

**Proposed Behavior Example:**
```python
# User config (example values)
Global.segment_size = 5.0  # 5 seconds (base value)

# SimpleNote CommandDoc
SpeechBucket(
    name="note_body",
    segment_size=0.5  # Relative: 0.5 × 5.0 = 2.5 seconds
)
```

**New Flow** (with chunking):
1. User speaks continuously for 15 seconds
2. **At 2.5s:** Chunk #1 sent to transcription (transcribes in background)
3. **At 5.0s:** Chunk #2 sent to transcription
4. **At 7.5s:** Chunk #3 sent to transcription
5. **At 10.0s:** Chunk #4 sent to transcription
6. **At 12.5s:** Chunk #5 sent to transcription
7. **At 15.0s:** User stops speaking
8. User waits 5s silence (can see chunks 1-4 already transcribed)
9. **At 20.0s:** Chunk #6 (final) sent to transcription
10. Text appears incrementally as each chunk completes

**User Experience:** See partial text every 2.5 seconds during speaking, instead of waiting 27 seconds!

### Implementation Challenge: VAD vs Transcription Chunking

**The Conflict:**
- VAD operates on **speech boundaries** (start/stop detection)
- Chunking operates on **time boundaries** (fixed intervals)

**Three Design Options:**

#### Option A: Keep VAD Segments Intact, Chunk During Transcription
**How:** VAD continues to work as-is. When a long segment completes, split the audio into chunks before transcription.

**Pros:**
- Minimal changes to audio callback (most critical code)
- VAD logic unchanged (proven stable)
- Chunking happens in async context (safe)

**Cons:**
- No real-time feedback during speaking (must wait for silence)
- User still waits full segment before seeing ANY text

**Example:**
```
User speaks 15s → Silence detected → Segment complete
Split into: [0-2.5s] [2.5-5s] [5-7.5s] [7.5-10s] [10-12.5s] [12.5-15s]
Transcribe all 6 chunks in parallel
```

#### Option B: Force Segment End at Time Boundaries
**How:** Audio callback monitors elapsed time. If chunk timer exceeds `segment_size`, force segment end even if still speaking.

**Pros:**
- True real-time chunking (text appears during speaking)
- Predictable chunk sizes

**Cons:**
- **Breaks VAD semantic boundaries** (mid-word/mid-sentence splits)
- Complex audio callback logic (more failure modes)
- Transcript quality may suffer (Whisper prefers complete phrases)
- May introduce audio artifacts at chunk boundaries

**Example:**
```
User speaking...
[0-2.5s chunk ends mid-sentence] → "This is a very import--"
[2.5-5s continues] → "ant meeting about the project"
```

#### Option C: Hybrid - VAD-Aware Opportunistic Chunking
**How:** Monitor elapsed time AND VAD state. When `segment_size` exceeded, force segment end at the **next natural VAD pause** (even if brief).

**Pros:**
- Respects speech boundaries (better transcript quality)
- Near real-time feedback (chunked at natural pauses)
- Safer than Option B (still uses VAD logic)

**Cons:**
- Chunk sizes less predictable (2-4s instead of exact 2.5s)
- More complex audio callback logic
- Still requires modifying critical VAD code

**Example:**
```
User speaking... (2.5s elapsed, timer triggered)
Wait for next VAD dip → [brief pause at 2.8s]
Chunk #1 ends → "This is a very important meeting."
User continues → [pause at 5.1s]
Chunk #2 ends → "We need to discuss the project timeline."
```

### Recommended Approach: **Option C (Hybrid)**

**Rationale:**
1. **Transcript Quality:** Whisper performs better with natural phrase boundaries
2. **User Experience:** Near real-time feedback without jarring mid-word splits
3. **Safety:** Still leverages proven VAD logic, just adds timeout logic
4. **Flexibility:** Chunk size becomes "target" not "exact" (acceptable for UX)

**Implementation Strategy:**
1. Add `chunk_timer` that tracks time since segment start
2. When `chunk_timer` exceeds bucket's `segment_size`:
   - Set `force_chunk_on_next_dip` flag
   - Wait for VAD to detect brief silence (even 100ms dip)
   - Force segment end at that point
3. If user speaks continuously for 2× `segment_size` without ANY dip:
   - Fall back to hard split (Option B) as safety valve

**Pseudo-code:**
```python
def _audio_callback(self, indata, ...):
    # ... existing VAD logic ...

    if self.in_speech:
        elapsed = time.time() - self.segment_start_time
        target_chunk_size = self.current_bucket.segment_size * GLOBAL_SEGMENT_SIZE

        if elapsed > target_chunk_size:
            self.force_chunk_on_next_dip = True

        if elapsed > 2 * target_chunk_size:
            # Safety: force hard split if no dips for 2× target
            self._force_segment_end()

    if window.get("end") and self.force_chunk_on_next_dip:
        # Even brief dip triggers chunk when timer exceeded
        self._end_segment_and_continue()
        self.force_chunk_on_next_dip = False
```

---

## Enhancement Plan Overview

### Goals

1. **Generalize** from hardcoded "notes" to flexible `CommandDoc` system
2. **Configurability** for attention_phrase, fuzzy matching, VAD parameters
3. **Slush bucket** for unmatched speech (future workflow flexibility)
4. **Event system** for UI feedback and extensibility
5. **Real-time chunking** for better UX during long speech

### Non-Goals

1. Dynamic plugin registry (deferred until design proven)
2. Command priority system (use list order)
3. Multi-language support (English only for now)

---

## Detailed Design

### 1. Configuration System

**File:** `src/palaver/config/recorder_config.yaml` (new)

```yaml
# Global recorder configuration
attention_phrase: "clerk"
attention_phrase_threshold: 80  # rapidfuzz similarity %

# Base timing values (in seconds)
# SpeechBucket parameters are relative to these
base_segment_size: 5.0         # Target chunk duration for real-time feedback
base_start_window: 2.0         # How long to wait for bucket to start
base_termination_silence: 0.8  # Silence duration to end bucket

# VAD settings
vad_threshold_normal: 0.5
vad_threshold_long: 0.7
min_segment_duration: 1.2

# Transcription
whisper_model: "models/multilang_whisper_large3_turbo.ggml"
num_workers: 2
whisper_timeout: 60

# Fuzzy matching
command_phrase_threshold: 80   # rapidfuzz similarity %
```

**Python API:**
```python
from palaver.config import RecorderConfig

config = RecorderConfig.from_file("config/recorder_config.yaml")
# Or use defaults
config = RecorderConfig.defaults()

# Access
config.attention_phrase  # "clerk"
config.base_segment_size  # 5.0
```

### 2. SpeechBucket Class

**File:** `src/palaver/commands/speech_bucket.py` (new)

```python
from dataclasses import dataclass
from typing import Optional

@dataclass
class SpeechBucket:
    """
    Specification for a speech input bucket within a CommandDoc workflow.

    Each bucket captures a portion of user speech (e.g., title, body, tags).
    Parameters are specified as RELATIVE multipliers of global base values.

    Example:
        # With global base_segment_size = 5.0 seconds
        SpeechBucket(
            name="title",
            display_name="Note Title",
            segment_size=0.4,  # 0.4 × 5.0 = 2.0 seconds (quick chunks)
            start_window=3.0,  # 3.0 × 2.0 = 6.0 seconds (generous wait)
            termination_silence=0.5  # 0.5 × 0.8 = 0.4 seconds (quick end)
        )
    """

    # Identity
    name: str              # Programmer-facing key (e.g., "note_title")
    display_name: str      # User-facing label (e.g., "Note Title")

    # Timing parameters (relative multipliers)
    segment_size: float = 1.0           # Chunking interval multiplier
    start_window: float = 1.0           # Timeout for first speech multiplier
    termination_silence: float = 1.0    # Silence duration multiplier

    def get_absolute_params(self, config: 'RecorderConfig') -> dict:
        """
        Convert relative parameters to absolute values.

        Returns:
            {
                'segment_size': 5.0,       # seconds
                'start_window': 2.0,       # seconds
                'termination_silence': 0.8 # seconds
            }
        """
        return {
            'segment_size': self.segment_size * config.base_segment_size,
            'start_window': self.start_window * config.base_start_window,
            'termination_silence': self.termination_silence * config.base_termination_silence,
        }

    def __post_init__(self):
        """Validate bucket configuration."""
        if not self.name or not self.name.replace('_', '').isalnum():
            raise ValueError(f"Invalid bucket name: {self.name}")
        if not self.display_name:
            raise ValueError("display_name cannot be empty")
        if self.segment_size <= 0:
            raise ValueError("segment_size must be positive")
        if self.start_window <= 0:
            raise ValueError("start_window must be positive")
        if self.termination_silence <= 0:
            raise ValueError("termination_silence must be positive")
```

### 3. CommandDoc Abstract Base Class

**File:** `src/palaver/commands/command_doc.py` (new)

```python
from abc import ABC, abstractmethod
from typing import List, Dict, Any
from pathlib import Path

class CommandDoc(ABC):
    """
    Abstract base class for command-driven document workflows.

    A CommandDoc defines:
    1. The command phrase that triggers it ("start new note")
    2. The sequence of speech buckets to fill (title, body, etc.)
    3. How to render the final output file(s)

    Subclasses implement specific document types (notes, emails, todos, etc.).
    """

    @property
    @abstractmethod
    def command_phrase(self) -> str:
        """
        Phrase that triggers this command.

        Examples: "start new note", "create reminder", "send email"
        """
        pass

    @property
    @abstractmethod
    def speech_buckets(self) -> List[SpeechBucket]:
        """
        Ordered list of speech buckets to fill.

        Example: [title_bucket, body_bucket, tags_bucket]
        """
        pass

    @abstractmethod
    def render(self, bucket_contents: Dict[str, str], output_dir: Path) -> List[Path]:
        """
        Generate output file(s) from filled buckets.

        Args:
            bucket_contents: {bucket_name: transcribed_text}
            output_dir: Session directory to write files

        Returns:
            List of created file paths

        Example:
            {
                "note_title": "My Important Meeting",
                "note_body": "Discussed project timeline and milestones..."
            }
            → writes "note_0001_my_important_meeting.md"
        """
        pass

    def validate_buckets(self):
        """
        Ensure bucket names are unique.
        Called during CommandDoc registration.
        """
        names = [b.name for b in self.speech_buckets]
        display_names = [b.display_name for b in self.speech_buckets]

        if len(names) != len(set(names)):
            raise ValueError(f"Duplicate bucket names in {self.command_phrase}")

        if len(display_names) != len(set(display_names)):
            raise ValueError(f"Duplicate display_names in {self.command_phrase}")
```

### 4. SimpleNote CommandDoc Implementation

**File:** `src/palaver/commands/simple_note.py` (new)

```python
from pathlib import Path
from typing import List, Dict
from palaver.commands.command_doc import CommandDoc
from palaver.commands.speech_bucket import SpeechBucket

class SimpleNote(CommandDoc):
    """
    Simple note-taking workflow: title + body.

    Replaces the hardcoded "note" system from text_processor.py.
    """

    @property
    def command_phrase(self) -> str:
        return "start new note"

    @property
    def speech_buckets(self) -> List[SpeechBucket]:
        return [
            SpeechBucket(
                name="note_title",
                display_name="Note Title",
                segment_size=0.4,         # 0.4 × 5.0 = 2.0s chunks (quick feedback)
                start_window=3.0,         # 3.0 × 2.0 = 6.0s wait (time to think)
                termination_silence=1.0   # 1.0 × 0.8 = 0.8s silence (normal mode)
            ),
            SpeechBucket(
                name="note_body",
                display_name="Note Body",
                segment_size=0.5,         # 0.5 × 5.0 = 2.5s chunks (real-time feedback)
                start_window=2.0,         # 2.0 × 2.0 = 4.0s wait
                termination_silence=6.25  # 6.25 × 0.8 = 5.0s silence (long mode)
            ),
        ]

    def render(self, bucket_contents: Dict[str, str], output_dir: Path) -> List[Path]:
        """
        Write note to markdown file.

        Filename: note_NNNN_slugified_title.md
        """
        title = bucket_contents.get("note_title", "Untitled")
        body = bucket_contents.get("note_body", "")

        # Generate slug for filename
        import re
        slug = re.sub(r'[^a-z0-9]+', '_', title.lower()).strip('_')[:50]

        # Find next note number
        existing = list(output_dir.glob("note_*.md"))
        note_num = len(existing) + 1

        filename = f"note_{note_num:04d}_{slug}.md"
        filepath = output_dir / filename

        # Write markdown
        content = f"# {title}\n\n{body}\n"
        filepath.write_text(content)

        return [filepath]
```

### 5. New Event Types

**File:** `src/palaver/recorder/async_vad_recorder.py` (additions)

```python
@dataclass
class CommandDetected(AudioEvent):
    """Command phrase matched, workflow starting."""
    command_doc_type: str      # "SimpleNote"
    command_phrase: str        # "start new note"
    matched_text: str          # "start a new note" (what user actually said)
    similarity_score: float    # 95.0 (rapidfuzz score)

@dataclass
class BucketStarted(AudioEvent):
    """Speech bucket became active and is waiting for input."""
    command_doc_type: str
    bucket_name: str           # "note_title"
    bucket_display_name: str   # "Note Title"
    bucket_index: int          # 0 (first bucket)
    start_window_sec: float    # 6.0 (timeout for first speech)

@dataclass
class BucketFilled(AudioEvent):
    """Speech bucket completed successfully."""
    command_doc_type: str
    bucket_name: str
    bucket_display_name: str
    text: str                  # Accumulated transcribed text
    duration_sec: float        # How long user spoke
    chunk_count: int           # Number of chunks accumulated

@dataclass
class BucketTimeout(AudioEvent):
    """Speech bucket timed out (no speech within start_window)."""
    command_doc_type: str
    bucket_name: str
    bucket_display_name: str
    elapsed_sec: float         # How long we waited

@dataclass
class CommandCompleted(AudioEvent):
    """All buckets filled, command completed successfully."""
    command_doc_type: str
    output_files: List[Path]  # Files created by render()

@dataclass
class CommandAborted(AudioEvent):
    """Command workflow aborted (timeout, user stopped recording, etc.)."""
    command_doc_type: str
    reason: str               # "bucket_timeout", "recording_stopped", etc.
    partial_buckets: Dict[str, str]  # Buckets that were filled before abort

@dataclass
class SlushBucketUpdated(AudioEvent):
    """Unmatched speech added to slush bucket."""
    text: str                 # New text added
    total_items: int          # Total segments in slush bucket
```

### 6. Enhanced TextProcessor

**File:** `src/palaver/recorder/text_processor.py` (major refactor)

**Key Changes:**
1. Replace hardcoded note detection with CommandDoc registry
2. Add attention_phrase matching with rapidfuzz
3. Add command_phrase matching with rapidfuzz
4. Implement slush bucket for unmatched speech
5. Implement bucket timeout logic
6. Emit new event types
7. Handle bucket-specific VAD parameter changes

**Pseudo-code Structure:**
```python
class TextProcessor:
    def __init__(
        self,
        session_dir: Path,
        result_queue: Queue,
        command_docs: List[CommandDoc],
        config: RecorderConfig,
        event_callback: Optional[Callable] = None
    ):
        self.session_dir = session_dir
        self.result_queue = result_queue
        self.config = config
        self.event_callback = event_callback

        # CommandDoc registry
        self.command_docs = self._validate_and_register(command_docs)

        # State machine
        self.state = "idle"  # idle, waiting_for_bucket, filling_bucket
        self.active_command: Optional[CommandDoc] = None
        self.current_bucket_index: int = 0
        self.bucket_contents: Dict[str, str] = {}
        self.bucket_start_time: Optional[float] = None

        # Slush bucket
        self.slush_bucket: List[str] = []

        # Output tracking
        self.note_counter = 0

    def _validate_and_register(self, command_docs: List[CommandDoc]) -> Dict[str, CommandDoc]:
        """Validate and build command_phrase → CommandDoc mapping."""
        registry = {}

        for doc in command_docs:
            # Validate buckets
            doc.validate_buckets()

            # Ensure unique command_phrase
            if doc.command_phrase in registry:
                raise ValueError(
                    f"Duplicate command_phrase: '{doc.command_phrase}' "
                    f"in {type(doc).__name__} and {type(registry[doc.command_phrase]).__name__}"
                )

            registry[doc.command_phrase] = doc

        return registry

    def _strip_attention_phrase(self, text: str) -> Optional[str]:
        """
        Remove attention_phrase from start of text if present (fuzzy match).

        Returns:
            Remaining text if attention_phrase matched, else None
        """
        from rapidfuzz import fuzz

        words = text.split()
        if not words:
            return None

        # Try matching first 1-2 words
        for n in [1, 2]:
            if n > len(words):
                break

            prefix = " ".join(words[:n]).lower()
            score = fuzz.ratio(prefix, self.config.attention_phrase.lower())

            if score >= self.config.attention_phrase_threshold:
                # Match! Strip and return remainder
                remainder = " ".join(words[n:]).strip()
                return remainder if remainder else None

        return None

    def _match_command(self, text: str) -> Optional[CommandDoc]:
        """
        Match text against registered command_phrases (fuzzy).

        Returns first match in registration order (poor man's priority).
        """
        from rapidfuzz import fuzz

        text_lower = text.lower()

        for phrase, doc in self.command_docs.items():
            score = fuzz.ratio(text_lower, phrase.lower())

            if score >= self.config.command_phrase_threshold:
                return doc

        return None

    def process_result(self, result: TranscriptionResult):
        """
        Process transcription result through state machine.

        States:
        - idle: No active command, check for attention_phrase + command
        - waiting_for_bucket: Command started, waiting for bucket to begin
        - filling_bucket: Accumulating text into current bucket
        """

        text = result.text.strip()
        if not text:
            return

        # Emit TranscriptionComplete event
        self._emit_event(TranscriptionComplete(...))

        # State machine
        if self.state == "idle":
            self._handle_idle_state(text)

        elif self.state == "waiting_for_bucket":
            self._handle_waiting_state(text)

        elif self.state == "filling_bucket":
            self._handle_filling_state(text)

    def _handle_idle_state(self, text: str):
        """
        Idle state: Look for attention_phrase + command_phrase.

        If no match, add to slush bucket.
        """

        # Try to strip attention_phrase (only when expecting command)
        remainder = self._strip_attention_phrase(text)

        if remainder:
            # Attention phrase matched, check remainder for command
            command_doc = self._match_command(remainder)

            if command_doc:
                # Command detected!
                self._start_command(command_doc, text, remainder)
                return
            else:
                # Attention phrase but no command → slush bucket
                self._add_to_slush_bucket(text)
                return

        # No attention phrase → check if text itself is a command
        # (Allows "start new note" without "clerk, start new note")
        command_doc = self._match_command(text)

        if command_doc:
            self._start_command(command_doc, text, text)
        else:
            # No match → slush bucket
            self._add_to_slush_bucket(text)

    def _start_command(self, doc: CommandDoc, original_text: str, matched_text: str):
        """Start command workflow, enter waiting_for_bucket state."""

        self.active_command = doc
        self.current_bucket_index = 0
        self.bucket_contents = {}
        self.bucket_start_time = time.time()
        self.state = "waiting_for_bucket"

        # Clear slush bucket (new command context)
        self.slush_bucket = []

        # Emit CommandDetected
        self._emit_event(CommandDetected(
            timestamp=time.time(),
            command_doc_type=type(doc).__name__,
            command_phrase=doc.command_phrase,
            matched_text=matched_text,
            similarity_score=fuzz.ratio(matched_text.lower(), doc.command_phrase.lower())
        ))

        # Start first bucket
        self._activate_bucket(0)

    def _activate_bucket(self, index: int):
        """Activate bucket at index, configure VAD, emit BucketStarted."""

        bucket = self.active_command.speech_buckets[index]
        params = bucket.get_absolute_params(self.config)

        # Emit BucketStarted
        self._emit_event(BucketStarted(
            timestamp=time.time(),
            command_doc_type=type(self.active_command).__name__,
            bucket_name=bucket.name,
            bucket_display_name=bucket.display_name,
            bucket_index=index,
            start_window_sec=params['start_window']
        ))

        # Request VAD parameter change
        # (async_vad_recorder will handle applying at segment boundary)
        self._request_vad_params(
            termination_silence_ms=int(params['termination_silence'] * 1000),
            segment_size_sec=params['segment_size']
        )

        self.bucket_start_time = time.time()
        self.state = "waiting_for_bucket"

    def _handle_waiting_state(self, text: str):
        """
        Waiting for bucket to start receiving speech.

        Check timeout, then transition to filling_bucket.
        """

        bucket = self.active_command.speech_buckets[self.current_bucket_index]
        params = bucket.get_absolute_params(self.config)
        elapsed = time.time() - self.bucket_start_time

        if elapsed > params['start_window']:
            # Timeout! Abort command
            self._abort_command("bucket_timeout", bucket.name)
            return

        # Got speech! Transition to filling
        self.bucket_contents[bucket.name] = text
        self.state = "filling_bucket"

    def _handle_filling_state(self, text: str):
        """
        Accumulating text into current bucket.

        Check for termination_silence (via SpeechEnded event).
        """

        bucket = self.active_command.speech_buckets[self.current_bucket_index]

        # Append text
        self.bucket_contents[bucket.name] += " " + text

    def on_speech_ended_long_silence(self):
        """
        Called by async_vad_recorder when termination_silence exceeded.

        Finalize current bucket, advance to next or complete command.
        """

        if self.state != "filling_bucket":
            return

        bucket = self.active_command.speech_buckets[self.current_bucket_index]
        text = self.bucket_contents[bucket.name]

        # Emit BucketFilled
        self._emit_event(BucketFilled(
            timestamp=time.time(),
            command_doc_type=type(self.active_command).__name__,
            bucket_name=bucket.name,
            bucket_display_name=bucket.display_name,
            text=text,
            duration_sec=time.time() - self.bucket_start_time,
            chunk_count=...  # Track this
        ))

        # Advance to next bucket or complete
        if self.current_bucket_index < len(self.active_command.speech_buckets) - 1:
            # More buckets
            self.current_bucket_index += 1
            self._activate_bucket(self.current_bucket_index)
        else:
            # All buckets filled!
            self._complete_command()

    def _complete_command(self):
        """All buckets filled, render output files, emit CommandCompleted."""

        output_files = self.active_command.render(
            self.bucket_contents,
            self.session_dir
        )

        self._emit_event(CommandCompleted(
            timestamp=time.time(),
            command_doc_type=type(self.active_command).__name__,
            output_files=output_files
        ))

        # Reset to idle
        self.active_command = None
        self.current_bucket_index = 0
        self.bucket_contents = {}
        self.state = "idle"

    def _abort_command(self, reason: str, context: str = ""):
        """Abort current command, dump to slush bucket, emit CommandAborted."""

        # Emit event
        self._emit_event(CommandAborted(
            timestamp=time.time(),
            command_doc_type=type(self.active_command).__name__,
            reason=f"{reason}: {context}",
            partial_buckets=self.bucket_contents.copy()
        ))

        # Dump everything to slush bucket
        # (command phrase + all filled bucket contents)
        for text in self.bucket_contents.values():
            self._add_to_slush_bucket(text)

        # Reset to idle
        self.active_command = None
        self.current_bucket_index = 0
        self.bucket_contents = {}
        self.state = "idle"

    def _add_to_slush_bucket(self, text: str):
        """Add unmatched text to slush bucket, emit event."""

        self.slush_bucket.append(text)

        self._emit_event(SlushBucketUpdated(
            timestamp=time.time(),
            text=text,
            total_items=len(self.slush_bucket)
        ))

    def finalize(self, total_segments: int):
        """
        Called when recording stops.

        Write slush bucket and generic transcript files.
        """

        # Write slush bucket
        if self.slush_bucket:
            slush_path = self.session_dir / "slush_bucket.txt"
            slush_path.write_text("\n\n".join(self.slush_bucket))

        # Write generic transcript (all transcribed text)
        # ... existing transcript_raw.txt logic ...
```

### 7. VAD Parameter Changes

**Challenge:** SpeechBuckets specify `termination_silence` and `segment_size`, but these affect VAD configuration.

**Current System:**
- VAD has two modes: `normal` (0.8s silence) and `long_note` (5.0s silence)
- Mode changes are queued and applied at segment boundaries
- Prevents race conditions between audio callback and event processor

**New System Requirements:**
1. Each bucket can specify different `termination_silence` (0.4s, 0.8s, 5.0s, etc.)
2. Each bucket can specify different `segment_size` for chunking
3. Must remain thread-safe (audio callback is sync, event processor is async)

**Design Decision:** Keep queued mode change pattern, but generalize to arbitrary parameters.

**New API:**

```python
# In TextProcessor
def _request_vad_params(self, termination_silence_ms: int, segment_size_sec: float):
    """Request VAD parameter change (applied at next segment boundary)."""

    # Callback to async_vad_recorder (thread-safe)
    if self.vad_params_callback:
        self.vad_params_callback(
            termination_silence_ms=termination_silence_ms,
            segment_size_sec=segment_size_sec
        )

# In AsyncVADRecorder
def _request_vad_params(self, termination_silence_ms: int, segment_size_sec: float):
    """Queue VAD parameter change (applied at next segment boundary)."""

    self.vad_params_requested = {
        'termination_silence_ms': termination_silence_ms,
        'segment_size_sec': segment_size_sec
    }

def _apply_vad_params(self):
    """Apply queued VAD parameters (called at segment start)."""

    if self.vad_params_requested:
        params = self.vad_params_requested

        # Recreate VAD with new silence threshold
        self.vad = create_vad_custom(
            min_silence_ms=params['termination_silence_ms'],
            threshold=self._choose_threshold(params['termination_silence_ms'])
        )

        # Set chunking timer
        self.target_chunk_size = params['segment_size_sec']

        # Emit event
        self._emit_event(VADParamsChanged(
            timestamp=time.time(),
            termination_silence_ms=params['termination_silence_ms'],
            segment_size_sec=params['segment_size_sec']
        ))

        self.vad_params_requested = None

def _choose_threshold(self, silence_ms: int) -> float:
    """
    Choose VAD threshold based on silence duration.

    Longer silence = higher threshold (ignore ambient noise).
    """
    if silence_ms >= 3000:
        return 0.7  # Long silence, ignore background
    else:
        return 0.5  # Normal sensitivity
```

### 8. Chunking Implementation (Hybrid Approach)

**Location:** `async_vad_recorder.py:_audio_callback()`

**New State:**
```python
class AsyncVADRecorder:
    def __init__(self, ...):
        # ... existing ...

        # Chunking state
        self.target_chunk_size: float = None  # Set by TextProcessor
        self.segment_start_time: float = None
        self.force_chunk_on_next_dip: bool = False
```

**Modified Callback:**
```python
def _audio_callback(self, indata, frames, time_info, status):
    # ... existing VAD logic ...

    if window:
        if window.get("start") is not None:
            # Speech started
            self.segment_start_time = time.time()
            self.force_chunk_on_next_dip = False
            # ... existing logic ...

        if window.get("end") is not None:
            # Speech ended (silence detected)

            # Check if we should force chunk due to timer
            if self.force_chunk_on_next_dip:
                # This is a forced chunk (opportunistic split)
                print(f" [CHUNK at {elapsed:.1f}s]", end="", flush=True)
                self._end_segment_chunk_and_continue()
                self.force_chunk_on_next_dip = False
                return

            # Normal segment end
            # ... existing logic ...

    # Monitor chunk timer while speaking
    if self.in_speech and self.target_chunk_size:
        elapsed = time.time() - self.segment_start_time

        if elapsed > self.target_chunk_size and not self.force_chunk_on_next_dip:
            # Target exceeded, wait for next VAD dip
            self.force_chunk_on_next_dip = True
            print(f" [chunk timer: {elapsed:.1f}s > {self.target_chunk_size:.1f}s]", end="", flush=True)

        if elapsed > 2 * self.target_chunk_size:
            # Safety: hard split if no dips for 2× target
            print(f" [HARD CHUNK at {elapsed:.1f}s]", end="", flush=True)
            self._force_segment_end_hard()
            return

def _end_segment_chunk_and_continue(self):
    """
    End current segment as a chunk, but immediately start new segment.

    Used for opportunistic chunking at VAD dips.
    """

    # End current segment (emit SpeechEnded)
    # ... similar to existing logic ...

    # Immediately start new segment (without waiting for VAD "start")
    self.in_speech = True
    self.segments.append([])
    self.segment_start_time = time.time()
    self.force_chunk_on_next_dip = False

    # Emit SpeechStarted for new chunk
    event = SpeechStarted(...)
    self._push_event(event)
```

---

## Migration Path

### Phase 1: Infrastructure (No Breaking Changes)
**Duration:** 2-3 days

1. Create new modules:
   - `src/palaver/config/recorder_config.py`
   - `src/palaver/commands/speech_bucket.py`
   - `src/palaver/commands/command_doc.py`
   - `src/palaver/commands/simple_note.py`

2. Add rapidfuzz dependency to `pyproject.toml`

3. Add new event types to `async_vad_recorder.py`

4. Create `config/recorder_config.yaml` with defaults

5. **Test:** All existing tests should pass (no behavior changes yet)

### Phase 2: TextProcessor Refactor
**Duration:** 3-4 days

1. Backup `text_processor.py` → `text_processor_legacy.py`

2. Rewrite `text_processor.py` with new architecture:
   - CommandDoc registry
   - State machine (idle, waiting, filling)
   - Attention phrase matching
   - Command phrase matching
   - Slush bucket
   - Event emission

3. Update `AsyncVADRecorder` to use new TextProcessor API

4. **Test:** Write unit tests for TextProcessor state machine

### Phase 3: VAD Parameter System
**Duration:** 2-3 days

1. Add `vad_params_requested` to AsyncVADRecorder

2. Implement `_request_vad_params()` callback

3. Implement `_apply_vad_params()` at segment boundaries

4. Add `VADParamsChanged` event

5. **Test:** Verify parameters change between buckets

### Phase 4: Chunking Implementation
**Duration:** 3-4 days

1. Add chunking state to AsyncVADRecorder

2. Implement hybrid chunking logic in `_audio_callback()`

3. Implement `_end_segment_chunk_and_continue()`

4. Add chunk tracking to events

5. **Test:** Verify chunks appear during long speech

### Phase 5: SimpleNote CommandDoc
**Duration:** 1-2 days

1. Implement `SimpleNote.render()` (markdown output)

2. Register SimpleNote in recorder startup

3. Remove legacy note detection code

4. **Test:** End-to-end note workflow

### Phase 6: TUI Integration
**Duration:** 2-3 days

1. Update TUI event handlers for new events:
   - `CommandDetected` → show "Command: {phrase}"
   - `BucketStarted` → show "Waiting for: {display_name}"
   - `BucketFilled` → show "✓ {display_name}"
   - `BucketTimeout` → show "⏱ Timeout: {display_name}"
   - `CommandCompleted` → show "✓ Complete: {files}"
   - `SlushBucketUpdated` → show count in status

2. Add bucket progress indicator

3. **Test:** Full interactive session with TUI

---

## Testing Strategy

### Unit Tests

**test_speech_bucket.py**
- Test parameter validation
- Test absolute parameter calculation
- Test invalid configurations

**test_command_doc.py**
- Test CommandDoc registration
- Test duplicate phrase detection
- Test bucket validation

**test_simple_note.py**
- Test render() output
- Test filename generation

**test_text_processor_state_machine.py**
- Test idle → command detection
- Test bucket transitions
- Test timeout handling
- Test slush bucket accumulation
- Test abort scenarios

**test_fuzzy_matching.py**
- Test attention_phrase variants ("clerk", "clear", "click")
- Test command_phrase variants ("start new note", "start a note")
- Test threshold tuning

### Integration Tests

**test_note_workflow_with_chunks.py**
- Record 15-second note body
- Verify chunks appear every 2.5s
- Verify all chunks transcribed correctly
- Verify final markdown file

**test_bucket_timeout.py**
- Start command
- Wait past start_window without speaking
- Verify CommandAborted event
- Verify slush bucket contains command phrase

**test_multiple_notes.py**
- Record 3 notes in one session
- Verify 3 markdown files
- Verify generic transcript contains all text

---

## Open Questions

### 1. Attention Phrase Position

**Current Spec:** Only match attention_phrase at segment start when expecting command.

**Question:** What if user says:
- "Um... clerk, start a new note" (filler before attention)
- "So, yeah, clerk, start a new note" (words before attention)

**Options:**
A. Strict start-of-segment only (current spec)
B. Allow attention phrase anywhere in first sentence
C. Strip common fillers ("um", "uh", "so") then check start

**Recommendation:** Start with A (strict), gather user feedback, iterate.

---

### 2. Chunk Boundary Artifacts

**Concern:** Whisper may produce inconsistent text at chunk boundaries.

**Example:**
- Chunk 1 ends: "We need to focus on the projectiles"
- Chunk 2 starts: "project timeline and deliverables"
- (Whisper guessed "projectiles" without full context)

**Mitigations:**
1. Hybrid chunking waits for VAD dips (reduces mid-word splits)
2. Overlap chunks by 0.5s for context (more complex)
3. Post-process to fix obvious errors (future enhancement)

**Recommendation:** Ship hybrid chunking, gather data on artifact frequency, iterate.

---

### 3. Slush Bucket UX

**Question:** What should user DO with slush bucket contents?

**Future Commands:**
- "summarize slush" → Generate summary of unmatched speech
- "add last to note" → Append last slush item to current note
- "clear slush" → Delete accumulated text

**Recommendation:** For initial release, just accumulate and write to file. Add commands in future iteration after seeing usage patterns.

---

## Success Criteria

1. ✅ User can say "clerk, start new note" → workflow begins
2. ✅ User sees transcript chunks every 2-3 seconds during long speech
3. ✅ Note title and body captured correctly
4. ✅ Markdown file generated: `note_0001_my_meeting.md`
5. ✅ Unmatched speech written to `slush_bucket.txt`
6. ✅ Generic transcript still written to `transcript_raw.txt`
7. ✅ TUI shows bucket progress and status
8. ✅ Config loaded from YAML file
9. ✅ No breaking changes to existing tests during phase 1

---

## Non-Functional Requirements

1. **Performance:** Chunking should not add >100ms latency to transcription
2. **Thread Safety:** All VAD parameter changes must be queued (never mid-segment)
3. **Error Handling:** Invalid config should fail at startup, not runtime
4. **Backward Compat:** Legacy `vad_recorder.py` wrapper continues to work
5. **Documentation:** Update CLAUDE.md with new CommandDoc system

---

## Future Enhancements (Out of Scope)

1. **Dynamic Plugin Registry:** Load CommandDocs from `~/.palaver/commands/`
2. **Multi-Language:** Support non-English command phrases
3. **Voice Commands:** "cancel note", "redo title", "append to last note"
4. **LLM Integration:** "Fix grammar in last note", "Summarize slush bucket"
5. **Cloud Sync:** Push notes to Dropbox/Google Drive
6. **Mobile Companion:** Trigger recording from phone

---

## Appendix: Rapidfuzz Examples

### Basic Usage
```python
from rapidfuzz import fuzz

# Simple ratio (0-100)
score = fuzz.ratio("clerk", "clear")  # 75.0
score = fuzz.ratio("clerk", "click")  # 66.67
score = fuzz.ratio("start new note", "start a new note")  # 93.33

# Partial ratio (substring matching)
score = fuzz.partial_ratio("start new note", "um start new note please")  # 100.0

# Token sort (word order independent)
score = fuzz.token_sort_ratio("new note start", "start new note")  # 100.0
```

### Recommended for Palaver
```python
def fuzzy_match(text: str, target: str, threshold: float = 80.0) -> bool:
    """
    Fuzzy match with sensible defaults.

    Uses token_sort_ratio to handle word order variations.
    """
    score = fuzz.token_sort_ratio(text.lower(), target.lower())
    return score >= threshold

# Examples
fuzzy_match("clerk, start new note", "start new note", 80)  # True
fuzzy_match("start a new note please", "start new note", 80)  # True
fuzzy_match("begin recording", "start new note", 80)  # False
```

---

## Appendix: File Structure After Implementation

```
palaver/
├── src/palaver/
│   ├── config/
│   │   ├── __init__.py
│   │   └── recorder_config.py       # Config loading (YAML/defaults)
│   │
│   ├── commands/
│   │   ├── __init__.py
│   │   ├── speech_bucket.py         # SpeechBucket class
│   │   ├── command_doc.py           # CommandDoc ABC
│   │   └── simple_note.py           # SimpleNote implementation
│   │
│   ├── recorder/
│   │   ├── async_vad_recorder.py    # Enhanced with chunking + VAD params
│   │   ├── text_processor.py        # Refactored with CommandDoc system
│   │   ├── transcription.py         # (unchanged)
│   │   ├── session.py               # (unchanged)
│   │   └── ...
│   │
│   └── tui/
│       └── recorder_tui.py          # Enhanced with new events
│
├── config/
│   └── recorder_config.yaml         # Default configuration
│
├── tests/
│   ├── test_speech_bucket.py
│   ├── test_command_doc.py
│   ├── test_simple_note.py
│   ├── test_text_processor_state_machine.py
│   ├── test_fuzzy_matching.py
│   └── ...
│
└── design_docs/
    └── text_processor_commanddoc_enhancement_plan.md  # This document
```

---

## Summary

This enhancement transforms the recorder from a hardcoded "note" system into a flexible CommandDoc framework with:

1. **Generalization:** Any document type (notes, emails, todos, etc.)
2. **Configurability:** YAML config for all parameters
3. **Real-time Feedback:** Chunking provides text every 2-3 seconds
4. **Extensibility:** Event system supports future UI/integration
5. **Slush Bucket:** Captures unmatched speech for future workflows
6. **Thread Safety:** Maintains proven VAD parameter queuing pattern
7. **Fuzzy Matching:** Handles speech recognition imperfections

**Estimated Effort:** 15-20 days (6 phases)

**Risk Level:** Medium (significant refactor, but well-scoped with clear tests)

**User Impact:** High (dramatically better UX for long notes, foundation for future features)
