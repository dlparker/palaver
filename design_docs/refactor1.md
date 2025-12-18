# Refactoring Phase 1: Simplify Pipeline Initialization

## Goals

1. Eliminate confusion between audio listeners and API listeners by prepending "Audio" to audio listener names
2. Move listener instantiation from server wrappers to script-level code
3. Consolidate pipeline configuration differences into `PipelineConfig`
4. Enable simplified programming model: Create listener → Create API listener → Pass both to ScribePipeline
5. Remove obsolete `MicServer` and `PlaybackServer` wrapper classes

## Current State Problems

### Naming Confusion
- `Listener` protocol is too generic - conflicts conceptually with `ScribeAPIListener`
- `ListenerCCSMixin` name doesn't clearly indicate it's for audio sources

### Server Wrapper Redundancy
- `MicServer` and `PlaybackServer` are thin wrappers that mostly differ in configuration
- Listener creation happens inside servers rather than at call site
- Background error handling uses outdated pattern (pre-TopErrorHandler)

### Configuration Scatter
- VAD configuration happens in `PlaybackServer.run()` after pipeline setup
- Whisper buffer configuration also happens post-setup in PlaybackServer
- Configuration differences not visible in `PipelineConfig`

## Refactoring Steps

### Step 1: Rename Audio Listener Protocols and Base Classes

**Objective:** Eliminate naming confusion by clearly distinguishing audio sources from API listeners.

#### Changes to `src/palaver/scribe/listen_api.py`

**Rename classes/protocols:**
- `Listener` → `AudioListener`
- `ListenerCCSMixin` → `AudioListenerCCSMixin`

**Keep unchanged:**
- `create_source_id()` function (not a listener-specific name)

**Rename file:**
- `src/palaver/scribe/listen_api.py` → `src/palaver/scribe/audio_listeners.py`

#### Update imports in dependent files:

**`src/palaver/scribe/core.py`:**
```python
# Before:
from palaver.scribe.listen_api import Listener

# After:
from palaver.scribe.audio_listeners import AudioListener
```

**`src/palaver/scribe/listener/mic_listener.py`:**
```python
# Before:
from palaver.scribe.listen_api import Listener, ListenerCCSMixin, create_source_id

# After:
from palaver.scribe.audio_listeners import AudioListener, AudioListenerCCSMixin, create_source_id

# Class declaration:
# Before:
class MicListener(ListenerCCSMixin, Listener):

# After:
class MicListener(AudioListenerCCSMixin, AudioListener):
```

**`src/palaver/scribe/listener/file_listener.py`:**
```python
# Before:
from palaver.scribe.listen_api import Listener, ListenerCCSMixin, create_source_id

# After:
from palaver.scribe.audio_listeners import AudioListener, AudioListenerCCSMixin, create_source_id

# Class declaration:
# Before:
class FileListener(ListenerCCSMixin, Listener):

# After:
class FileListener(AudioListenerCCSMixin, AudioListener):
```

#### Files Modified:
- `src/palaver/scribe/listen_api.py` → `src/palaver/scribe/audio_listeners.py` (rename + internal changes)
- `src/palaver/scribe/core.py`
- `src/palaver/scribe/listener/mic_listener.py`
- `src/palaver/scribe/listener/file_listener.py`

---

### Step 2: Move Listener Creation to Scripts

**Objective:** Eliminate server wrapper classes by moving listener instantiation to script main functions.

#### Remove Background Error Handling

Per user guidance, the `set_background_error()` methods in servers are obsolete. Both `MicListener` and `FileListener` already use `get_error_handler().wrap_task()` for their `_reader()` methods, which automatically delivers errors to `TopErrorHandler`.

**Remove from MicListener (`src/palaver/scribe/listener/mic_listener.py:57-58`):**
```python
def set_background_error(self, error_dict):
    self._background_error = error_dict
```

Also remove `self._background_error = None` from `__init__` (line 41).

#### Changes to `scripts/run_mic.py`

**Before (current structure):**
```python
api_wrapper = APIWrapper()
mic_server = MicServer(
    model_path=args.model,
    api_listener=api_wrapper,
    use_multiprocessing=True,
)
api_wrapper.set_server(mic_server, "Microphone Listening")

async def main_task():
    if args.output_dir:
        await api_wrapper.add_recorder(args)
    await api_wrapper.server.run()
```

