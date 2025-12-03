# TUI Troubleshooting

## Terminal Corruption After Crash

**Symptoms:**
- Terminal fills with control sequences
- Mouse highlighting doesn't work
- Can't see typed characters
- Ctrl+C doesn't work

**Cause:** Textual puts terminal in raw mode. If app crashes, raw mode isn't restored.

**Fix:**
```bash
# Type this (you won't see it, but it works):
reset

# Or:
stty sane

# If nothing works:
exit
# Open new terminal
```

## App Hangs on Start / Can't Kill with Ctrl+C

**Fixed in current version.** Previous issue was:
- VAD loading on import blocked UI thread
- Multiprocessing workers spawned from UI thread caused deadlock

**Solution implemented:**
1. **Lazy VAD loading** - VAD only loads when `start_recording()` called
2. **Background thread** - Initialization runs in separate thread
3. **Non-blocking** - UI stays responsive during startup

## Testing

```bash
# Test that backend doesn't block:
timeout 5 uv run python -c "
import sys
sys.path.insert(0, 'recorder')
from recorder_backend import RecorderBackend
b = RecorderBackend()
print('✓ Ready')
"

# Should print ✓ Ready and exit quickly
```

## Common Issues

### 1. "ModuleNotFoundError: No module named 'textual'"

**Fix:**
```bash
uv sync
# Or:
pip install textual rich
```

### 2. "ModuleNotFoundError: No module named 'recorder_backend'"

**Fix:** Run from project root:
```bash
cd /path/to/palaver
uv run python tui/recorder_tui.py
```

### 3. Audio device not found

**Fix:** Check `DEVICE = 3` in `recorder_backend.py` matches your hardware:
```bash
python -c "import sounddevice as sd; print(sd.query_devices())"
```

### 4. Whisper model not found

**Fix:** Ensure model file exists:
```bash
ls models/multilang_whisper_large3_turbo.ggml
```

Update path in `recorder_backend.py` if needed.

## Debug Mode

To see backend events:

```python
from recorder_backend import RecorderBackend

def debug_handler(event):
    print(f"[{event.__class__.__name__}] {event.__dict__}")

backend = RecorderBackend(event_callback=debug_handler)
backend.start_recording()
# ... recording ...
backend.stop_recording()
```

## Performance Tips

1. **Reduce workers if CPU constrained:**
   ```python
   NUM_WORKERS = 1  # In recorder_backend.py
   ```

2. **Increase queue if transcription is slow:**
   ```python
   JOB_QUEUE_SIZE = 20  # In recorder_backend.py
   ```

3. **Use faster Whisper model:**
   ```python
   WHISPER_MODEL = "models/tiny.en.ggml"  # Much faster
   ```
