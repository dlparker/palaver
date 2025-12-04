# VAD Recorder Async Refactoring Plan

## Overview

Refactor `vad_recorder.py` from threading-based to async/await architecture to enable non-blocking UI integration while preserving all existing functionality.

## Key Decisions (from User)

1. **Async only**: Convert entire codebase to async, CLI wraps with `asyncio.run()`
2. **Queue-based**: Audio callback (sync) → `asyncio.Queue` → async processors
3. **Start fresh**: Discard `recorder_backend_async.py`, build new design from working `vad_recorder.py`
4. **Preserve tests**: `tests_fast/test_simulated_recorder.py` must work without modification

## Architecture: Sync Audio Thread → Async Processing

```
┌─────────────────────────────────────────────────────────────┐
│ Audio Thread (sync, must be fast)                          │
│  • sounddevice callback                                     │
│  • VAD processing (fast numpy ops)                          │
│  • Segment detection                                        │
│  • Push events to Queue ──────────────────────┐             │
└────────────────────────────────────────────────┼─────────────┘
                                                 │
                                        asyncio.Queue (thread-safe)
                                                 │
┌────────────────────────────────────────────────┼─────────────┐
│ Async Event Loop (main thread)                │             │
│  • Event processor (async coroutine) ◄─────────┘             │
│  • Save WAV files (via executor)                             │
│  • Transcription orchestration                               │
│  • Text processing & command detection                       │
│  • Session management                                        │
└──────────────────────────────────────────────────────────────┘
```

### Key Insight: Audio Callback Stays Sync

The audio callback **must remain synchronous** - it runs in sounddevice's audio thread and needs to be fast. However, we can push events/data into an `asyncio.Queue` from the callback, then process them asynchronously.

**Pattern**:
```python
def audio_callback(indata, frames, time_info, status):
    # Fast sync operations
    chunk = indata[:, 0].copy()
    vad_result = process_vad(chunk)

    # Push to async queue (thread-safe)
    asyncio.run_coroutine_threadsafe(
        queue.put(event),
        loop
    ).result(timeout=0.001)  # Or use Queue directly with loop.call_soon_threadsafe
```

## Component Breakdown

### 1. Core Async Recorder Class

**File**: `src/palaver/recorder/async_vad_recorder.py` (new)

```python
class AsyncVADRecorder:
    """Async VAD-based voice recorder"""

    async def start_recording(
        self,
        input_source: Optional[AudioSource] = None,
        session: Optional[Session] = None
    ) -> None:
        """Start recording session (async)"""

    async def stop_recording(self) -> Path:
        """Stop recording and return session directory"""

    async def process_events(self) -> None:
        """Main event processing loop (async coroutine)"""
```

**Responsibilities**:
- Manages recording session lifecycle
- Coordinates audio input, VAD, transcription, text processing
- Processes events from audio callback asynchronously
- Handles cleanup and finalization

### 2. Audio Event Queue

**Events pushed from audio callback**:

```python
@dataclass
class AudioEvent:
    """Base class for audio callback events"""
    timestamp: float

@dataclass
class AudioChunk(AudioEvent):
    """Raw audio data chunk"""
    data: np.ndarray
    in_speech: bool

@dataclass
class SpeechStarted(AudioEvent):
    """Speech segment started"""
    segment_index: int

@dataclass
class SpeechEnded(AudioEvent):
    """Speech segment ended"""
    segment_index: int
    audio_data: np.ndarray
    duration_sec: float
```

Audio callback pushes events → Async coroutine processes them

### 3. Async Transcription Orchestrator

**Current**: `transcription.py` with `WhisperTranscriber` (multiprocess, threading)

**Keep**: The multiprocess architecture (whisper is CPU-bound)

**Change**: Make the coordinator async

```python
class AsyncWhisperTranscriber:
    """Async wrapper for multiprocess transcription"""

    async def start(self) -> None:
        """Start worker processes and result collector task"""

    async def queue_job(self, job: TranscriptionJob) -> None:
        """Queue transcription job (async, non-blocking)"""

    async def get_result(self) -> TranscriptionResult:
        """Get next result (async iterator)"""

    async def stop(self) -> None:
        """Shutdown workers and cleanup"""
```

