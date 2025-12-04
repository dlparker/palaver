# Test Audio Samples

## note1.wav

**Created:** 2025-12-03
**Generation method:** Piper text-to-speech (see `../../piper.sh`)

### Content
```
"Clerk, start a new note. Clerk, This is the title. Clerk, This is the body, first sentence. Stop"
```

**Design notes:**
- **"Clerk," prefix**: Workaround for VAD quirk where speech start isn't detected soon enough
  - The transcription reader should filter out these extraneous bits
- **"Stop" at end**: Ensures the body has two sentences so there's silence after the first one
- **--sentence-silence 6**: Generates 6 seconds of silence between sentences
  - This exceeds the 5-second threshold for long note mode termination
  - Allows testing that long note mode properly ends

### Properties
- Sample rate: 22050 Hz (will be resampled to 48000 Hz)
- Channels: 1 (mono, will be converted to stereo)
- Sample width: 16-bit
- Duration: ~variable based on piper generation
- Sentence silence: 6 seconds

### Generation Command
```bash
echo "Clerk, start a new note. Clerk, This is the title. Clerk, This is the body, first sentence. Stop" | \
  uv run piper \
    --model models/en_US-lessac-medium.onnx \
    --sentence-silence 6 \
    --output_file tests/audio_samples/note1.wav
```

### Expected Behavior

**Voice commands:**
- "Clerk, start a new note" - Should trigger note detection (filter "Clerk,")
- "Clerk, This is the title" - Should be captured as note title (filter "Clerk,")
- "Clerk, This is the body, first sentence" - First sentence of body (filter "Clerk,")
- "Stop" - Second sentence, triggers 6-second silence

**VAD segments:**
With --sentence-silence 6 seconds:
- Silence between sentences is 6 seconds
- This EXCEEDS the 5-second threshold for long note mode
- Should properly test long note mode termination

**Expected workflow:**
1. Detect "start a new note" command → switch to long note mode
2. Capture title from next segment
3. Process body sentences in long note mode (5s silence threshold)
4. After 6s silence following "Stop", end the long note
5. Switch back to normal mode (0.8s threshold)

**Testing capabilities:**
- ✅ Can test long note mode termination (6s > 5s threshold)
- ✅ Tests "Clerk," prefix handling
- ✅ Tests multi-sentence body with silence

## Creating New Test Files

Use the piper.sh script as template:
```bash
echo "YOUR TEXT HERE" | \
  uv run piper \
    --model models/en_US-lessac-medium.onnx \
    --sentence-silence N \
    --output_file tests/audio_samples/your_file.wav
```

Adjust `--sentence-silence` based on test needs:
- `1` = Normal speech (0.8s VAD threshold testing)
- `6+` = Long note mode testing (5s VAD threshold)
