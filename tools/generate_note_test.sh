#!/bin/bash
#
# Generate test audio file for "start new note" workflow
# Uses piper for speech generation and wav_utils.py for precise silence control

set -e  # Exit on error

# Configuration
MODEL="models/en_US-lessac-medium.onnx"
SENTENCE_SILENCE=1  # Short silence between sentences (default)
FINAL_SILENCE=6     # Long silence at end to trigger note termination (must be > 5s)

# Generate speech with piper
# Using 1-second silence between sentences (default/natural)
echo "Clerk, start a new note. Clerk, This is the title. Clerk, This is the body, first sentence. Stop" | \
    uv run piper \
        --model "$MODEL" \
        --sentence-silence "$SENTENCE_SILENCE" \
        --output_file tests/audio_samples/note1_base.wav

echo "✓ Generated base audio with piper"

# Append additional silence to the end (6 seconds total to exceed 5s threshold)
PYTHONPATH=. uv run python tools/wav_utils.py append \
    tests/audio_samples/note1_base.wav \
    tests/audio_samples/note1.wav \
    --silence "$FINAL_SILENCE"

echo "✓ Added $FINAL_SILENCE seconds of silence to end"

# Optional: play the result
if command -v aplay &> /dev/null; then
    echo ""
    read -p "Play the generated file? (y/n) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        aplay tests/audio_samples/note1.wav
    fi
fi

echo ""
echo "✅ Test file ready: tests/audio_samples/note1.wav"
echo "   - Speech with 1s pauses between sentences"
echo "   - Final ${FINAL_SILENCE}s silence to trigger note end"
