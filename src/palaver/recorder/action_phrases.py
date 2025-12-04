"""
palaver/recorder/action_phrases.py
Action phrase detection with flexible matching for voice commands

Supports fuzzy matching with configurable thresholds and prefix filtering
to handle transcription variations and artifacts.
"""

import re
from typing import Optional


class ActionPhrase:
    """Base class for action phrase matching."""

    def match(self, text: str, threshold: Optional[float] = None,
              ignore_prefix: Optional[str] = None) -> float:
        """
        Match text against the action phrase pattern.

        Args:
            text: The transcribed text to match against
            threshold: Optional threshold for binary match (0.0 or 1.0)
            ignore_prefix: Optional regex pattern to strip from start of text

        Returns:
            Match score (0.0 to 1.0), or binary if threshold provided
        """
        raise NotImplementedError("Subclasses must implement match()")


class LooseActionPhrase(ActionPhrase):
    """
    Flexible action phrase matcher that ignores filler words and uses
    word overlap scoring.

    Examples:
        >>> phrase = LooseActionPhrase("start new note")
        >>> phrase.match("start a new note")  # "a" ignored
        1.0
        >>> phrase.match("new note")  # 2 of 3 words
        0.6666666666666666
        >>> phrase.match("new note", threshold=0.7)  # Below threshold
        0.0
        >>> phrase.match("start new note", threshold=0.7)  # At/above threshold
        1.0
    """

    # Common filler words to ignore during matching
    FILLER_WORDS = {
        'a', 'an', 'the', 'to', 'of', 'in', 'on', 'at', 'for', 'with',
        'and', 'or', 'but', 'is', 'are', 'was', 'were', 'be', 'been',
        'please', 'um', 'uh', 'like'
    }

    def __init__(self, pattern: str, threshold: Optional[float] = None,
                 ignore_prefix: Optional[str] = None):
        """
        Initialize with a pattern phrase and optional default matching parameters.

        Args:
            pattern: The phrase to match (e.g., "start new note")
            threshold: Default threshold for binary matching (0.0-1.0)
            ignore_prefix: Default regex pattern to strip from start of text
        """
        self.pattern = pattern.lower()
        self.pattern_words = self._normalize_words(self.pattern)
        self.threshold = threshold
        self.ignore_prefix = ignore_prefix

        if not self.pattern_words:
            raise ValueError(f"Pattern must contain at least one meaningful word: {pattern}")

    def _normalize_words(self, text: str) -> list[str]:
        """
        Extract meaningful words from text, ignoring filler words.

        Args:
            text: Input text

        Returns:
            List of normalized (lowercase) meaningful words
        """
        # Split on whitespace and punctuation
        words = re.findall(r'\b\w+\b', text.lower())
        # Filter out filler words
        return [w for w in words if w not in self.FILLER_WORDS]

    def match(self, text: str, threshold: Optional[float] = None,
              ignore_prefix: Optional[str] = None) -> float:
        r"""
        Match text against pattern using word overlap scoring.

        Method parameters override instance defaults set in __init__.

        Scoring:
            score = (matched_words / total_pattern_words)

        Args:
            text: The transcribed text to match against
            threshold: If provided, overrides instance default. Return 1.0 if score >= threshold, else 0.0
            ignore_prefix: If provided, overrides instance default. Regex pattern to strip from start of text
                          (e.g., r'^(clerk|lurk|clark),?\s*' for transcription artifacts)

        Returns:
            Match score (0.0 to 1.0), or binary 1.0/0.0 if threshold provided

        Examples:
            >>> phrase = LooseActionPhrase("start new note", threshold=0.66,
            ...                            ignore_prefix=r'^(clerk|lurk|clark),?\s*')
            >>> phrase.match("clerk, start a new note")  # Uses instance defaults
            1.0
            >>> phrase.match("start the note")  # "the" ignored, "start" and "note" match
            1.0
            >>> phrase.match("new note")  # 2 of 3 words, at threshold
            1.0
            >>> phrase.match("just note")  # 1 of 3 words, below threshold
            0.0
        """
        # Use method parameters if provided, otherwise fall back to instance defaults
        actual_threshold = threshold if threshold is not None else self.threshold
        actual_ignore_prefix = ignore_prefix if ignore_prefix is not None else self.ignore_prefix

        # Apply prefix filter if provided
        if actual_ignore_prefix:
            text = re.sub(actual_ignore_prefix, '', text, flags=re.IGNORECASE)

        # Normalize input text
        text_words = self._normalize_words(text)

        # Count how many pattern words appear in text
        # (using set for O(1) lookup, but preserving count logic for clarity)
        text_words_set = set(text_words)
        matches = sum(1 for word in self.pattern_words if word in text_words_set)

        # Calculate score
        score = matches / len(self.pattern_words) if self.pattern_words else 0.0

        # Apply threshold if provided
        if actual_threshold is not None:
            return 1.0 if score >= actual_threshold else 0.0

        return score

    def __repr__(self) -> str:
        parts = [f"pattern='{self.pattern}'", f"words={self.pattern_words}"]
        if self.threshold is not None:
            parts.append(f"threshold={self.threshold}")
        if self.ignore_prefix is not None:
            parts.append(f"ignore_prefix={self.ignore_prefix!r}")
        return f"LooseActionPhrase({', '.join(parts)})"
