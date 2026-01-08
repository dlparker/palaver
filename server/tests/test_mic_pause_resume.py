"""Test pause/resume functionality in MicListener."""
import asyncio
import pytest
import logging
from palaver.scribe.audio.mic_listener import MicListener
from palaver_shared.audio_events import AudioChunkEvent
from palaver_shared.top_error import TopErrorHandler, TopLevelCallback

logger = logging.getLogger("test_mic_pause_resume")


class EventCollector:
    """Collects events for testing."""
    def __init__(self):
        self.events = []

    async def on_audio_event(self, event):
        self.events.append(event)


class ErrorCallback(TopLevelCallback):
    """Callback for top-level errors."""
    async def on_error(self, error_dict: dict):
        logger.error(f"Top-level error: {error_dict}")


@pytest.mark.asyncio
async def test_mic_listener_pause_resume():
    """Test that pause/resume controls event emission."""

    async def run_test():
        # Create listener
        listener = MicListener(chunk_duration=0.03)

        # Add event collector
        collector = EventCollector()
        listener.add_audio_event_listener(collector)

        # Verify initial state
        assert not listener.is_streaming()
        assert not listener.is_paused()

        # Start streaming (note: will fail on headless systems without audio device)
        # This test primarily checks the API contracts, not actual audio capture
        try:
            async with listener:
                await listener.start_streaming()

                # Give it a moment to start
                await asyncio.sleep(0.1)

                # Verify streaming state
                assert listener.is_streaming()
                assert not listener.is_paused()

                # Get initial event count
                initial_count = len(collector.events)

                # Pause streaming
                await listener.pause_streaming()

                # Verify paused state
                assert listener.is_streaming()  # Still streaming
                assert listener.is_paused()     # But paused

                # Wait a bit - should not receive new events
                await asyncio.sleep(0.2)
                paused_count = len(collector.events)

                # Should have same or very few new events (from buffer draining)
                # When paused, events should not be emitted
                assert paused_count - initial_count < 3, "Too many events during pause"

                # Resume streaming
                await listener.resume_streaming()

                # Verify resumed state
                assert listener.is_streaming()
                assert not listener.is_paused()

                # Wait and verify we get new events
                await asyncio.sleep(0.2)
                resumed_count = len(collector.events)

                # Should have received events after resume
                assert resumed_count > paused_count, "No events received after resume"

        except Exception as e:
            # On systems without audio devices, we may get an error
            # but we can still verify the state management works
            if "No Default Input Device Available" in str(e) or "sounddevice" in str(e):
                pytest.skip(f"No audio device available: {e}")
            else:
                raise

    # Run with error handler
    handler = TopErrorHandler(top_level_callback=ErrorCallback(), logger=logger)
    await handler.async_run(run_test)


@pytest.mark.asyncio
async def test_pause_without_streaming():
    """Test that pause without start_streaming gives warning."""
    listener = MicListener(chunk_duration=0.03)

    # Should not raise, but log warning
    await listener.pause_streaming()

    # State should remain not streaming
    assert not listener.is_streaming()
    assert not listener.is_paused()


@pytest.mark.asyncio
async def test_resume_without_streaming():
    """Test that resume without start_streaming gives warning."""
    listener = MicListener(chunk_duration=0.03)

    # Should not raise, but log warning
    await listener.resume_streaming()

    # State should remain not streaming
    assert not listener.is_streaming()
    assert not listener.is_paused()


@pytest.mark.asyncio
async def test_double_pause():
    """Test that pausing twice is safe."""

    async def run_test():
        listener = MicListener(chunk_duration=0.03)

        try:
            async with listener:
                await listener.start_streaming()
                await asyncio.sleep(0.05)

                await listener.pause_streaming()
                assert listener.is_paused()

                # Second pause should be safe
                await listener.pause_streaming()
                assert listener.is_paused()

        except Exception as e:
            if "No Default Input Device Available" in str(e) or "sounddevice" in str(e):
                pytest.skip(f"No audio device available: {e}")
            else:
                raise

    handler = TopErrorHandler(top_level_callback=ErrorCallback(), logger=logger)
    await handler.async_run(run_test)


@pytest.mark.asyncio
async def test_double_resume():
    """Test that resuming twice is safe."""

    async def run_test():
        listener = MicListener(chunk_duration=0.03)

        try:
            async with listener:
                await listener.start_streaming()
                await asyncio.sleep(0.05)

                await listener.pause_streaming()
                await listener.resume_streaming()
                assert not listener.is_paused()

                # Second resume should be safe
                await listener.resume_streaming()
                assert not listener.is_paused()

        except Exception as e:
            if "No Default Input Device Available" in str(e) or "sounddevice" in str(e):
                pytest.skip(f"No audio device available: {e}")
            else:
                raise

    handler = TopErrorHandler(top_level_callback=ErrorCallback(), logger=logger)
    await handler.async_run(run_test)
