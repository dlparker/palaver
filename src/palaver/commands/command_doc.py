"""
CommandDoc: Abstract base class for command-driven document workflows.

A CommandDoc defines:
1. The command phrase that triggers it ("start new note")
2. The sequence of speech buckets to fill (title, body, etc.)
3. How to render the final output file(s)
"""

from abc import ABC, abstractmethod
from typing import List, Dict, Any
from pathlib import Path
from palaver.commands.speech_bucket import SpeechBucket


class CommandDoc(ABC):
    """
    Abstract base class for command-driven document workflows.

    A CommandDoc defines:
    1. The command phrase that triggers it ("start new note")
    2. The sequence of speech buckets to fill (title, body, etc.)
    3. How to render the final output file(s)

    Subclasses implement specific document types (notes, emails, todos, etc.).
    """

    @property
    @abstractmethod
    def command_phrase(self) -> str:
        """
        Phrase that triggers this command.

        Examples: "start new note", "create reminder", "send email"

        Returns:
            Command phrase string (lowercase)
        """
        pass

    @property
    @abstractmethod
    def speech_buckets(self) -> List[SpeechBucket]:
        """
        Ordered list of speech buckets to fill.

        Buckets are filled sequentially in the order specified.

        Example: [title_bucket, body_bucket, tags_bucket]

        Returns:
            List of SpeechBucket instances
        """
        pass

    @abstractmethod
    def render(self, bucket_contents: Dict[str, str], output_dir: Path) -> List[Path]:
        """
        Generate output file(s) from filled buckets.

        Args:
            bucket_contents: Dictionary mapping bucket names to transcribed text
                Example: {"note_title": "My Meeting", "note_body": "Details..."}
            output_dir: Session directory to write files

        Returns:
            List of created file paths

        Raises:
            ValueError: If bucket_contents is missing required buckets
        """
        pass

    def validate_buckets(self):
        """
        Ensure bucket names and display_names are unique.

        Called during CommandDoc registration.

        Raises:
            ValueError: If duplicate names or display_names found
        """
        names = [b.name for b in self.speech_buckets]
        display_names = [b.display_name for b in self.speech_buckets]

        if len(names) != len(set(names)):
            duplicates = [n for n in names if names.count(n) > 1]
            raise ValueError(
                f"Duplicate bucket names in '{self.command_phrase}': {duplicates}"
            )

        if len(display_names) != len(set(display_names)):
            duplicates = [d for d in display_names if display_names.count(d) > 1]
            raise ValueError(
                f"Duplicate display_names in '{self.command_phrase}': {duplicates}"
            )

    def __repr__(self) -> str:
        """String representation for debugging."""
        return (
            f"{self.__class__.__name__}("
            f"command_phrase='{self.command_phrase}', "
            f"buckets={[b.name for b in self.speech_buckets]})"
        )
