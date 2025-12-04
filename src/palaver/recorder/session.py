"""
palaver/recorder/session.py
Session management for recording sessions

Handles:
- Session directory creation
- Manifest generation and writing
- Metadata tracking
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any, Optional


class Session:
    """
    Manages a recording session.

    Each session has:
    - Timestamped directory (YYYYMMDD_HHMMSS)
    - manifest.json with metadata
    - Transcript files (managed by TextProcessor)
    - Segment WAV files (managed by recorder)
    """

    def __init__(self, base_dir: Path = Path("sessions")):
        """
        Initialize Session.

        Args:
            base_dir: Base directory for all sessions (default: "sessions/")
        """
        self.base_dir = base_dir
        self.session_dir: Optional[Path] = None
        self.start_time: Optional[datetime] = None
        self.metadata: Dict[str, Any] = {}

    def create(self) -> Path:
        """
        Create new session directory.

        Returns:
            Path to session directory
        """
        # Ensure base directory exists
        self.base_dir.mkdir(exist_ok=True)

        # Create timestamped directory
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.session_dir = self.base_dir / timestamp
        self.session_dir.mkdir(exist_ok=True)

        # Record start time
        self.start_time = datetime.now(timezone.utc)

        print(f"\nSession → {self.session_dir}")
        return self.session_dir

    def add_metadata(self, key: str, value: Any):
        """
        Add metadata to session.

        Args:
            key: Metadata key
            value: Metadata value (must be JSON-serializable)
        """
        self.metadata[key] = value

    def write_manifest(self,
                       segments: List[Dict[str, Any]],
                       total_segments: int,
                       samplerate: int = 48000):
        """
        Write manifest.json for this session.

        Args:
            segments: List of segment info dicts with keys:
                     - index: Segment index
                     - file: Filename (e.g., "seg_0000.wav")
                     - duration_sec: Duration in seconds
            total_segments: Total number of segments
            samplerate: Audio sample rate
        """
        if not self.session_dir:
            raise RuntimeError("Session not created. Call create() first.")

        if not self.start_time:
            raise RuntimeError("Session start time not set.")

        manifest = {
            "session_start_utc": self.start_time.isoformat(),
            "samplerate": samplerate,
            "total_segments": total_segments,
            **self.metadata,  # Include all added metadata
            "segments": segments
        }

        manifest_path = self.session_dir / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2))

    def get_path(self) -> Path:
        """
        Get the session directory path.

        Returns:
            Path to session directory

        Raises:
            RuntimeError: If session not created yet
        """
        if not self.session_dir:
            raise RuntimeError("Session not created. Call create() first.")

        return self.session_dir