**Implementation**:
- Worker processes remain the same (run subprocess for whisper-cli)
- Use `asyncio.Queue` for results instead of `queue.Queue`
- Result collector is async task instead of thread
- Job submission is async (uses executor for queue.put if needed)

### 4. Async Text Processor

**Current**: `TextProcessor` (thread-based, watches queue)

**Change**: Make it fully async

```python
class AsyncTextProcessor:
    """Async text processor with command detection"""

    async def process_result(self, result: TranscriptionResult) -> None:
        """Process single transcription result"""

    async def finalize(self, total_segments: int) -> None:
        """Write final transcript files"""
```

**Key change**: Instead of thread polling a queue, it becomes a passive processor called by the main event loop.

### 5. Audio Sources (Already Abstracted)

**Current**: `audio_sources.py` has `DeviceAudioSource`, `FileAudioSource`, `SimulatedAudioSource`

**Keep**: These are perfect as-is! They abstract the input source.

**Change**: Make simulated source async-aware

```python
class SimulatedAudioSource:
    """Async simulated audio source for testing"""

    async def start(self, callback: Callable) -> None:
        """Start generating simulated events"""
        # Instead of calling callback directly, schedule it
        # Or push pre-made events to queue
```

Actually, simpler approach: For simulated mode, bypass audio entirely and inject events directly into the event queue.

### 6. Session Management (Already Modular)

**Current**: `session.py` - perfect as-is!

**Minor change**: Make file writes async using `aiofiles` or executor

```python
class Session:
    async def write_manifest(self, ...) -> None:
        """Write manifest (async)"""
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._write_manifest_sync, ...)
```

## Detailed Async Flow

### Recording Flow (Microphone Mode)

```python
async def start_recording(self):
    # 1. Setup
    self.session = Session()
    self.session.create()

    # 2. Create event queue
    self.event_queue = asyncio.Queue()

    # 3. Start transcriber
    self.transcriber = AsyncWhisperTranscriber(...)
    await self.transcriber.start()

    # 4. Start text processor
    self.text_processor = AsyncTextProcessor(...)

    # 5. Setup audio callback (sync function)
    def audio_callback(indata, frames, time_info, status):
        event = self._process_audio_chunk(indata)  # Sync VAD
        if event:
            # Thread-safe queue push
            asyncio.run_coroutine_threadsafe(
                self.event_queue.put(event),
                self.loop
            )

    # 6. Start audio stream
    self.audio_source.start(audio_callback)

    # 7. Start event processor task
    self.event_task = asyncio.create_task(self._process_events())

    # 8. Start transcription result processor task
    self.result_task = asyncio.create_task(self._process_transcriptions())

async def _process_events(self):
    """Main event processing loop"""
    while self.is_recording:
        event = await self.event_queue.get()

        if isinstance(event, SpeechEnded):
            # Save WAV (async, via executor)
            await self._save_segment(event)
            # Queue transcription
            await self.transcriber.queue_job(job)

        elif isinstance(event, ModeChangeRequested):
            self.vad_mode_requested = event.mode
        # ... handle other events

async def _process_transcriptions(self):
    """Process transcription results"""
    async for result in self.transcriber.results():
        # Process text
        await self.text_processor.process_result(result)

        # Check for commands
        if command_detected:
            # Queue mode change
            await self.event_queue.put(ModeChangeRequested("long_note"))

async def stop_recording(self):
    # 1. Stop audio stream
    self.is_recording = False
    await asyncio.sleep(0.5)  # Let queue drain

    # 2. Stop transcriber
    await self.transcriber.stop()

    # 3. Finalize text processor
    await self.text_processor.finalize(total_segments)

    # 4. Wait for tasks
    await self.event_task
    await self.result_task

    # 5. Write manifest
    await self.session.write_manifest(...)

    return self.session.get_path()
```

## Simulated Mode (for Fast Tests)

Simulated mode bypasses audio entirely and injects pre-defined events:

