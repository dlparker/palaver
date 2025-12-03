#!/bin/bash
echo "Clerk, start new note. This is the title. This is the body, first sentence. This is the second sentence." | uv run piper --model models/en_US-lessac-medium.onnx --sentence-silence 1 --output_file piper_out.wav
aplay piper_out.wav