**After (new structure):**
```python
api_wrapper = APIWrapper()

async def main_task():
    if args.output_dir:
        await api_wrapper.add_recorder(args)

    # Create listener directly
    mic_listener = MicListener(chunk_duration=0.03)

    # Create pipeline config
    config = PipelineConfig(
        model_path=args.model,
        api_listener=api_wrapper,
        target_samplerate=16000,
        target_channels=1,
        use_multiprocessing=True,
    )

    # Manage context and lifecycle
    async with mic_listener:
        async with ScribePipeline(mic_listener, config) as pipeline:
            await pipeline.start_listener()
            try:
                await pipeline.run_until_error_or_interrupt()
            except (KeyboardInterrupt, asyncio.CancelledError):
                print("\nControl-C detected. Shutting down...")
```

**Imports to add:**
```python
from palaver.scribe.listener.mic_listener import MicListener
from palaver.scribe.core import ScribePipeline, PipelineConfig
```

**Remove:**
- `from palaver.scribe.mic_server import MicServer`
- `api_wrapper.set_server()` calls
- `api_wrapper.server` references

**Remove obsolete class members from APIWrapper:**
- `self.server`
- `self.server_type`
- `set_server()` method

#### Changes to `scripts/playback.py`

**Before (current structure):**
```python
api_wrapper = APIWrapper(play_sound=args.play_sound)
playback_server = PlaybackServer(
    model_path=args.model,
    audio_file=args.file,
    api_listener=api_wrapper,
    simulate_timing=sim_timing,
    use_multiprocessing=True,
)
api_wrapper.set_server(playback_server, "File playback")
await api_wrapper.server.run()
```

**After (new structure):**
```python
api_wrapper = APIWrapper(play_sound=args.play_sound)

sim_timing = False
if args.output_dir:
    if sim_timing:
        chunk_ring_seconds = 3
    else:
        chunk_ring_seconds = 12
    block_recorder = BlockAudioRecorder(args.output_dir, chunk_ring_seconds)
    await api_wrapper.add_recorder(block_recorder)

# Create listener directly
file_listener = FileListener(
    audio_file=args.file,
    chunk_duration=0.03,
    simulate_timing=sim_timing,
)

# Create pipeline config with playback-specific settings
config = PipelineConfig(
    model_path=args.model,
    api_listener=api_wrapper,
    target_samplerate=16000,
    target_channels=1,
    use_multiprocessing=True,
    require_command_alerts=False,
    vad_silence_ms=3000,
    vad_speech_pad_ms=1000,
    seconds_per_scan=2,
)

# Manage context and lifecycle
async with file_listener:
    async with ScribePipeline(file_listener, config) as pipeline:
        await pipeline.start_listener()
        await pipeline.run_until_error_or_interrupt()
```

**Imports to add:**
```python
from palaver.scribe.listener.file_listener import FileListener
from palaver.scribe.core import ScribePipeline, PipelineConfig
```

**Remove:**
- `from palaver.scribe.playback_server import PlaybackServer`
- `api_wrapper.set_server()` calls

#### Changes to `scripts/rescan.py`

**Before (current structure):**
```python
playback_server = PlaybackServer(
    model_path=args.model,
    audio_file=last_block_files.sound_path,
    api_listener=api_wrapper,
    require_alerts=False,
    seconds_per_scan=seconds_per_scan,
    simulate_timing=False,
    use_multiprocessing=True,
)
api_wrapper.set_server(playback_server, "File playback")
await api_wrapper.server.run()
```

**After (new structure):**
```python
# Determine seconds_per_scan based on model size
if str(args.model.resolve()) in long_models:
    seconds_per_scan = 10
else:
    seconds_per_scan = 2

# Create listener directly
file_listener = FileListener(
    audio_file=last_block_files.sound_path,
    chunk_duration=0.03,
    simulate_timing=False,
)

# Create pipeline config with rescan-specific settings
config = PipelineConfig(
    model_path=args.model,
    api_listener=api_wrapper,
    target_samplerate=16000,
    target_channels=1,
    use_multiprocessing=True,
    require_command_alerts=False,
    vad_silence_ms=3000,
    vad_speech_pad_ms=1000,
    seconds_per_scan=seconds_per_scan,
)

# Manage context and lifecycle
async with file_listener:
    async with ScribePipeline(file_listener, config) as pipeline:
        await pipeline.start_listener()
        await pipeline.run_until_error_or_interrupt()
```

**Imports to add:**
```python
from palaver.scribe.listener.file_listener import FileListener
from palaver.scribe.core import ScribePipeline, PipelineConfig
```

