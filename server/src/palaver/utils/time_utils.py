"""Time parsing utilities for flexible timestamp format support."""

from datetime import datetime


def parse_timestamp(value: str) -> float:
    """Parse timestamp string as Unix float or ISO datetime.

    Supports two formats with auto-detection:
    1. Unix timestamp as float (e.g., "1704067200.5")
    2. ISO 8601 datetime string (e.g., "2024-01-01T00:00:00" or "2024-01-01T00:00:00Z")

    Args:
        value: Timestamp string to parse

    Returns:
        Unix timestamp as float (seconds since epoch)

    Raises:
        ValueError: If string cannot be parsed as either format

    Examples:
        >>> parse_timestamp("1704067200.5")
        1704067200.5
        >>> parse_timestamp("2024-01-01T00:00:00")
        1704067200.0
        >>> parse_timestamp("2024-01-01T00:00:00Z")
        1704067200.0
    """
    # Try parsing as Unix timestamp (float) first
    try:
        return float(value)
    except ValueError:
        pass

    # Try parsing as ISO datetime string
    try:
        # Replace 'Z' suffix with '+00:00' for UTC timezone
        # datetime.fromisoformat handles most ISO 8601 formats
        iso_value = value.replace('Z', '+00:00')
        dt = datetime.fromisoformat(iso_value)
        return dt.timestamp()
    except (ValueError, AttributeError):
        pass

    # If both parsing attempts failed, raise helpful error
    raise ValueError(
        f"Invalid timestamp format: '{value}'. "
        "Expected Unix timestamp (e.g., '1704067200.5') "
        "or ISO datetime (e.g., '2024-01-01T00:00:00')"
    )
