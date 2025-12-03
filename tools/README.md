# Test Audio Generation Tools

This directory contains utilities for generating precise test audio files for the voice recorder.

## The Problem

When testing voice-activated workflows, you need precise control over silence duration:
- Short silences (0.8-1s) for normal speech pauses
- Long silences (5-6s) to trigger mode changes or workflow termination

Piper's `--sentence-silence` parameter applies **uniformly** to all sentences, making it impossible to create mixed silence patterns.

## The Solution

**Two-stage generation:**
1. Generate speech with Piper (uniform short silences)
2. Manipulate WAV files to add precise silence where needed

## Tools

### wav_utils.py

Core utility for WAV file manipulation.

**Features:**
- Append silence to end of WAV file
- Concatenate multiple WAV files with custom silence between each
- Maintains audio format (sample rate, channels, bit depth)

**Command-line usage:**

```bash
# Append 6 seconds of silence
python tools/wav_utils.py append input.wav output.wav --silence 6.0

# Concatenate with same silence everywhere
python tools/wav_utils.py concat a.wav b.wav c.wav -o output.wav --silence 1.0

# Concatenate with custom silence after each file
python tools/wav_utils.py concat a.wav b.wav c.wav -o output.wav --silence 1.0 1.0 6.0
#                                                                            ↑    ↑    ↑
#                                                                      after a   b    c
```

**Python API:**

```python
from tools.wav_utils import append_silence, concatenate_wavs

# Append silence
append_silence("input.wav", "output.wav", silence_sec=6.0)

# Concatenate with precise control
concatenate_wavs(
    input_wavs=["seg1.wav", "seg2.wav", "seg3.wav"],
    output_wav="final.wav",
    silence_between=[1.0, 1.0, 6.0]  # Silence after each segment
)
```

### generate_note_test.sh

Simple script for generating the "start new note" test file.

**Usage:**
```bash
cd /path/to/palaver
./tools/generate_note_test.sh
```

**What it does:**
1. Generates speech with Piper (1s sentence silence)
2. Appends 6s silence to end (exceeds 5s threshold)
3. Creates `tests/audio_samples/note1.wav`

### generate_test_audio_example.py

Advanced examples showing patterns for creating complex test scenarios.

**Usage:**
```bash
# Generate single note workflow test
python tools/generate_test_audio_example.py note

# Generate multi-note workflow test
python tools/generate_test_audio_example.py multi-note

# Show custom interaction pattern example
python tools/generate_test_audio_example.py custom-example
```

## Common Patterns

### Pattern 1: Simple Workflow with Final Silence

**Use case:** Testing workflow that ends with long silence

```bash
# Generate base audio
echo "Command. Action. Result." | \
    uv run piper --model models/en_US-lessac-medium.onnx \
                 --sentence-silence 1 \
                 --output_file base.wav

# Add final silence
python tools/wav_utils.py append base.wav final.wav --silence 6.0
```

### Pattern 2: Multi-Segment with Precise Timing

**Use case:** Testing multiple interactions with different silence durations

```python
from tools.wav_utils import concatenate_wavs

# Generate segments separately (or use existing files)
segments = [
    "tests/audio_samples/command.wav",
    "tests/audio_samples/title.wav",
    "tests/audio_samples/body1.wav",
    "tests/audio_samples/body2.wav",
]

# Define exact silence after each segment
silence = [
    1.0,  # After command: normal pause
    1.0,  # After title: normal pause
    1.0,  # After body1: normal pause
    6.0,  # After body2: long silence triggers end
]

concatenate_wavs(segments, "test.wav", silence_between=silence)
```

### Pattern 3: Programmatic Generation

**Use case:** Generating many test variations

```python
import subprocess
from pathlib import Path
from tools.wav_utils import concatenate_wavs

def generate_segment(text: str, output: Path):
    """Generate single speech segment"""
    subprocess.run(
        ["uv", "run", "piper",
         "--model", "models/en_US-lessac-medium.onnx",
         "--sentence-silence", "0",
         "--output_file", str(output)],
        input=text.encode()
    )

# Generate segments
temp_dir = Path("temp")
temp_dir.mkdir(exist_ok=True)

phrases = ["First.", "Second.", "Third.", "Fourth."]
segment_files = []

for i, phrase in enumerate(phrases):
    output = temp_dir / f"seg{i}.wav"
    generate_segment(phrase, output)
    segment_files.append(output)

# Combine with custom silence pattern
concatenate_wavs(
    segment_files,
    "output.wav",
    silence_between=[0.8, 0.8, 0.8, 6.0]
)
```

## Test File Design Guidelines

### For VAD Testing

**Normal mode (0.8s threshold):**
- Use 0.5-0.7s silence: Should NOT trigger segment end
- Use 1.0-1.5s silence: SHOULD trigger segment end
- Test boundary: ~0.8s (may be flaky)

**Long note mode (5.0s threshold):**
- Use 1.0-3.0s silence: Should NOT trigger segment end
- Use 6.0-8.0s silence: SHOULD trigger segment end
- Test boundary: ~5.0s (may be flaky)

**Best practices:**
- Avoid testing exactly at threshold (0.8s, 5.0s) - flaky
- Use clear margins: 0.5s or 1.5s for normal, 3s or 6s for long
- Document expected behavior in test

### For Command Detection Testing

**"Clerk," prefix:**
- All segments should start with "Clerk," to work around VAD speech-start detection quirk
- Transcription processing should filter these out
- Document in test that "Clerk," is expected in raw output

**Command phrases:**
- "start a new note" (or "start new note")
- Keep consistent for reliable detection
- Test variations separately if needed

### File Organization

```
tests/audio_samples/
├── README.md                    # Documentation of test files
├── note1.wav                    # Main single-note test
├── note1_base.wav              # Intermediate (before silence added)
├── multi_note.wav              # Multiple notes test
├── temp/                       # Temporary segment files
│   ├── seg1_command.wav
│   ├── seg2_title.wav
│   └── ...
└── [scenario]_expected.md      # Expected behavior documentation
```

## Extending for New Interactions

When designing new interaction types:

1. **Define the workflow:**
   - What commands/responses are involved?
   - What silence durations are meaningful?
   - What state changes should occur?

2. **Create segment content:**
   - Write out what should be spoken
   - Include "Clerk," prefix if needed
   - Keep segments short and focused

3. **Map silence to behavior:**
   - Which pauses are "normal" (0.8-1s)?
   - Which pauses trigger mode/state changes (5-6s)?
   - Which pauses are irrelevant (0.1-0.3s)?

4. **Generate test file:**
   - Use `generate_segment()` for each phrase
   - Use `concatenate_wavs()` with silence list
   - Document expected behavior

5. **Write test:**
   - Load file into recorder
   - Assert expected segments, transcriptions, state changes
   - Document what each silence duration tests

## Tips & Tricks

**Debugging silence:**
```bash
# Check actual silence duration in file
ffmpeg -i test.wav -af silencedetect=noise=-50dB:d=0.5 -f null -
```

**Visualizing audio:**
```bash
# Install sox if needed: apt install sox
sox test.wav -n spectrogram -o spectrogram.png
```

**Quick playback:**
```bash
aplay test.wav
# or
ffplay -autoexit test.wav
```

**Verify format:**
```bash
file test.wav
soxi test.wav  # If sox installed
```

## Future Enhancements

Potential additions:
- [ ] Support for other TTS engines (festival, espeak, etc.)
- [ ] Noise injection for robustness testing
- [ ] Batch generation from YAML specs
- [ ] Visual timeline generator showing silence durations
- [ ] Automatic test file validation
