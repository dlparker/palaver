# Async Backend - Thread Safety Fix

## The Problem

**Audio callback runs in a different thread** than the asyncio event loop.

When the audio callback tried to use `asyncio.create_task()`:
```python
def _audio_callback(self, indata, frames, time_info, status):
    # This runs in audio thread!
    asyncio.create_task(self._emit_event(...))  # ❌ WRONG THREAD
```

**Result**: Hung indefinitely because `create_task()` only works from the event loop's thread.

## The Solution

Use `asyncio.run_coroutine_threadsafe()` to schedule coroutines from other threads:

```python
def _schedule_coro(self, coro):
    """Schedule coroutine from audio thread"""
    if self.loop:
        asyncio.run_coroutine_threadsafe(coro, self.loop)

def _audio_callback(self, indata, frames, time_info, status):
    # Now safe from audio thread!
    self._schedule_coro(self._emit_event(...))  # ✅ CORRECT
```

## Key Changes

### 1. Store Event Loop Reference

```python
async def start_recording(self):
    self.loop = asyncio.get_event_loop()  # Store for audio thread
    # ... rest of setup
```

### 2. Use Thread-Safe Scheduling

```python
# Before (broken):
asyncio.create_task(coro)  # Only works from event loop thread

# After (fixed):
asyncio.run_coroutine_threadsafe(coro, self.loop)  # Works from any thread
```

### 3. Updated All Audio Callback Async Calls

- `_emit_event()` - Now scheduled thread-safe
- `_save_and_queue_segment()` - Now scheduled thread-safe
- `_apply_vad_mode_change()` - Event emission now thread-safe

## Architecture

```
┌─────────────────────────────────────┐
│  Event Loop Thread (Textual)        │
│                                     │
│  - await backend.start_recording()  │
│  - Event callbacks                  │
│  - UI updates                       │
└──────────────┬──────────────────────┘
               │
               │ self.loop reference
               │
┌──────────────▼──────────────────────┐
│  Audio Thread (sounddevice)         │
│                                     │
│  - _audio_callback()                │
│  - VAD processing (sync)            │
│  - _schedule_coro() ────────────┐   │
└─────────────────────────────────┼───┘
                                  │
                                  │ run_coroutine_threadsafe()
                                  │
┌─────────────────────────────────▼───┐
│  Back to Event Loop Thread          │
│                                     │
│  - async _emit_event()              │
│  - async _save_and_queue_segment()  │
└─────────────────────────────────────┘
```

## Why This Matters

**sounddevice audio callbacks run in a separate thread** for real-time performance. You can't use asyncio primitives directly from these callbacks.

**Pattern for mixing threads + asyncio:**
1. Store event loop reference: `self.loop = asyncio.get_event_loop()`
2. From other thread: `asyncio.run_coroutine_threadsafe(coro, self.loop)`
3. Coroutine runs in event loop thread safely

## Testing

Should now work without hanging:

```bash
uv run python tui/recorder_tui.py
# Press SPACE to start recording
# Should work without hanging!
```

## General Rule

**Any time you need to call async code from a sync callback in a different thread:**

```python
# DON'T (hangs):
def callback_from_other_thread():
    asyncio.create_task(my_async_func())

# DO (works):
def callback_from_other_thread():
    asyncio.run_coroutine_threadsafe(my_async_func(), loop)
```

This is a fundamental asyncio + threading pattern!
