"""
Unit tests for action phrase matching classes.

Tests the flexible phrase matching system used for voice command detection.
"""

import pytest
from palaver.recorder.action_phrases import ActionPhrase, LooseActionPhrase


class TestActionPhrase:
    """Tests for the ActionPhrase base class."""

    def test_base_class_is_abstract(self):
        """Base class should raise NotImplementedError when match() is called."""
        phrase = ActionPhrase()
        with pytest.raises(NotImplementedError):
            phrase.match("test text")


class TestLooseActionPhrase:
    """Tests for the LooseActionPhrase implementation."""

    # ===== Initialization Tests =====

    def test_initialization_basic(self):
        """Test basic initialization with just a pattern."""
        phrase = LooseActionPhrase("start new note")
        assert phrase.pattern == "start new note"
        assert phrase.pattern_words == ["start", "new", "note"]
        assert phrase.threshold is None
        assert phrase.ignore_prefix is None

    def test_initialization_with_defaults(self):
        """Test initialization with threshold and ignore_prefix defaults."""
        phrase = LooseActionPhrase(
            "start new note",
            threshold=0.66,
            ignore_prefix=r'^clerk,?\s*'
        )
        assert phrase.pattern == "start new note"
        assert phrase.threshold == 0.66
        assert phrase.ignore_prefix == r'^clerk,?\s*'

    def test_initialization_empty_pattern_raises_error(self):
        """Pattern with only filler words should raise ValueError."""
        with pytest.raises(ValueError, match="must contain at least one meaningful word"):
            LooseActionPhrase("a the to")

    def test_pattern_normalization(self):
        """Pattern should be normalized to lowercase."""
        phrase = LooseActionPhrase("START NEW NOTE")
        assert phrase.pattern == "start new note"
        assert phrase.pattern_words == ["start", "new", "note"]

    # ===== Basic Matching Tests =====

    def test_exact_match_returns_1_0(self):
        """Exact match of all words should return 1.0."""
        phrase = LooseActionPhrase("start new note")
        assert phrase.match("start new note") == 1.0

    def test_case_insensitive_matching(self):
        """Matching should be case-insensitive."""
        phrase = LooseActionPhrase("start new note")
        assert phrase.match("START NEW NOTE") == 1.0
        assert phrase.match("Start New Note") == 1.0
        assert phrase.match("StArT nEw NoTe") == 1.0

    def test_partial_match_scoring(self):
        """Partial matches should return fractional scores."""
        phrase = LooseActionPhrase("start new note")

        # 3 of 3 words
        assert phrase.match("start new note") == 1.0

        # 2 of 3 words (approximately 0.667)
        score = phrase.match("new note")
        assert 0.66 <= score <= 0.67

        score = phrase.match("start note")
        assert 0.66 <= score <= 0.67

        # 1 of 3 words (approximately 0.333)
        score = phrase.match("start")
        assert 0.33 <= score <= 0.34

    def test_no_match_returns_0_0(self):
        """Text with no matching words should return 0.0."""
        phrase = LooseActionPhrase("start new note")
        assert phrase.match("hello world") == 0.0
        assert phrase.match("completely different") == 0.0

    # ===== Filler Word Tests =====

    def test_filler_words_ignored_in_pattern(self):
        """Filler words should be removed from pattern."""
        phrase = LooseActionPhrase("start a new note")
        # "a" should be filtered out
        assert phrase.pattern_words == ["start", "new", "note"]

    def test_filler_words_ignored_in_text(self):
        """Filler words in input text should be ignored."""
        phrase = LooseActionPhrase("start new note")

        # All these should match perfectly (filler words ignored)
        assert phrase.match("start a new note") == 1.0
        assert phrase.match("start the new note") == 1.0
        assert phrase.match("please start a new note") == 1.0
        assert phrase.match("start and new and note") == 1.0

    def test_multiple_filler_words(self):
        """Multiple filler words should all be ignored."""
        phrase = LooseActionPhrase("create document")
        assert phrase.match("please create a new document for me") == 1.0

    # ===== Threshold Tests =====

    def test_threshold_at_init_binary_matching(self):
        """Threshold set at init should enable binary matching."""
        phrase = LooseActionPhrase("start new note", threshold=0.66)

        # Above threshold: return 1.0
        assert phrase.match("start new note") == 1.0  # 3/3 = 1.0
        assert phrase.match("new note") == 1.0        # 2/3 = 0.67

        # Below threshold: return 0.0
        assert phrase.match("note") == 0.0            # 1/3 = 0.33

    def test_threshold_in_method_overrides_no_default(self):
        """Threshold passed to match() should work when no default set."""
        phrase = LooseActionPhrase("start new note")

        # Without threshold, get raw score
        score = phrase.match("new note")
        assert 0.66 <= score <= 0.67

        # With threshold in method, get binary
        assert phrase.match("new note", threshold=0.66) == 1.0
        assert phrase.match("note", threshold=0.66) == 0.0

    def test_threshold_in_method_overrides_instance_default(self):
        """Threshold in method should override instance default."""
        phrase = LooseActionPhrase("start new note", threshold=0.66)

        # Instance default threshold=0.66
        assert phrase.match("new note") == 1.0  # 2/3 = 0.67 >= 0.66

        # Override with stricter threshold
        assert phrase.match("new note", threshold=0.9) == 0.0  # 0.67 < 0.9

        # Override with looser threshold
        assert phrase.match("note", threshold=0.3) == 1.0  # 0.33 >= 0.3

    def test_threshold_at_boundary(self):
        """Test exact boundary conditions for threshold."""
        phrase = LooseActionPhrase("start new note")

        # Exactly at threshold
        score = phrase.match("new note")  # 2/3 = 0.6666...
        assert phrase.match("new note", threshold=0.666) == 1.0
        assert phrase.match("new note", threshold=0.6666) == 1.0
        assert phrase.match("new note", threshold=0.667) == 0.0

    # ===== Prefix Filtering Tests =====

    def test_ignore_prefix_at_init(self):
        """Prefix filter set at init should remove matched prefixes."""
        phrase = LooseActionPhrase(
            "start new note",
            ignore_prefix=r'^clerk,?\s*'
        )

        # Should strip "clerk," or "clerk " prefix
        assert phrase.match("clerk, start new note") == 1.0
        assert phrase.match("clerk start new note") == 1.0
        assert phrase.match("Clerk, start new note") == 1.0  # Case insensitive

    def test_ignore_prefix_multiple_patterns(self):
        """Prefix regex should support multiple alternatives."""
        phrase = LooseActionPhrase(
            "start new note",
            ignore_prefix=r'^(clerk|lurk|clark|plurk),?\s*'
        )

        # All these prefixes should be stripped
        assert phrase.match("clerk, start new note") == 1.0
        assert phrase.match("lurk, start new note") == 1.0
        assert phrase.match("clark, start new note") == 1.0
        assert phrase.match("plurk, start new note") == 1.0
        assert phrase.match("Clerk, start new note") == 1.0  # Case insensitive

    def test_ignore_prefix_in_method_overrides(self):
        """Prefix filter in method should override instance default."""
        phrase = LooseActionPhrase(
            "start new note",
            ignore_prefix=r'^clerk,?\s*'
        )

        # Instance default strips "clerk,"
        assert phrase.match("clerk, start new note") == 1.0

        # Override to strip different prefix
        assert phrase.match("please start new note",
                           ignore_prefix=r'^please\s+') == 1.0

        # Original "clerk," prefix not stripped when overridden with None
        # This disables prefix filtering entirely
        score = phrase.match("clerk, start new note", ignore_prefix=None)
        # Note: "clerk" becomes an extra word, but extra words don't hurt score
        # Score is based on pattern words found, so still 1.0 (all 3 pattern words present)
        assert score == 1.0

        # To test override works, use text where default would help but override doesn't
        assert phrase.match("clerk, start new note") == 1.0  # Uses default, strips "clerk,"
        score = phrase.match("please, start new note")  # Default doesn't strip "please,"
        # "please" is a filler word so gets ignored anyway, score is 1.0
        assert score == 1.0

        # Better test: override with a prefix that would strip needed words
        score = phrase.match("start new note", ignore_prefix=r'^start\s*')
        # Strips "start", leaving "new note", so only 2/3 pattern words found
        assert 0.66 <= score <= 0.67

    def test_ignore_prefix_no_match_no_effect(self):
        """Prefix that doesn't match should not affect the text."""
        phrase = LooseActionPhrase(
            "start new note",
            ignore_prefix=r'^clerk,?\s*'
        )

        # Text without prefix should work normally
        assert phrase.match("start new note") == 1.0

    # ===== Combined Features Tests =====

    def test_threshold_and_prefix_together(self):
        """Test threshold and prefix filtering working together."""
        phrase = LooseActionPhrase(
            "start new note",
            threshold=0.66,
            ignore_prefix=r'^(clerk|lurk),?\s*'
        )

        # Should strip prefix and apply threshold
        assert phrase.match("clerk, start new note") == 1.0  # 3/3 >= 0.66
        assert phrase.match("lurk, new note") == 1.0         # 2/3 >= 0.66
        assert phrase.match("clerk, note") == 0.0            # 1/3 < 0.66

    def test_threshold_prefix_and_filler_words_together(self):
        """Test all features working together."""
        phrase = LooseActionPhrase(
            "start new note",
            threshold=0.66,
            ignore_prefix=r'^clerk,?\s*'
        )

        # Should strip "clerk,", ignore "a" and "the", then apply threshold
        assert phrase.match("clerk, please start a new note") == 1.0
        assert phrase.match("clerk, the new note") == 1.0  # 2/3 >= 0.66
        assert phrase.match("clerk, just a note") == 0.0   # 1/3 < 0.66

    # ===== Edge Cases =====

    def test_empty_text_returns_0_0(self):
        """Empty text should return 0.0."""
        phrase = LooseActionPhrase("start new note")
        assert phrase.match("") == 0.0

    def test_text_with_only_filler_words_returns_0_0(self):
        """Text with only filler words should return 0.0."""
        phrase = LooseActionPhrase("start new note")
        assert phrase.match("a the to") == 0.0
        assert phrase.match("please um uh") == 0.0

    def test_extra_words_dont_reduce_score(self):
        """Extra words in text should not reduce the score."""
        phrase = LooseActionPhrase("start note")
        # Both pattern words present, extra words don't hurt
        assert phrase.match("hello start something note goodbye") == 1.0

    def test_punctuation_handling(self):
        """Punctuation should be handled correctly."""
        phrase = LooseActionPhrase("start new note")
        assert phrase.match("start, new, note.") == 1.0
        assert phrase.match("start! new? note!") == 1.0
        assert phrase.match("start...new...note") == 1.0

    def test_word_boundaries(self):
        """Matching should respect word boundaries."""
        phrase = LooseActionPhrase("start")
        # "start" should match as a word
        assert phrase.match("start") == 1.0
        assert phrase.match("started") == 0.0  # Different word
        assert phrase.match("restart") == 0.0  # Different word

    # ===== Repr Tests =====

    def test_repr_without_defaults(self):
        """Repr should show pattern and words."""
        phrase = LooseActionPhrase("start new note")
        repr_str = repr(phrase)
        assert "pattern='start new note'" in repr_str
        assert "words=['start', 'new', 'note']" in repr_str
        assert "threshold" not in repr_str
        assert "ignore_prefix" not in repr_str

    def test_repr_with_defaults(self):
        """Repr should show all configured parameters."""
        phrase = LooseActionPhrase(
            "start new note",
            threshold=0.66,
            ignore_prefix=r'^clerk,?\s*'
        )
        repr_str = repr(phrase)
        assert "pattern='start new note'" in repr_str
        assert "threshold=0.66" in repr_str
        assert "ignore_prefix=" in repr_str

    # ===== Real-World Scenarios =====

    def test_note_command_variations(self):
        """Test realistic variations of the 'start new note' command."""
        phrase = LooseActionPhrase(
            "start new note",
            threshold=0.66,
            ignore_prefix=r'^(clerk|lurk|clark|plurk),?\s*'
        )

        # Perfect matches (with transcription artifacts)
        assert phrase.match("clerk, start a new note") == 1.0
        assert phrase.match("lurk, start new note") == 1.0

        # Partial matches above threshold
        assert phrase.match("clerk, new note") == 1.0  # 2/3
        assert phrase.match("start note") == 1.0       # 2/3

        # Below threshold
        assert phrase.match("note") == 0.0              # 1/3
        assert phrase.match("start") == 0.0             # 1/3

    def test_similar_but_different_commands(self):
        """Test that similar commands can be distinguished."""
        start_phrase = LooseActionPhrase("start new note", threshold=0.66)
        end_phrase = LooseActionPhrase("end note", threshold=0.66)

        text = "start a new note"
        assert start_phrase.match(text) == 1.0
        assert end_phrase.match(text) == 0.0  # Only 1/2 words match

        text = "end the note"
        assert end_phrase.match(text) == 1.0
        score = start_phrase.match(text)
        assert score < 0.66  # Only "note" matches, 1/3 = 0.33
