#!/usr/bin/env python
import pytest
import asyncio
import time
# Import async backend from recorder directory
from palaver.recorder.recorder_backend_async import (
    AsyncRecorderBackend,
    RecorderEvent,
    RecordingStateChanged,
    VADModeChanged,
    SpeechDetected,
    SpeechEnded,
    TranscriptionQueued,
    TranscriptionComplete,
    NoteCommandDetected,
    NoteTitleCaptured,
    QueueStatus,
)

class RecorderAuto():

    def __init__(self):
        self.backend = AsyncRecorderBackend(event_callback=self.handle_recorder_event)
        self.current_segment = -1
        self.done = False

    async def run_till_done(self):
        print(f"records to {self.backend.get_session_path()}")
        await self.backend.start_recording()
        start_time = time.time()
        while time.time() - start_time < 20 and not self.done:
            asyncio.sleep(1)
        if not self.done:
            await self.backend.stop_recording()
            raise Exception('what!?')
                
    async def handle_recorder_event(self, event: RecorderEvent):
        """Handle events from backend (async callback)"""
        # Events come from async tasks, can directly update UI
        if isinstance(event, RecordingStateChanged):
            if event.is_recording:
                print("Recording started")
            else:
                print("Recording stopped")

        elif isinstance(event, VADModeChanged):
            self.mode_display.mode = event.mode
            if event.mode == "long_note":
                print("LONG NOTE MODE (5s silence)")
            else:
                print("Normal mode restored (0.8s)")
                self.done = True

        elif isinstance(event, SpeechDetected):
            self.current_segment = event.segment_index

        elif isinstance(event, SpeechEnded):
            if event.kept:
                print(f"[Processing... {event.duration_sec:.1f}s]")
            else:
                print(f"Segment discarded ({event.duration_sec:.1f}s < 1.2s)")

        elif isinstance(event, TranscriptionQueued):
            pass  # Already shown as "Processing..."

        elif isinstance(event, TranscriptionComplete):
            if event.success:
                print("transcription okay")
            else:
                print("transcription failed")

        elif isinstance(event, NoteCommandDetected):
            print("NEW NOTE DETECTED - Speak title next...")

        elif isinstance(event, NoteTitleCaptured):
            print(f" TITLE: {event.title}")

        elif isinstance(event, QueueStatus):
            pass


    
