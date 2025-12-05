"""
SpeechBucket: Specification for capturing speech input within a CommandDoc workflow.

Each bucket captures a portion of user speech (e.g., title, body, tags).
Parameters are specified as RELATIVE multipliers of global base values.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from palaver.config.recorder_config import RecorderConfig


@dataclass
class SpeechBucket:
    """
    Specification for a speech input bucket within a CommandDoc workflow.

    Each bucket captures a portion of user speech (e.g., title, body, tags).
    Parameters are specified as RELATIVE multipliers of global base values.

    Example:
        # With global base_segment_size = 5.0 seconds
        SpeechBucket(
            name="title",
            display_name="Note Title",
            segment_size=0.4,  # 0.4 × 5.0 = 2.0 seconds (quick chunks)
            start_window=3.0,  # 3.0 × 2.0 = 6.0 seconds (generous wait)
            termination_silence=0.5  # 0.5 × 0.8 = 0.4 seconds (quick end)
        )
    """

    # Identity
    name: str              # Programmer-facing key (e.g., "note_title")
    display_name: str      # User-facing label (e.g., "Note Title")

    # Timing parameters (relative multipliers)
    segment_size: float = 1.0           # Chunking interval multiplier
    start_window: float = 1.0           # Timeout for first speech multiplier
    termination_silence: float = 1.0    # Silence duration multiplier

    def get_absolute_params(self, config: 'RecorderConfig') -> dict:
        """
        Convert relative parameters to absolute values.

        Args:
            config: RecorderConfig instance with base values

        Returns:
            Dictionary with absolute values in seconds:
            {
                'segment_size': 5.0,       # seconds
                'start_window': 2.0,       # seconds
                'termination_silence': 0.8 # seconds
            }
        """
        return {
            'segment_size': self.segment_size * config.base_segment_size,
            'start_window': self.start_window * config.base_start_window,
            'termination_silence': self.termination_silence * config.base_termination_silence,
        }

    def __post_init__(self):
        """Validate bucket configuration."""
        if not self.name or not self.name.replace('_', '').replace('-', '').isalnum():
            raise ValueError(f"Invalid bucket name: {self.name} (must be alphanumeric with _ or -)")

        if not self.display_name:
            raise ValueError("display_name cannot be empty")

        if self.segment_size <= 0:
            raise ValueError(f"segment_size must be positive, got {self.segment_size}")

        if self.start_window <= 0:
            raise ValueError(f"start_window must be positive, got {self.start_window}")

        if self.termination_silence <= 0:
            raise ValueError(f"termination_silence must be positive, got {self.termination_silence}")
