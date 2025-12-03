# Async Recorder Backend

**File**: `recorder_backend_async.py`

## Why Async?

The async version provides clean integration with async frameworks (Textual, FastAPI, etc.) without threading hacks:

### Threading Version (Old)
```python
backend = RecorderBackend()
backend.start_recording()  # Spawns thread internally, hope it works
```

**Problems:**
- Hidden thread management
- Race conditions possible
- `call_from_thread()` needed for UI updates
- Multiprocessing + threading = complexity

### Async Version (New)
```python
backend = AsyncRecorderBackend()
await backend.start_recording()  # Clean async/await
```

**Benefits:**
- ✅ Explicit async flow
- ✅ No hidden threads
- ✅ Integrates naturally with Textual/asyncio
- ✅ Blocking operations (torch.hub.load, file I/O) run in executor
- ✅ Events can be async or sync callbacks

## Usage

### Basic Example

```python
import asyncio
from recorder_backend_async import AsyncRecorderBackend

async def my_handler(event):
    print(f"Event: {event.__class__.__name__}")

async def main():
    backend = AsyncRecorderBackend(event_callback=my_handler)

    # Start recording (non-blocking)
    await backend.start_recording()

    # Let it record for 10 seconds
    await asyncio.sleep(10)

    # Stop recording (async)
    await backend.stop_recording()

    print(f"Session: {backend.get_session_path()}")

asyncio.run(main())
```

### With Textual

```python
from textual.app import App
from recorder_backend_async import AsyncRecorderBackend

class MyApp(App):
    def __init__(self):
        super().__init__()
        self.backend = AsyncRecorderBackend(event_callback=self.handle_event)

    async def handle_event(self, event):
        """Async callback - can directly update UI"""
        self.status.update(f"Event: {event.__class__.__name__}")

    async def action_toggle_recording(self):
        """Keybinding handler"""
        if self.backend.is_recording:
            await self.backend.stop_recording()
        else:
            await self.backend.start_recording()
```

## Key Async Features

### 1. Lazy VAD Loading

VAD loads on first `start_recording()` call, in executor:

```python
await self._ensure_vad_loaded()  # Runs torch.hub.load in thread pool
```

### 2. Non-Blocking File I/O

```python
await loop.run_in_executor(None, self._save_wav, path, audio)
```

### 3. Async Result Collection

```python
async def _result_collector_loop(self):
    while self.is_recording:
        result = await loop.run_in_executor(None, self.result_queue.get, 0.5)
        await self._emit_event(TranscriptionComplete(...))
```

### 4. Event Callbacks (Async or Sync)

Backend handles both:

```python
async def _emit_event(self, event):
    if asyncio.iscoroutinefunction(self.event_callback):
        await self.event_callback(event)  # Async
    else:
        self.event_callback(event)  # Sync
```

## Audio Callback (Sync)

The `_audio_callback()` must be sync (called by sounddevice in audio thread):

```python
def _audio_callback(self, indata, frames, time_info, status):
    # Sync VAD processing
    window = self.vad(vad_chunk, return_seconds=False)

    # Schedule async event emission
    asyncio.create_task(self._emit_event(SpeechDetected(...)))
```

## Comparison

| Feature | Threading Version | Async Version |
|---------|------------------|---------------|
| Initialization | Instant | Instant |
| VAD Loading | On first start | On first start |
| VAD Loading Method | Thread pool | Executor |
| Event Delivery | `call_from_thread()` | Direct async |
| File I/O | Blocking | Executor |
| Result Collection | Thread | Async task |
| Integration | Manual sync | Native async |
| Textual Fit | Awkward | Perfect |

## Migration from Threading Version

### Before (Threading)
```python
from recorder_backend import RecorderBackend

backend = RecorderBackend(event_callback=my_handler)
backend.start_recording()  # Sync
backend.stop_recording()   # Sync
```

### After (Async)
```python
from recorder_backend_async import AsyncRecorderBackend

backend = AsyncRecorderBackend(event_callback=my_handler)
await backend.start_recording()  # Async
await backend.stop_recording()   # Async
```

**Event callbacks** can be async:
```python
# Before (sync callback)
def my_handler(event):
    print(event)

# After (async callback)
async def my_handler(event):
    await some_async_operation()
    print(event)
```

## Performance

No performance difference - same worker processes, same VAD, same transcription. Just cleaner async integration.

## When to Use Which

**Use `recorder_backend_async.py` when:**
- ✅ Using Textual, FastAPI, or other async framework
- ✅ You're comfortable with async/await
- ✅ Want clean async flow

**Use `recorder_backend.py` when:**
- ✅ Simple CLI script
- ✅ Sync-only environment
- ✅ Don't want to deal with asyncio

Both backends produce identical outputs and have the same features.
