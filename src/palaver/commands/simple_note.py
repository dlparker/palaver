"""
SimpleNote: Basic note-taking workflow with title and body.

Replaces the hardcoded "note" system from text_processor.py.
"""

import re
from pathlib import Path
from typing import List, Dict
from palaver.commands.command_doc import CommandDoc
from palaver.commands.speech_bucket import SpeechBucket


class SimpleNote(CommandDoc):
    """
    Simple note-taking workflow: title + body.

    Command: "start new note"
    Buckets: title (quick), body (long with chunking)
    Output: Markdown file with slugified filename
    """

    @property
    def command_phrase(self) -> str:
        return "start new note"

    @property
    def speech_buckets(self) -> List[SpeechBucket]:
        return [
            SpeechBucket(
                name="note_title",
                display_name="Note Title",
                segment_size=0.4,         # 0.4 × 5.0 = 2.0s chunks (quick feedback)
                start_window=3.0,         # 3.0 × 2.0 = 6.0s wait (time to think)
                termination_silence=1.0   # 1.0 × 0.8 = 0.8s silence (normal mode)
            ),
            SpeechBucket(
                name="note_body",
                display_name="Note Body",
                segment_size=0.5,         # 0.5 × 5.0 = 2.5s chunks (real-time feedback)
                start_window=2.0,         # 2.0 × 2.0 = 4.0s wait
                termination_silence=6.25  # 6.25 × 0.8 = 5.0s silence (long mode)
            ),
        ]

    def render(self, bucket_contents: Dict[str, str], output_dir: Path) -> List[Path]:
        """
        Write note to markdown file.

        Filename format: note_NNNN_slugified_title.md

        Args:
            bucket_contents: {"note_title": "...", "note_body": "..."}
            output_dir: Session directory

        Returns:
            List containing single path to created markdown file

        Raises:
            ValueError: If required buckets are missing
        """
        # Validate required buckets
        if "note_title" not in bucket_contents:
            raise ValueError("Missing required bucket: note_title")

        if "note_body" not in bucket_contents:
            raise ValueError("Missing required bucket: note_body")

        title = bucket_contents["note_title"].strip()
        body = bucket_contents["note_body"].strip()

        # Handle empty title
        if not title:
            title = "Untitled"

        # Generate slug for filename (lowercase, alphanumeric + underscores)
        slug = re.sub(r'[^a-z0-9]+', '_', title.lower()).strip('_')
        # Limit slug length
        slug = slug[:50] if slug else "untitled"

        # Find next note number by counting existing notes
        existing = list(output_dir.glob("note_*.md"))
        note_num = len(existing) + 1

        # Create filename
        filename = f"note_{note_num:04d}_{slug}.md"
        filepath = output_dir / filename

        # Write markdown content
        content = f"# {title}\n\n{body}\n"
        filepath.write_text(content)

        return [filepath]
