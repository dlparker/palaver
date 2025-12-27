#!/usr/bin/env python
"""
tests/test_vad_recorder_file.py
Test VAD recorder with pre-recorded audio files
"""

import pytest
import asyncio
import sys
import os
import time
import uuid
from pprint import pprint
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

