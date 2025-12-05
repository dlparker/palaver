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
                 event_callback: Optional[Callable] = None,
                 keep_segment_files: bool = False):
        """
        Initialize TextProcessor.

        Args:
            session_dir: Directory to write transcripts
            result_queue: Queue to read TranscriptionResults from
            mode_change_callback: Callback to trigger VAD mode changes
                                 Called with "long_note" or "normal"
            event_callback: Callback for emitting events to UI/monitoring
                           Called with AudioEvent instances (thread-safe)
            keep_segment_files: If False, delete segment files after CommandDoc completion
        """
        self.session_dir = session_dir
        self.result_queue = result_queue
        self.mode_change_callback = mode_change_callback
        self.event_callback = event_callback
        self.keep_segment_files = keep_segment_files

        # State machine (legacy - will be replaced by command workflow)
        self.waiting_for_title = False
        self.current_note_title = None

        # Command workflow tracking
        self.current_command = None  # CommandDoc instance
        self.current_bucket_index = 0
        self.bucket_contents: Dict[str, str] = {}  # {bucket_name: accumulated_text}
        self.bucket_start_times: Dict[str, float] = {}  # {bucket_name: timestamp}
        self.command_start_time = None
        self.bucket_segment_indices: Dict[str, List[int]] = {}  # {bucket_name: [segment_indices]}
        self.mode_returned_to_normal = False  # Flag set when VAD mode changes back to normal

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
        Check for commands in transcribed text and handle bucket workflow.

        Workflow states:
        1. No active command → detect "start new note" → start command, begin note_title bucket
        2. In note_title bucket → next segment → fill bucket, start note_body bucket
        3. In note_body bucket → accumulate segments → (wait for mode change)
        4. Mode changes back to normal → complete note_body bucket, render note

        Args:
            result: TranscriptionResult to check for commands
        """
        if not result.success:
            return

        # Import event types
        from palaver.recorder.async_vad_recorder import (
            CommandDetected, BucketStarted, BucketFilled, NoteCommandDetected, NoteTitleCaptured
        )
        from palaver.commands.simple_note import SimpleNote

        # State 1: Check for "start new note" command (no active command)
        if self.current_command is None:
            match_score = self.start_note_phrase.match(result.text)

            if match_score > 0:
                # Create SimpleNote command instance
                self.current_command = SimpleNote()
                self.current_bucket_index = 0
                self.command_start_time = time.time()

                print("\n" + "="*70)
                print("📝 NEW NOTE DETECTED")
                print(f"   Command matched: {result.text}")
                print("Please speak the title for this note...")
                print("="*70 + "\n")

                # Emit CommandDetected event
                self._emit_event(CommandDetected(
                    timestamp=time.time(),
                    command_doc_type="SimpleNote",
                    command_phrase=self.current_command.command_phrase,
                    matched_text=result.text,
                    similarity_score=match_score
                ))

                # Emit legacy NoteCommandDetected for backward compatibility
                self._emit_event(NoteCommandDetected(
                    timestamp=time.time(),
                    segment_index=result.segment_index
                ))

                # Start first bucket (note_title)
                bucket = self.current_command.speech_buckets[0]
                self._start_bucket(bucket)

                return  # Don't process this segment as bucket content

        # State 2 & 3: Accumulate text to current bucket
        if self.current_command is not None:
            bucket = self.current_command.speech_buckets[self.current_bucket_index]
            bucket_name = bucket.name

            # Initialize bucket content if first segment
            if bucket_name not in self.bucket_contents:
                self.bucket_contents[bucket_name] = ""
                self.bucket_segment_indices[bucket_name] = []

            # Accumulate text
            self.bucket_contents[bucket_name] += " " + result.text
            self.bucket_segment_indices[bucket_name].append(result.segment_index)

            # Check if this is the note_title bucket (completes after one segment)
            if self.current_bucket_index == 0:  # note_title bucket
                # Complete note_title bucket and start note_body
                self._complete_bucket(bucket, result.segment_index)

                # Emit legacy NoteTitleCaptured event
                self._emit_event(NoteTitleCaptured(
                    timestamp=time.time(),
                    segment_index=result.segment_index,
                    title=result.text.strip()
                ))

                # Switch to long note mode for body
                if self.mode_change_callback:
                    self.mode_change_callback("long_note")

                print("\n" + "="*70)
                print(f"📌 TITLE: {result.text}")
                print("🎙️  LONG NOTE MODE ACTIVATED")
                print("Silence threshold: 5 seconds (continue speaking...)")
                print("="*70 + "\n")

                # Move to next bucket (note_body)
                self.current_bucket_index += 1
                if self.current_bucket_index < len(self.current_command.speech_buckets):
                    next_bucket = self.current_command.speech_buckets[self.current_bucket_index]
                    self._start_bucket(next_bucket)

            # Check if we're in note_body bucket and mode already changed back to normal
            elif self.current_bucket_index == 1 and self.mode_returned_to_normal:
                # This is the last segment transcription - complete the command
                bucket = self.current_command.speech_buckets[self.current_bucket_index]
                self._complete_bucket(bucket, result.segment_index)
                self._complete_command()

    def _start_bucket(self, bucket):
        """
        Start a new bucket and emit BucketStarted event.

        Args:
            bucket: SpeechBucket instance to start
        """
        from palaver.recorder.async_vad_recorder import BucketStarted

        self.bucket_start_times[bucket.name] = time.time()

        self._emit_event(BucketStarted(
            timestamp=time.time(),
            command_doc_type="SimpleNote",
            bucket_name=bucket.name,
            bucket_display_name=bucket.display_name,
            bucket_index=self.current_bucket_index,
            start_window_sec=bucket.start_window  # Multiplier value
        ))

    def _complete_bucket(self, bucket, last_segment_index: int):
        """
        Complete current bucket and emit BucketFilled event.

        Args:
            bucket: SpeechBucket instance that was completed
            last_segment_index: Index of last segment in bucket
        """
        from palaver.recorder.async_vad_recorder import BucketFilled

        bucket_name = bucket.name
        duration = time.time() - self.bucket_start_times[bucket_name]
        text = self.bucket_contents[bucket_name].strip()
        chunk_count = len(self.bucket_segment_indices[bucket_name])

        self._emit_event(BucketFilled(
            timestamp=time.time(),
            command_doc_type="SimpleNote",
            bucket_name=bucket_name,
            bucket_display_name=bucket.display_name,
            text=text,
            duration_sec=duration,
            chunk_count=chunk_count
        ))

    def notify_mode_changed(self, mode: str):
        """
        Notify TextProcessor of VAD mode change.

        Called when VADModeChanged event occurs. Used to detect when
        note_body bucket is complete (mode changes back to normal).

        Args:
            mode: New VAD mode ("normal" or "long_note")
        """
        # If we're in the note_body bucket and mode changed back to normal, set flag
        # The actual completion will happen when the next transcription arrives
        if (self.current_command is not None and
            self.current_bucket_index == 1 and  # note_body bucket (index 1)
            mode == "normal"):
            self.mode_returned_to_normal = True

    def _complete_command(self):
        """
        Complete command workflow: render output and emit CommandCompleted event.

        Called when all buckets are filled. Calls the CommandDoc's render()
        method to create output files and emits CommandCompleted event.

        If keep_segment_files=False, deletes segment WAV files used in this command.
        """
        from palaver.recorder.async_vad_recorder import CommandCompleted

        try:
            # Call render to create output files
            output_files = self.current_command.render(
                self.bucket_contents,
                self.session_dir
            )

            # Get all segment indices used in this command
            all_segment_indices = []
            for bucket_indices in self.bucket_segment_indices.values():
                all_segment_indices.extend(bucket_indices)

            # Emit CommandCompleted event
            self._emit_event(CommandCompleted(
                timestamp=time.time(),
                command_doc_type=self.current_command.__class__.__name__,
                output_files=output_files,
                bucket_contents=self.bucket_contents.copy()
            ))

            print("\n" + "="*70)
            print("✅ NOTE COMPLETED")
            print(f"   Output: {output_files[0].name}")

            # Cleanup segment files if configured
            if not self.keep_segment_files:
                deleted_count = self._cleanup_segment_files(all_segment_indices)
                if deleted_count > 0:
                    print(f"   Cleaned up {deleted_count} segment files")

            print("="*70 + "\n")

        except Exception as e:
            print(f"\n⚠️  Error completing command: {e}\n")

        finally:
            # Reset command workflow state
            self.current_command = None
            self.current_bucket_index = 0
            self.bucket_contents = {}
            self.bucket_start_times = {}
            self.command_start_time = None
            self.bucket_segment_indices = {}
            self.mode_returned_to_normal = False

    def _cleanup_segment_files(self, segment_indices: list) -> int:
        """
        Delete segment WAV files for the given segment indices.

        Args:
            segment_indices: List of segment indices to delete

        Returns:
            Number of files successfully deleted
        """
        deleted_count = 0
        for seg_idx in segment_indices:
            seg_file = self.session_dir / f"seg_{seg_idx:04d}.wav"
            try:
                if seg_file.exists():
                    seg_file.unlink()
                    deleted_count += 1
            except Exception as e:
                print(f"[TextProcessor] Warning: Failed to delete {seg_file.name}: {e}")

        return deleted_count

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
