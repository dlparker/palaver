# note1.wav - Expected Test Behavior

## Audio Content Analysis

**Text content:**
```
"Clerk, start a new note. Clerk, This is the title. Clerk, This is the body, first sentence. Stop"
```

**Sentence breakdown:**
1. "Clerk, start a new note." - Command trigger
2. "Clerk, This is the title." - Title capture
3. "Clerk, This is the body, first sentence." - Body sentence 1
4. "Stop" - Body sentence 2 (triggers final silence)

**"Clerk," prefix usage:**
- Workaround for VAD quirk where speech start detection is delayed
- Should be filtered out by transcription processing
- Appears at start of sentences 1-3

**Silence timing:**
- Between sentences: 6 seconds (from --sentence-silence 6)
- This EXCEEDS normal VAD threshold (0.8s)
- This EXCEEDS long note threshold (5s)
- Should trigger long note termination

## Expected VAD Behavior

### Expected Scenario: Proper segmentation with long note mode
- **Segments detected:** 4 (one per sentence with 6s silence between)
- **Expected splits:**
  - Segment 1: "Clerk, start a new note." (normal mode, 0.8s threshold)
  - Segment 2: "Clerk, This is the title." (long note mode activated)
  - Segment 3: "Clerk, This is the body, first sentence." (long note mode, 5s threshold)
  - Segment 4: "Stop" (long note mode, 5s threshold)
- **Mode changes:**
  - Start: normal (0.8s threshold)
  - After seg 1 transcribed: detect "start a new note" → queue switch to long_note
  - Seg 2: long_note mode active (5s threshold)
  - After seg 2 transcribed: capture title
  - Seg 3-4: continue in long_note mode
  - After seg 4: 6s silence > 5s threshold → end note, queue switch to normal
- **Key test:** Long note should end after the 6-second silence following "Stop"

## What This Test CAN Verify

✅ File input processing works
✅ VAD detects speech vs silence (6s gaps)
✅ Audio resampling (22050 Hz → 48000 Hz)
✅ Mono → stereo conversion
✅ Segment creation and saving
✅ Transcription pipeline processes segments
✅ "start a new note" command detection
✅ Title capture from transcription
✅ Mode switch to long_note
✅ **Long note ending after 6 seconds of silence (> 5s threshold)**
✅ **Mode restoration to normal after long note**
✅ "Clerk," prefix handling in transcription

## What This Test CANNOT Verify

❌ Multiple note workflow (only one note)
❌ Real-time microphone interaction
❌ Edge cases with silence exactly at threshold (5.0s)

## Action Items

- [ ] Run test with current file (note1.wav with 6s silence)
- [ ] Verify 4 segments are detected
- [ ] Verify long note mode activates and deactivates correctly
- [ ] Compare file-based results to microphone behavior (2025-12-03 issue)
- [ ] If file test works but microphone doesn't, investigate microphone-specific issues

## Empirical Validation Needed

After first test run, document:
- Actual number of segments created
- Actual segment durations
- Transcription accuracy
- Whether segments match expected splits
- Any unexpected behavior
