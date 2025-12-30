"""Example test showing how to use file_sender in pytest.

This demonstrates the pattern for using send_file_to_server() in tests.
"""
import pytest
import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

# Add scripts directory to path for import
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
from file_sender import send_file_to_server


@pytest.mark.asyncio
async def test_send_file_to_server_example():
    """Example: How to use send_file_to_server() in a test.

    This is a skeleton showing the pattern. In a real test, you would:
    1. Start a mock or real vtt_server
    2. Use send_file_to_server() to send a test WAV file
    3. Verify the server received expected events
    """
    # This test is marked as skip because it requires a running server
    pytest.skip("Example test - requires running vtt_server instance")

    # Example usage in a test:
    test_file = Path("tests/audio_samples/test_audio.wav")

    # Would need a running server at this URL
    server_url = "ws://localhost:8000"

    # Send the file (fast mode for testing)
    event_count = await send_file_to_server(
        file_path=test_file,
        server_url=server_url,
        simulate_timing=False  # Fast mode for tests
    )

    # Assert expected behavior
    assert event_count > 0
    # Would verify server received events, etc.


@pytest.mark.asyncio
async def test_file_not_found():
    """Test that send_file_to_server raises FileNotFoundError for missing files."""
    nonexistent_file = Path("/tmp/nonexistent_audio_file.wav")

    with pytest.raises(FileNotFoundError, match="Audio file not found"):
        await send_file_to_server(
            file_path=nonexistent_file,
            server_url="ws://localhost:8000",
            simulate_timing=False
        )
