#!/bin/bash
# Wrapper script to run vad_recorder with uv

cd "$(dirname "$0")"
PYTHONPATH=src uv run python src/palaver/recorder/vad_recorder.py "$@"
