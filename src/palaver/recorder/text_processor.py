"""
palaver/recorder/text_processor.py
Text processing and command detection for transcribed segments

Handles:
- Incremental transcript writing
- Command detection (ActionPhrase matching)
- Title capture
- Mode switching callbacks
- State machine for note workflow
- Event emission for UI integration
"""

import threading
import time
from pathlib import Path
from typing import Optional, Callable, Dict
from queue import Empty
from multiprocessing import Queue

from palaver.recorder.transcription import TranscriptionResult
from palaver.recorder.action_phrases import LooseActionPhrase


class TextProcessor:
    """
    Processes transcribed text segments and detects commands.

    This class is the core of downstream text processing. It receives
    TranscriptionResults and:
    1. Writes incremental transcripts
    2. Detects commands using ActionPhrase matching
    3. Manages state machine for note-taking workflow
    4. Triggers mode changes via callbacks

    Can be tested independently of audio/transcription by feeding it
    mock TranscriptionResults.
    """

    def __init__(self,
                 session_dir: Path,
                 result_queue: Queue,
                 mode_change_callback: Optional[Callable[[str], None]] = None,
                 event_callback: Optional[Callable] = None):
        """
        Initialize TextProcessor.

        Args:
            session_dir: Directory to write transcripts
            result_queue: Queue to read TranscriptionResults from
            mode_change_callback: Callback to trigger VAD mode changes
                                 Called with "long_note" or "normal"
            event_callback: Callback for emitting events to UI/monitoring
                           Called with AudioEvent instances (thread-safe)
        """
        self.session_dir = session_dir
        self.result_queue = result_queue
        self.mode_change_callback = mode_change_callback
        self.event_callback = event_callback

        # State machine
        self.waiting_for_title = False
        self.current_note_title = None

        # Results tracking
        self.results: Dict[int, TranscriptionResult] = {}

        # Threading
        self.running = True
        self.thread = None

        # File paths
        self.transcript_path = session_dir / "transcript_raw.txt"
        self.incremental_path = session_dir / "transcript_incremental.txt"

        # Initialize action phrase matchers with defaults
        # Prefix pattern handles transcription artifacts like "Clerk,", "lurk,", "clark,"
        self.start_note_phrase = LooseActionPhrase(
            pattern="start new note",
            threshold=0.66,  # Require at least 2 of 3 words to match
            ignore_prefix=r'^(clerk|lurk|clark|plurk),?\s*'
        )

        # Initialize transcript files
        self.transcript_path.write_text("# Raw Transcript\n")
        self.incremental_path.write_text("# Incremental Transcript (updates as segments complete)\n")

    def start(self):
        """Start collector thread to process results from queue."""
        self.thread = threading.Thread(target=self._collect_loop, daemon=True)
        self.thread.start()

    def _emit_event(self, event):
        """
        Emit event to callback if provided.

        Thread-safe event emission. Can be called from text processor thread.
        The event_callback should handle thread-safety (e.g., using
        asyncio.run_coroutine_threadsafe for async code).

        Args:
            event: AudioEvent instance to emit
        """
        if self.event_callback:
            try:
                self.event_callback(event)
            except Exception as e:
                print(f"[TextProcessor] Event callback error: {e}")

    def _collect_loop(self):
        """
        Main loop for collecting results from the transcription queue.

        Runs in a separate thread, continuously polling the result queue
        and processing each TranscriptionResult.
        """
        while self.running:
            try:
                result_dict = self.result_queue.get(timeout=0.5)
                if result_dict is None:  # Stop signal
                    break

                result = TranscriptionResult(**result_dict)
                self.process_result(result)

            except Empty:
                continue
            except Exception as e:
                print(f"[TextProcessor] Error processing result: {e}")

    def process_result(self, result: TranscriptionResult):
        """
        Process a single transcription result.

        1. Store result
        2. Write incremental update
        3. Emit TranscriptionComplete event
        4. Check for commands
        5. Update state machine
        6. Trigger callbacks

        Args:
            result: TranscriptionResult to process
        """
        self.results[result.segment_index] = result
        self._write_incremental(result)

        # Emit TranscriptionComplete event for UI updates
        if self.event_callback:
            # Import here to avoid circular dependency
            from palaver.recorder.async_vad_recorder import TranscriptionComplete
            self._emit_event(TranscriptionComplete(
                timestamp=time.time(),
                segment_index=result.segment_index,
                text=result.text,
                success=result.success,
                processing_time_sec=result.processing_time_sec,
                error_msg=result.error_msg
            ))

        self._check_commands(result)

    def _write_incremental(self, result: TranscriptionResult):
        """
        Write incremental transcript update for this segment.

        Args:
            result: TranscriptionResult to write
        """
        with open(self.incremental_path, 'a') as f:
            status = "✓" if result.success else "✗"
            f.write(f"\n{status} Segment {result.segment_index + 1}: {result.text}\n")
            if not result.success and result.error_msg:
                f.write(f"   Error: {result.error_msg}\n")

        print(f"[Collector] Segment {result.segment_index} transcribed: {result.text[:60]}...")

    def _check_commands(self, result: TranscriptionResult):
        """
        Check for commands in transcribed text and update state machine.

        State machine:
        1. Normal -> "start new note" detected -> waiting_for_title
        2. waiting_for_title -> next segment -> capture title -> long_note mode
        3. long_note -> segment ends -> (vad_recorder queues return to normal)

        Args:
            result: TranscriptionResult to check for commands
        """
        if not result.success or not self.mode_change_callback:
            return

        # State 1: Check for "start new note" command
        # Uses instance defaults: threshold=0.66, ignore_prefix for "Clerk," artifacts
        match_score = self.start_note_phrase.match(result.text)

        if not self.waiting_for_title and match_score > 0:
            # Enter title-waiting state
            self.waiting_for_title = True
            print("\n" + "="*70)
            print("📝 NEW NOTE DETECTED")
            print(f"   Command matched: {result.text}")
            print("Please speak the title for this note...")
            print("="*70 + "\n")

            # Emit NoteCommandDetected event
            if self.event_callback:
                from palaver.recorder.async_vad_recorder import NoteCommandDetected
                self._emit_event(NoteCommandDetected(
                    timestamp=time.time(),
                    segment_index=result.segment_index
                ))

        # State 2: Capture the title (next segment after command)
        elif self.waiting_for_title:
            self.waiting_for_title = False
            self.current_note_title = result.text

            # Emit NoteTitleCaptured event BEFORE mode change
            # This gives immediate UI feedback before the mode actually switches
            if self.event_callback:
                from palaver.recorder.async_vad_recorder import NoteTitleCaptured
                self._emit_event(NoteTitleCaptured(
                    timestamp=time.time(),
                    segment_index=result.segment_index,
                    title=result.text
                ))

            # Switch to long note mode
            self.mode_change_callback("long_note")
            print("\n" + "="*70)
            print(f"📌 TITLE: {result.text}")
            print("🎙️  LONG NOTE MODE ACTIVATED")
            print("Silence threshold: 5 seconds (continue speaking...)")
            print("="*70 + "\n")

    def stop(self):
        """Stop collector thread."""
        self.running = False
        if self.thread:
            self.thread.join(timeout=2.0)

    def finalize(self, total_segments: int):
        """
        Write final ordered transcript.

        Called at end of recording session to create the final transcript_raw.txt
        with all segments in order.

        Args:
            total_segments: Total number of segments recorded
        """
        lines = ["# Raw Transcript\n"]

        # Write in order, handling missing segments
        for i in range(total_segments):
            if i in self.results:
                result = self.results[i]
                lines.append(f"{i+1}. {result.text}")
            else:
                lines.append(f"{i+1}. [transcription pending or failed]")

        self.transcript_path.write_text("\n".join(lines))

        # Summary
        successful = sum(1 for r in self.results.values() if r.success)
        failed = total_segments - successful

        summary = [
            f"\n# Transcription Summary",
            f"Total segments: {total_segments}",
            f"Successful: {successful}",
            f"Failed: {failed}"
        ]

        with open(self.transcript_path, 'a') as f:
            f.write("\n".join(summary))