```python
async def run_simulated(simulated_segments: List[Tuple[str, float]]) -> Path:
    """Fast simulated mode for testing"""

    # 1. Create session
    session = Session()
    session.create()

    # 2. Create simulated transcriber (instant results)
    transcriber = SimulatedTranscriber(transcripts={...})
    await transcriber.start()

    # 3. Create text processor
    text_processor = AsyncTextProcessor(...)

    # 4. Inject simulated events directly
    for i, (text, duration) in enumerate(simulated_segments):
        # Queue job
        job = TranscriptionJob(segment_index=i, ...)
        await transcriber.queue_job(job)

        # Process result (instant)
        result = await transcriber.get_result()
        await text_processor.process_result(result)

    # 5. Finalize
    await text_processor.finalize(len(simulated_segments))
    await session.write_manifest(...)

    return session.get_path()
```

## CLI Wrapper (Non-Async Entry Point)

**File**: `scripts/direct_recorder.py`

```python
#!/usr/bin/env python3
"""CLI wrapper for async recorder"""

import sys
import asyncio
import argparse
from palaver.recorder.async_vad_recorder import AsyncVADRecorder
from palaver.recorder.audio_sources import create_audio_source

async def run_interactive(input_source: Optional[str] = None):
    """Run interactive recording session (async)"""
    recorder = AsyncVADRecorder()

    # Setup
    audio_source = create_audio_source(input_spec=input_source, ...)

    # User interaction (async input)
    print("Press Enter to start...")
    await asyncio.get_event_loop().run_in_executor(None, input)

    # Start recording
    await recorder.start_recording(input_source=audio_source)

    # Wait for stop (async input)
    if is_microphone_mode:
        print("Recording... press Enter to stop")
        await asyncio.get_event_loop().run_in_executor(None, input)
    else:
        # File mode - wait for completion
        await recorder.wait_for_completion()

    # Stop and finalize
    session_dir = await recorder.stop_recording()
    return session_dir

def main():
    parser = argparse.ArgumentParser(...)
    args = parser.parse_args()

    try:
        # Run async code from sync CLI
        session_dir = asyncio.run(run_interactive(input_source=args.input))
        print(f"\nSession complete: {session_dir}")
        return 0
    except KeyboardInterrupt:
        print("\n\nInterrupted by user")
        return 0
    except Exception as e:
        print(f"\n\nError: {e}")
        return 1

if __name__ == "__main__":
    sys.exit(main())
```

## Test Compatibility

**File**: `vad_recorder.py` (refactored)

Keep a simple wrapper for test compatibility:

```python
# vad_recorder.py

from palaver.recorder.async_vad_recorder import AsyncVADRecorder, run_simulated

def main(
    input_source: Optional[str] = None,
    mode: str = "auto",
    simulated_segments: Optional[list] = None
) -> Path:
    """
    Test-friendly wrapper (sync function for backward compatibility).

    This function is kept for test compatibility only.
    For new code, use AsyncVADRecorder directly with async/await.
    """
    if mode == "simulated":
        # Simulated mode - run async code synchronously
        return asyncio.run(run_simulated(simulated_segments))
    else:
        # Real mode
        async def _run():
            recorder = AsyncVADRecorder()
            audio_source = create_audio_source(input_spec=input_source, ...)
            await recorder.start_recording(input_source=audio_source)

            # File mode - wait for completion
            if is_file_mode:
                await recorder.wait_for_completion()
            # Microphone mode would need user interaction

            return await recorder.stop_recording()

        return asyncio.run(_run())
```

**This keeps tests working without changes**:
```python
# tests_fast/test_simulated_recorder.py (unchanged)
session_dir = main(mode="simulated", simulated_segments=segments)
```

## Implementation Steps

### Phase 1: Create Async Foundation (2-3 hours)

**Step 1.1**: Create `async_vad_recorder.py` skeleton
- [ ] Define `AsyncVADRecorder` class
- [ ] Define event types (`AudioEvent`, `SpeechStarted`, `SpeechEnded`, etc.)
- [ ] Implement `start_recording()` and `stop_recording()` stubs
- [ ] Implement event queue and `_process_events()` loop

