#!/usr/bin/env python
import pytest

from palaver.recorder.recorder_auto import RecorderAuto

async def test_auto():
    r_auto = RecorderAuto()
    await r_auto.run_till_done()
    
