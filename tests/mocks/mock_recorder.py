"""
tests/mocks/mock_recorder.py
Mock AsyncVADRecorder for TUI testing without audio
"""

import asyncio
import time
from pathlib import Path
from typing import Optional, Callable

from palaver.recorder.async_vad_recorder import (
    RecordingStateChanged,
    VADModeChanged,
    SpeechStarted,
    SpeechEnded,
    TranscriptionQueued,
    TranscriptionComplete,
    NoteCommandDetected,
    NoteTitleCaptured,
    QueueStatus,
)


class MockAsyncVADRecorder:
    """
    Mock recorder for TUI testing.

    Simulates recorder behavior by emitting events without actual audio processing.
    """

    def __init__(self, event_callback: Optional[Callable] = None):
        self.event_callback = event_callback
        self.is_recording = False
        self.session_dir = None
        self.vad_mode = "normal"
        self.segment_count = 0

    async def start_recording(self, input_source: Optional[str] = None, **kwargs):
        """Start mock recording session"""
        if self.is_recording:
            raise RuntimeError("Already recording")

        self.is_recording = True
        self.session_dir = Path("sessions/mock_session")
        self.segment_count = 0

        # Emit recording started event
        if self.event_callback:
            await self._emit_event(RecordingStateChanged(
                timestamp=time.time(),
                is_recording=True
            ))

    async def stop_recording(self) -> Path:
        """Stop mock recording session"""
        if not self.is_recording:
            raise RuntimeError("Not recording")

        self.is_recording = False

        # Emit recording stopped event
        if self.event_callback:
            await self._emit_event(RecordingStateChanged(
                timestamp=time.time(),
                is_recording=False
            ))

        return self.session_dir

    async def simulate_speech_segment(
        self,
        text: str,
        duration: float = 2.0,
        transcribe: bool = True
    ):
        """
        Simulate a speech segment with transcription.

        Args:
            text: Transcribed text
            duration: Segment duration in seconds
            transcribe: If True, emit transcription events
        """
        if not self.is_recording:
            raise RuntimeError("Not recording")

        segment_index = self.segment_count
        self.segment_count += 1

        # Emit speech started
        if self.event_callback:
            await self._emit_event(SpeechStarted(
                timestamp=time.time(),
                segment_index=segment_index,
                vad_mode=self.vad_mode
            ))

        # Small delay to simulate speech
        await asyncio.sleep(0.05)

        # Emit speech ended
        if self.event_callback:
            await self._emit_event(SpeechEnded(
                timestamp=time.time(),
                segment_index=segment_index,
                audio_data=None,  # Mock - no actual audio
                duration_sec=duration,
                kept=True
            ))

        if transcribe:
            # Emit transcription queued
            if self.event_callback:
                await self._emit_event(TranscriptionQueued(
                    timestamp=time.time(),
                    segment_index=segment_index,
                    wav_path=Path(f"mock_seg_{segment_index:04d}.wav"),
                    duration_sec=duration
                ))

            # Small delay to simulate transcription
            await asyncio.sleep(0.05)

            # Emit transcription complete
            if self.event_callback:
                await self._emit_event(TranscriptionComplete(
                    timestamp=time.time(),
                    segment_index=segment_index,
                    text=text,
                    success=True,
                    processing_time_sec=0.1
                ))

            # Check for note commands
            text_lower = text.lower()
            if "start new note" in text_lower or "start a new note" in text_lower:
                if self.event_callback:
                    await self._emit_event(NoteCommandDetected(
                        timestamp=time.time(),
                        segment_index=segment_index
                    ))
                    # Switch to long note mode
                    await self._switch_mode("long_note")

    async def simulate_mode_change(self, mode: str):
        """Simulate VAD mode change"""
        if mode != self.vad_mode:
            await self._switch_mode(mode)

    async def _switch_mode(self, mode: str):
        """Internal mode switch with event emission"""
        self.vad_mode = mode
        silence_ms = 5000 if mode == "long_note" else 800

        if self.event_callback:
            await self._emit_event(VADModeChanged(
                timestamp=time.time(),
                mode=mode,
                min_silence_ms=silence_ms
            ))

    async def simulate_note_workflow(self, title: str, body_segments: list):
        """
        Simulate complete note workflow.

        Args:
            title: Note title text
            body_segments: List of body text segments
        """
        # Command segment
        await self.simulate_speech_segment("start a new note", 1.5)

        # Title segment
        await self.simulate_speech_segment(title, 2.0)
        if self.event_callback:
            await self._emit_event(NoteTitleCaptured(
                timestamp=time.time(),
                segment_index=self.segment_count - 1,
                title=title
            ))

        # Body segments (in long note mode)
        for body_text in body_segments:
            await self.simulate_speech_segment(body_text, 3.0)

        # Return to normal mode
        await self._switch_mode("normal")

    async def simulate_queue_status(self, queued: int, completed: int):
        """Simulate queue status update"""
        if self.event_callback:
            await self._emit_event(QueueStatus(
                timestamp=time.time(),
                queued_jobs=queued,
                completed_transcriptions=completed,
                total_segments=self.segment_count
            ))

    async def _emit_event(self, event):
        """Emit event to callback"""
        if self.event_callback:
            if asyncio.iscoroutinefunction(self.event_callback):
                await self.event_callback(event)
            else:
                self.event_callback(event)

    # Mock methods that might be accessed
    def wait_for_completion(self):
        """Mock wait for completion"""
        pass