**Remove:**
- `from palaver.scribe.playback_server import PlaybackServer`
- `api_wrapper.set_server()` calls

#### Files to Delete:
- `src/palaver/scribe/mic_server.py`
- `src/palaver/scribe/playback_server.py`

#### Files Modified:
- `scripts/run_mic.py`
- `scripts/playback.py`
- `scripts/rescan.py`
- `src/palaver/scribe/listener/mic_listener.py` (remove `set_background_error()`)

---

### Step 3: Consolidate Configuration in PipelineConfig

**Objective:** Move all pipeline configuration differences into `PipelineConfig` and have `ScribePipeline.setup_pipeline()` apply them.

#### Expand PipelineConfig (`src/palaver/scribe/core.py`)

**Add new configuration fields:**

```python
@dataclass
class PipelineConfig:
    """Configuration for the Scribe pipeline."""
    model_path: Path
    api_listener: ScribeAPIListener
    require_command_alerts: bool = True
    target_samplerate: int = 16000
    target_channels: int = 1
    use_multiprocessing: bool = False
    whisper_shutdown_timeout: float = 10.0

    # VAD configuration
    vad_silence_ms: int = 2000           # Default from VADFilter
    vad_speech_pad_ms: int = 1500        # Default from VADFilter
    vad_threshold: float = 0.5           # Default from VADFilter

    # Whisper buffer configuration
    whisper_buffer_samples: Optional[int] = None
    seconds_per_scan: Optional[float] = None  # Alternative to buffer_samples
```

**Configuration semantics:**
- If `whisper_buffer_samples` is set, use it directly
- If `seconds_per_scan` is set, calculate buffer as `int(target_samplerate * seconds_per_scan)`
- If both are None, WhisperThread uses its default buffering behavior
- `seconds_per_scan` provides a more intuitive interface (2 seconds, 10 seconds, etc.)

#### Update ScribePipeline.setup_pipeline()

**Current code (line 85-136):**
```python
async def setup_pipeline(self):
    if self._pipeline_setup_complete:
        return

    # Create downsampler
    self.downsampler = DownSampler(
        target_samplerate=self.config.target_samplerate,
        target_channels=self.config.target_channels
    )
    self.listener.add_event_listener(self.downsampler)

    self.vadfilter = VADFilter(self.listener)
    self.downsampler.add_event_listener(self.vadfilter)

    # ... rest of setup ...

    self._pipeline_setup_complete = True
```

**Add VAD and Whisper configuration (after VADFilter creation, before marking complete):**

```python
async def setup_pipeline(self):
    if self._pipeline_setup_complete:
        return

    # ... existing setup code for downsampler, vadfilter, whisper_thread, etc. ...

    # Apply VAD configuration from PipelineConfig
    self.vadfilter.reset(
        silence_ms=self.config.vad_silence_ms,
        speech_pad_ms=self.config.vad_speech_pad_ms,
        threshold=self.config.vad_threshold
    )

    # Apply Whisper buffer configuration from PipelineConfig
    if self.config.whisper_buffer_samples is not None:
        await self.whisper_thread.set_buffer_samples(self.config.whisper_buffer_samples)
    elif self.config.seconds_per_scan is not None:
        samples = int(self.config.target_samplerate * self.config.seconds_per_scan)
        await self.whisper_thread.set_buffer_samples(samples)

    self._pipeline_setup_complete = True
    # ... rest of method ...
```

**Location:** Insert this configuration block after all pipeline components are created (line ~122, after `self.whisper_thread.add_text_event_listener(self.command_dispatch)`) but before `self._pipeline_setup_complete = True`.

#### Update ScribePipeline Type Hint

**In `src/palaver/scribe/core.py`, line 39:**

```python
# Before:
def __init__(self, listener: Listener, config: PipelineConfig):

# After:
def __init__(self, listener: AudioListener, config: PipelineConfig):
```

**Add import:**
```python
from palaver.scribe.audio_listeners import AudioListener
```

#### Files Modified:
- `src/palaver/scribe/core.py`

---

## Implementation Order

1. **Step 1: Rename protocols** - Pure refactoring, no logic changes
2. **Step 2: Move listener creation** - Requires Step 1 complete, removes server files
3. **Step 3: Consolidate configuration** - Requires Step 2 complete, must update scripts to use new config fields

## Testing Strategy

**IMPORTANT:** The existing tests in `tests/test_scribe_basic.py` are skeleton only and don't validate actual functionality. Automated testing will be implemented after this refactoring phase and the next phase are complete.

**Manual verification required after each step:**