**Step 1.2**: Port VAD logic
- [ ] Copy `audio_callback()` from `vad_recorder.py`
- [ ] Keep it synchronous (required for audio thread)
- [ ] Make it push events to `asyncio.Queue` via `run_coroutine_threadsafe()`
- [ ] Copy VAD mode switching logic (still queue-based)

**Step 1.3**: Implement async segment handling
- [ ] `_save_segment()` - saves WAV via executor
- [ ] Event creation for speech start/end
- [ ] Segment validation (MIN_SEG_SEC check)

### Phase 2: Async Transcription (1-2 hours)

**Step 2.1**: Create `AsyncWhisperTranscriber`
- [ ] Keep multiprocess worker pool (same as current)
- [ ] Make coordinator async
- [ ] Use `asyncio.Queue` for results (wrap multiprocessing.Queue)
- [ ] Result collector as async task instead of thread

**Step 2.2**: Integrate with recorder
- [ ] Start transcriber in `start_recording()`
- [ ] Queue jobs from event processor
- [ ] Process results in separate async task

### Phase 3: Async Text Processing (1 hour)

**Step 3.1**: Create `AsyncTextProcessor`
- [ ] Convert from thread to async processor
- [ ] Keep command detection logic (action_phrases)
- [ ] Make file writes async (via executor or aiofiles)

**Step 3.2**: Integrate with recorder
- [ ] Call from transcription result processor
- [ ] Wire up mode change callback

### Phase 4: Simulated Mode (1 hour)

**Step 4.1**: Implement `run_simulated()` function
- [ ] Create session
- [ ] Use `SimulatedTranscriber` (may need async version)
- [ ] Process segments without audio
- [ ] Write outputs

**Step 4.2**: Verify with tests
- [ ] Run `uv run pytest tests_fast/ -v`
- [ ] Ensure all tests pass
- [ ] Verify output capture works

### Phase 5: CLI Wrapper (30 minutes)

**Step 5.1**: Create `scripts/direct_recorder.py`
- [ ] Implement argparse
- [ ] Wrap async recorder with `asyncio.run()`
- [ ] Handle user interaction (async input)
- [ ] Error handling

**Step 5.2**: Update shell script
- [ ] Update `run_vad_recorder.sh` to call new script

### Phase 6: Test Compatibility Layer (30 minutes)

**Step 6.1**: Update `vad_recorder.py`
- [ ] Keep `main()` function as compatibility wrapper
- [ ] Import from `async_vad_recorder`
- [ ] Wrap async calls with `asyncio.run()`
- [ ] Remove old implementation (keep only wrapper)

**Step 6.2**: Verify all tests
- [ ] Run fast tests: `uv run pytest tests_fast/ -v`
- [ ] Run integration tests: `uv run pytest tests/ -v` (when ready)

### Phase 7: Documentation (30 minutes)

- [ ] Update `CLAUDE.md` with async architecture
- [ ] Document new async API
- [ ] Update "Running the Recorder" section
- [ ] Add async examples

## Key Implementation Details

### Thread-Safe Queue Access

**Problem**: Audio callback runs in separate thread, need to push to asyncio.Queue

**Solution**:
```python
class AsyncVADRecorder:
    def __init__(self):
        self.loop = None
        self.event_queue = None

    async def start_recording(self):
        self.loop = asyncio.get_running_loop()
        self.event_queue = asyncio.Queue()

        def audio_callback(indata, frames, time_info, status):
            event = self._process_chunk(indata)  # Sync
            if event:
                # Thread-safe push
                asyncio.run_coroutine_threadsafe(
                    self.event_queue.put(event),
                    self.loop
                )

        # Start audio stream with callback
        self.stream.start(audio_callback)
```

### Executor for Blocking I/O

File writes should use executor to avoid blocking event loop:

