#!/bin/bash
# Wrapper script to run VAD recorder with uv
# Now uses the async CLI interface

cd "$(dirname "$0")"
PYTHONPATH=src uv run python scripts/direct_recorder.py "$@"