After completing each step, implementation must **STOP** and request user verification before proceeding to the next step.

### Step 1 Verification (Rename protocols)
- All files must compile without import errors
- No runtime testing needed (pure refactoring)

### Step 2 Verification (Move listener creation)
User will manually test each script:
```bash
uv run scripts/run_mic.py --model models/ggml-base.en.bin
uv run scripts/playback.py --model models/ggml-base.en.bin <audio-file>
uv run scripts/rescan.py <block-directory>
```

Verify:
- Scripts start without errors
- Audio processing and transcription work correctly
- Command detection functions (if tested with BlockRecorder)
- No crashes or exceptions

### Step 3 Verification (Consolidate configuration)
User will manually test with focus on configuration differences:

**Mic mode:**
- VAD silence threshold is 2000ms (default)
- Whisper buffering uses defaults

**Playback mode:**
- VAD silence threshold is 3000ms
- Whisper uses 2-second scan windows

**Rescan mode:**
- VAD silence threshold is 3000ms
- Whisper uses 10-second scans for large models, 2-second for small models

This is a laborious manual process but necessary until proper functional tests are implemented.

## Future Refactoring Opportunities (Not in This Phase)

### Identified During Analysis:

1. **APIWrapper Duplication:**
   - `run_mic.py`, `playback.py`, and `rescan.py` all have nearly identical `APIWrapper` implementations
   - Could extract to shared module: `src/palaver/scribe/api_wrapper.py`
   - Differences: `play_sound` parameter only used in playback/rescan

2. **BlockRecorder Wiring Pattern:**
   - All three scripts follow same pattern: create recorder, pass to `APIWrapper.add_recorder()`, wire in `on_pipeline_ready()`
   - Could simplify by having ScribePipeline accept optional `block_recorder` in config

3. **Error Handler Boilerplate:**
   - All three scripts have identical `MyTLC` class and `TopErrorHandler` setup
   - Could extract to helper function: `run_with_error_handler(async_main, logger)`

4. **Model Path Validation:**
   - Duplicated `if not args.model.exists()` check in all three scripts
   - Could move to shared `create_parser()` function with `type=existing_path_type`

5. **Context Manager Nesting Pattern:**
   - `async with listener: async with pipeline:` repeated in all scripts
   - Could create helper context manager that nests both

6. **Pipeline Accessor After Errors:**
   - Pattern of `api_wrapper.server.get_pipeline()` no longer works after removing servers
   - Scripts may need to store pipeline reference locally

7. **Duplicate TextEvent Handling:**
   - `playback.py:106` has bug: `if event.event_id == self.text_events:` (should be `in`)
   - `run_mic.py:100-117` has duplicate `handle_text_event()` method (lines 100-117 and 118-137)
   - Should be consolidated in shared APIWrapper

8. **VADFilter Defaults:**
   - Constants at top of `vad_filter.py` (MIN_SILENCE_MS, SPEECH_PAD_MS, VAD_THRESHOLD)
   - Could use PipelineConfig defaults instead of module-level constants

9. **TopErrorHandler Integration in MicListener:**
   - `MicListener._reader()` has complex exception handling (lines 64-120+)
   - User indicated this should be simplified to just re-raise since it's wrapped by TopErrorHandler
   - Similar cleanup may be needed in `FileListener._reader()`

10. **Configuration Validation:**
    - No validation that `whisper_buffer_samples` and `seconds_per_scan` aren't both set
    - Could add `__post_init__` to PipelineConfig to validate mutually exclusive options

## Notes

- All changes maintain backward compatibility with existing event flow
- No changes to audio processing logic or event types
- TopErrorHandler pattern already in place, just removing redundant error handling
- Scripts become more explicit about what they're configuring
- Server wrapper classes were adding no significant value beyond configuration bundling

## Files Summary

**Files to Rename:**
- `src/palaver/scribe/listen_api.py` → `src/palaver/scribe/audio_listeners.py`

**Files to Delete:**
- `src/palaver/scribe/mic_server.py`
- `src/palaver/scribe/playback_server.py`

**Files to Modify:**
- `src/palaver/scribe/audio_listeners.py` (renamed from listen_api.py)
- `src/palaver/scribe/core.py`
- `src/palaver/scribe/listener/mic_listener.py`
- `src/palaver/scribe/listener/file_listener.py`
- `scripts/run_mic.py`
- `scripts/playback.py`
- `scripts/rescan.py`

**Total:** 1 file renamed, 2 files deleted, 7 files modified