```python
async def _save_segment(self, wav_path: Path, audio: np.ndarray):
    """Save WAV file (async)"""
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(
        None,
        self._save_wav_sync,
        wav_path,
        audio
    )

def _save_wav_sync(self, wav_path: Path, audio: np.ndarray):
    """Sync implementation of WAV saving"""
    # ... wave.open() and write ...
```

### Mode Change Still Queue-Based

The VAD mode change logic **stays the same**:
- Changes are **requested** (queued)
- Applied at segment boundaries only
- Applied in audio callback before starting new segment

This is critical for thread safety. The audio callback applies the change synchronously, no async needed here.

### Async Input for CLI

For interactive prompts in CLI:

```python
async def async_input(prompt: str) -> str:
    """Non-blocking input (runs in executor)"""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, input, prompt)

# Usage
await async_input("Press Enter to start...")
```

## File Structure After Refactoring

```
src/palaver/recorder/
├── async_vad_recorder.py      # New: Async recorder (main implementation)
├── vad_recorder.py             # Modified: Thin wrapper for test compatibility
├── transcription.py            # Modified: Add AsyncWhisperTranscriber
├── text_processor.py           # Modified: Add AsyncTextProcessor
├── session.py                  # Minor: Make writes async
├── audio_sources.py            # Unchanged: Already abstracted well
└── action_phrases.py           # Unchanged: Pure logic, no I/O

scripts/
└── direct_recorder.py          # New: CLI entry point

tests_fast/
└── test_simulated_recorder.py  # Unchanged: Uses main() wrapper

tests/
└── test_vad_recorder_file.py   # May need updates (separate task)
```

## Benefits

1. **Non-blocking**: Can integrate with TUI, web UI, etc.
2. **Clean architecture**: Clear async/await flow
3. **Test compatible**: Existing tests work without changes
4. **Reusable**: Core async recorder can be used anywhere
5. **Maintainable**: Simpler than thread-based coordination

## Risks and Mitigations

### Risk 1: Audio callback thread safety
**Mitigation**: Use `asyncio.run_coroutine_threadsafe()` with stored event loop reference. This is the standard pattern.

### Risk 2: Queue blocking
**Mitigation**: Use unbounded `asyncio.Queue()` or handle full queue gracefully in callback (drop events if necessary).

### Risk 3: Simulated mode breaks tests
**Mitigation**: Keep `main()` wrapper in `vad_recorder.py` that calls async code with `asyncio.run()`. Tests see no difference.

### Risk 4: Integration tests need rewrite
**Mitigation**: Phase 6 focused on test compatibility. Integration tests may need separate task (user said to use fast tests first).

### Risk 5: Performance overhead
**Mitigation**: Async overhead is minimal. Critical path (audio callback) stays synchronous. Event processing is async but not latency-sensitive.

## Testing Strategy

### Phase 4: Fast tests (simulated mode)
```bash
uv run pytest tests_fast/ -v
```
Must pass without test modifications.

### Phase 6: Integration tests (when ready)
```bash
uv run pytest tests/test_vad_recorder_file.py -v
```
User will indicate when to run these.

### Manual CLI testing
```bash
./run_vad_recorder.sh
./run_vad_recorder.sh --input tests/audio_samples/note1.wav
```

## Success Criteria

- [ ] All fast tests pass (`tests_fast/`)
- [ ] CLI works for both microphone and file input
- [ ] Simulated mode remains fast (<2 seconds for 20 segments)
- [ ] No breaking changes to test API
- [ ] Code is cleaner and more maintainable than threading version
- [ ] Ready for TUI integration (non-blocking async API)

## Timeline Estimate

- Phase 1: 2-3 hours (foundation)
- Phase 2: 1-2 hours (transcription)
- Phase 3: 1 hour (text processing)
- Phase 4: 1 hour (simulated mode + testing)
- Phase 5: 30 minutes (CLI)
- Phase 6: 30 minutes (compatibility)
- Phase 7: 30 minutes (docs)

**Total: 6-8 hours of focused work**

Can be done incrementally, testing at each phase.

## Next Steps

1. User approval of plan
2. Start Phase 1 (async foundation)
3. Iterative implementation with testing after each phase
4. Integration with TUI (future task after refactoring complete)
