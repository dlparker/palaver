# Recorder Backend Architecture

## Threading vs Multiprocessing - What's Really Happening?

### TL;DR

- **0 threads created by us** (async backend)
- **1 audio thread** (created by sounddevice library, not us)
- **N worker processes** (for Whisper transcription)
- **Asyncio event loop** (main thread, runs Textual + our code)

---

## Process & Thread Map

```
┌─────────────────────────────────────────────────────────────┐
│ MAIN PROCESS                                                 │
│                                                              │
│  ┌────────────────────────────────────────────────────┐    │
│  │ Main Thread (asyncio event loop)                   │    │
│  │                                                     │    │
│  │ - Textual UI                                       │    │
│  │ - await backend.start_recording()                  │    │
│  │ - async event callbacks                            │    │
│  │ - UI updates                                       │    │
│  └────────────────────────────────────────────────────┘    │
│                                                              │
│  ┌────────────────────────────────────────────────────┐    │
│  │ Audio Thread (created by sounddevice)             │    │
│  │                                                     │    │
│  │ - _audio_callback() [sync, real-time]             │    │
│  │ - VAD processing                                   │    │
│  │ - Schedules async tasks via                        │    │
│  │   run_coroutine_threadsafe()                       │    │
│  └────────────────────────────────────────────────────┘    │
│                                                              │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│ WORKER PROCESS 1                                             │
│                                                              │
│ - transcription_worker()                                     │
│ - Runs Whisper CLI subprocess                               │
│ - Puts results in Queue                                      │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│ WORKER PROCESS 2                                             │
│                                                              │
│ - transcription_worker()                                     │
│ - Runs Whisper CLI subprocess                               │
│ - Puts results in Queue                                      │
└─────────────────────────────────────────────────────────────┘
```

---

## What Uses What

### 1. Multiprocessing (Worker Processes)

**Used for**: Whisper transcription

**Why**: True parallelism for CPU-intensive work, bypasses Python's GIL

```python
for i in range(NUM_WORKERS):
    p = Process(target=transcription_worker, ...)
    p.start()
```

**Benefits**:
- Multiple Whisper transcriptions in parallel
- Each on separate CPU core
- GIL not a problem

### 2. Audio Thread (sounddevice library)

**Used for**: Real-time audio capture

**Why**: Audio callbacks need low latency, sounddevice creates its own thread

```python
self.stream = sd.InputStream(callback=self._audio_callback, ...)
# sounddevice creates thread internally
```

**Not our choice** - this is how sounddevice works. The callback runs in a separate thread for timing guarantees.

### 3. Asyncio (Main Thread)

**Used for**: Everything else

**Why**: Clean async/await, integrates with Textual

```python
async def start_recording(self):
    await self._ensure_vad_loaded()  # Runs in executor (thread pool)
    # ... setup ...
    await self._emit_event(...)  # Async event emission
```

**Benefits**:
- No explicit thread management
- Clean async flow
- Integrates naturally with Textual

### 4. Thread Pool Executor (Hidden)

**Used for**: Blocking I/O operations

**Why**: Keep event loop responsive during blocking calls

```python
# Runs torch.hub.load in thread pool:
await loop.run_in_executor(None, lambda: torch.hub.load(...))

# Runs file I/O in thread pool:
await loop.run_in_executor(None, self._save_wav, path, audio)
```

**Managed by asyncio** - we don't create threads, asyncio does automatically.

---

## Why No Explicit Threading?

### Old Version (recorder_backend.py)
```python
# Manual thread management:
self.result_collector_thread = threading.Thread(
    target=self._result_collector_loop,
    daemon=True
)
self.result_collector_thread.start()

# Startup in background thread:
threading.Thread(target=self._start_recording_async).start()
```

**Problems**:
- Manual thread lifecycle
- Need `call_from_thread()` for UI updates
- Race conditions possible
- Complex cleanup

### Async Version (recorder_backend_async.py)
```python
# Async task (no threads):
self.result_collector_task = asyncio.create_task(
    self._result_collector_loop()
)

# Blocking ops in executor (asyncio manages threads):
await loop.run_in_executor(None, blocking_function)
```

**Benefits**:
- No manual thread management
- Asyncio handles thread pool
- Clean async/await
- Automatic cleanup

---

## Communication Paths

### Audio Thread → Event Loop
```python
# In audio thread:
def _audio_callback(self, ...):
    asyncio.run_coroutine_threadsafe(
        self._emit_event(...),
        self.loop  # Main thread's event loop
    )
```

### Event Loop → Worker Processes
```python
# Via multiprocessing Queue:
self.job_queue.put(job.to_dict())  # Event loop thread
# ... worker process reads from queue ...
```

### Worker Processes → Event Loop
```python
# Via multiprocessing Queue:
self.result_queue.put(result)  # Worker process
# ... async task polls queue in executor ...
```

---

## Summary Table

| Component | Type | Created By | Purpose |
|-----------|------|------------|---------|
| Main thread | Thread | Python | Asyncio event loop, Textual UI |
| Audio thread | Thread | sounddevice | Real-time audio callbacks |
| Worker processes | Process | Us (multiprocessing) | Parallel Whisper transcription |
| Thread pool | Threads | asyncio | Blocking I/O (torch.hub.load, file writes) |
| Result collector | Async task | Us (asyncio) | Poll transcription results |

---

## Key Insight

**We don't create any threads manually.** The only threads are:
1. Main thread (Python default)
2. Audio thread (sounddevice library)
3. Thread pool (asyncio default, for blocking I/O)

Everything else is either:
- **Async tasks** (coroutines in event loop)
- **Worker processes** (true parallelism for CPU work)

This is **much cleaner** than explicit threading!

---

## Could We Eliminate All Threads?

### Audio Thread: No
sounddevice requires it for real-time audio. This is non-negotiable.

### Thread Pool: Yes, but why?
You could make everything async (async file I/O, async torch loading) but:
- Complexity increases
- Marginal benefit
- `run_in_executor()` is perfect for occasional blocking ops

### Worker Processes: No
Python multiprocessing is the right tool for CPU-bound parallel work.

---

## Conclusion

**Current design is optimal:**
- Multiprocessing for CPU-intensive work (Whisper)
- Asyncio for I/O and coordination (event loop)
- Sounddevice's audio thread (required)
- Thread pool executor for blocking ops (automatic)

**No manual thread management needed!**
