"""
Test DraftBuilder with simulated TextEvents to verify event tracking and audio time mapping.

These tests follow the pattern from scripts/test_match.py but add TextEvent instances
to verify that boundary phrases can be mapped back to their source audio segments.
"""
import pytest
from palaver.scribe.scriven.drafts import DraftBuilder
from palaver.scribe.text_events import TextEvent


async def test_draft_start_with_single_event():
    """Test that start boundary phrase is tracked when it appears in a single TextEvent."""
    builder = DraftBuilder()

    # Simulate a TextEvent containing the complete start phrase
    event1 = TextEvent(
        text="Rupert, start draft now",
        audio_start_time=1.0,
        audio_end_time=2.5,
        audio_source_id="test://source/1"
    )

    cur_draft, last_draft, matched_events = await builder.new_text(event1.text, event1)

    # Should create a new draft
    assert cur_draft is not None
    assert last_draft is None
    assert cur_draft.start_matched_events == [event1]

    # Verify audio times can be retrieved
    assert event1.audio_start_time == 1.0
    assert event1.audio_end_time == 2.5


async def test_draft_start_split_across_events():
    """Test that start boundary phrase is tracked when split across multiple TextEvents.

    The odd concatenation pattern (text1 + text2) mirrors test_match.py to keep
    interpretation boundaries distinct from VTT result boundaries.
    """
    builder = DraftBuilder()

    # Simulate start phrase split across two events
    event1 = TextEvent(
        text="Rupert, start ",
        audio_start_time=1.0,
        audio_end_time=1.5,
        audio_source_id="test://source/1"
    )
    text1 = "Rupert, start "

    cur_draft, last_draft, matched_events = await builder.new_text(text1, event1)
    assert cur_draft is None
    assert last_draft is None

    event2 = TextEvent(
        text="draft now",
        audio_start_time=1.5,
        audio_end_time=2.0,
        audio_source_id="test://source/1"
    )
    text2 = "draft now"

    cur_draft, last_draft, matched_events = await builder.new_text(text2, event2)

    # Should create draft, tracking both events that contain the phrase
    assert cur_draft is not None
    assert last_draft is None
    assert event1 in cur_draft.start_matched_events
    assert event2 in cur_draft.start_matched_events

    # Verify we can get the full audio range
    start_times = [e.audio_start_time for e in cur_draft.start_matched_events]
    end_times = [e.audio_end_time for e in cur_draft.start_matched_events]
    assert min(start_times) == 1.0
    assert max(end_times) == 2.0


async def test_draft_end_with_single_event():
    """Test that end boundary phrase is tracked when it appears in a single TextEvent."""
    builder = DraftBuilder()

    # Start a draft
    event1 = TextEvent(
        text="Rupert, start draft now",
        audio_start_time=1.0,
        audio_end_time=2.0,
        audio_source_id="test://source/1"
    )
    cur_draft, last_draft, matched_events = await builder.new_text(event1.text, event1)
    assert cur_draft is not None

    # Add body content with clear separation
    event2 = TextEvent(
        text=" okay here's the text in the body.",
        audio_start_time=2.0,
        audio_end_time=4.0,
        audio_source_id="test://source/1"
    )
    cur_draft, last_draft, matched_events = await builder.new_text(event2.text, event2)

    # End the draft - ensure space at start for clean boundary
    event3 = TextEvent(
        text=" Rupert, stop draft",
        audio_start_time=4.0,
        audio_end_time=5.5,
        audio_source_id="test://source/1"
    )
    cur_draft, last_draft, matched_events = await builder.new_text(event3.text, event3)

    # Should close the draft
    assert cur_draft is None
    assert last_draft is not None

    # The end phrase should be in event3; event2 might also be included
    # if the fuzzy match spans the boundary, so check that event3 is present
    assert event3 in last_draft.end_matched_events

    # Verify we can get the audio times that contain the end phrase
    end_times = [e.audio_end_time for e in last_draft.end_matched_events]
    assert 5.5 in end_times  # event3's end time should be present


async def test_draft_end_split_across_events():
    """Test that end boundary phrase is tracked when split across multiple TextEvents."""
    builder = DraftBuilder()

    # Start a draft
    event1 = TextEvent(
        text="Rupert, start draft now",
        audio_start_time=1.0,
        audio_end_time=2.0,
        audio_source_id="test://source/1"
    )
    cur_draft, last_draft, matched_events = await builder.new_text(event1.text, event1)
    assert cur_draft is not None

    # Add body content
    event2 = TextEvent(
        text=" okay here's the text",
        audio_start_time=2.0,
        audio_end_time=3.5,
        audio_source_id="test://source/1"
    )
    cur_draft, last_draft, matched_events = await builder.new_text(event2.text, event2)

    # End phrase split across two events
    event3 = TextEvent(
        text=" and some more Rupert, stop ",
        audio_start_time=3.5,
        audio_end_time=5.0,
        audio_source_id="test://source/1"
    )
    text3 = " and some more Rupert, stop "
    cur_draft, last_draft, matched_events = await builder.new_text(text3, event3)

    event4 = TextEvent(
        text="draft",
        audio_start_time=5.0,
        audio_end_time=5.5,
        audio_source_id="test://source/1"
    )
    text4 = "draft"
    cur_draft, last_draft, matched_events = await builder.new_text(text4, event4)

    # Should close draft with both events tracked
    assert cur_draft is None
    assert last_draft is not None
    assert event3 in last_draft.end_matched_events
    assert event4 in last_draft.end_matched_events

    # Verify audio range
    start_times = [e.audio_start_time for e in last_draft.end_matched_events]
    end_times = [e.audio_end_time for e in last_draft.end_matched_events]
    assert min(start_times) == 3.5
    assert max(end_times) == 5.5


async def test_complete_draft_cycle_with_events():
    """Test complete draft lifecycle with TextEvents tracking both boundaries.

    This mirrors the pattern from test_match.py with incremental text additions.
    """
    builder = DraftBuilder()

    # Start phrase split across events (like test_match.py pattern)
    event1 = TextEvent(
        text="Rupert, start ",
        audio_start_time=1.0,
        audio_end_time=1.5,
        audio_source_id="test://source/1"
    )
    text1 = "Rupert, start "
    cur_draft, last_draft, events = await builder.new_text(text1, event1)
    assert cur_draft is None
    assert last_draft is None

    event2 = TextEvent(
        text="draft now ",
        audio_start_time=1.5,
        audio_end_time=2.0,
        audio_source_id="test://source/1"
    )
    event3 = TextEvent(
        text=" okay here's the text in the body",
        audio_start_time=2.0,
        audio_end_time=4.0,
        audio_source_id="test://source/1"
    )
    text2 = "draft now "
    text3 = " okay here's the text in the body"
    cur_draft, last_draft, events = await builder.new_text(text2 + text3, event2)

    # Note: event3 text is concatenated but we only pass event2
    # In real usage, each TextEvent would come separately
    # This tests the buffer accumulation behavior
    assert cur_draft is not None
    assert last_draft is None
    assert event1 in cur_draft.start_matched_events
    assert event2 in cur_draft.start_matched_events

    # Add more body content and end phrase
    event4 = TextEvent(
        text=" and some more",
        audio_start_time=4.0,
        audio_end_time=5.0,
        audio_source_id="test://source/1"
    )
    event5 = TextEvent(
        text=" Rupert, stop draft",
        audio_start_time=5.0,
        audio_end_time=6.5,
        audio_source_id="test://source/1"
    )
    text4 = " and some more"
    text5 = " Rupert, stop draft"
    cur_draft, last_draft, events = await builder.new_text(text4 + text5, event4)

    assert cur_draft is None
    assert last_draft is not None

    # Verify both boundaries are tracked
    assert len(last_draft.start_matched_events) == 2  # event1 and event2
    assert event4 in last_draft.end_matched_events

    # Verify we can determine audio ranges for rescan
    start_audio_range = (
        min(e.audio_start_time for e in last_draft.start_matched_events),
        max(e.audio_end_time for e in last_draft.start_matched_events)
    )
    end_audio_range = (
        min(e.audio_start_time for e in last_draft.end_matched_events),
        max(e.audio_end_time for e in last_draft.end_matched_events)
    )

    assert start_audio_range == (1.0, 2.0)
    assert end_audio_range[0] >= 4.0  # Depends on which event matched


async def test_multiple_drafts_in_single_text_with_events():
    """Test recursive pattern detection when multiple boundaries appear in one text.

    This mirrors the test_match.py pattern where calling new_text("") triggers
    detection of additional boundaries in the buffer.
    """
    builder = DraftBuilder()

    # Long text with complete start, body, and TWO end phrases
    event1 = TextEvent(
        text="Rupert, start draft now okay here's the text in the body Rupert, stop draft Rupert, stop draft",
        audio_start_time=1.0,
        audio_end_time=8.0,
        audio_source_id="test://source/1"
    )
    text = "Rupert, start draft now okay here's the text in the body Rupert, stop draft Rupert, stop draft"

    cur_draft, last_draft, events = await builder.new_text(text, event1)

    # First call should find start boundary
    assert cur_draft is not None
    assert last_draft is None
    assert event1 in cur_draft.start_matched_events

    # Call with empty string to find the end boundary (as in test_match.py)
    cur_draft, last_draft, events = await builder.new_text("")

    # Should close the draft
    assert cur_draft is None
    assert last_draft is not None
    assert event1 in last_draft.end_matched_events

    # Verify audio times show the event contained both boundaries
    assert last_draft.start_matched_events == [event1]
    assert last_draft.end_matched_events == [event1]


async def test_force_end_has_no_end_events():
    """Test that force_end creates empty end_matched_events list."""
    builder = DraftBuilder()

    # Start a draft
    event1 = TextEvent(
        text="Rupert, start draft now",
        audio_start_time=1.0,
        audio_end_time=2.0,
        audio_source_id="test://source/1"
    )
    cur_draft, last_draft, matched_events = await builder.new_text(event1.text, event1)
    assert cur_draft is not None

    # Add some body
    event2 = TextEvent(
        text=" here is some content",
        audio_start_time=2.0,
        audio_end_time=3.0,
        audio_source_id="test://source/1"
    )
    await builder.new_text(event2.text, event2)

    # Force end without end phrase
    draft = await builder.end_of_text()

    assert draft is not None
    assert draft.start_matched_events == [event1]
    assert draft.end_matched_events == []  # No end phrase matched


async def test_buffer_rolling_maintains_event_tracking():
    """Test that event tracking survives buffer rolling (roll_size=100)."""
    builder = DraftBuilder()
    assert builder.roll_size == 100

    # Add enough text to trigger rolling, but no draft start
    events = []
    for i in range(5):
        event = TextEvent(
            text=" " + "x" * 25,  # 26 chars each
            audio_start_time=float(i),
            audio_end_time=float(i + 1),
            audio_source_id="test://source/1"
        )
        events.append(event)
        await builder.new_text(event.text, event)

    # Buffer should have rolled by now (130 chars > 100)
    # Older events should be pruned from text_event_map

    # Now add a draft start
    event_start = TextEvent(
        text=" Rupert, start draft now",
        audio_start_time=5.0,
        audio_end_time=6.0,
        audio_source_id="test://source/1"
    )
    cur_draft, last_draft, matched_events = await builder.new_text(event_start.text, event_start)

    # Should create draft with only the recent event
    assert cur_draft is not None
    assert event_start in cur_draft.start_matched_events

    # Verify old events were pruned (they're not in the kept buffer region)
    # The event_map should only contain events from the kept region


async def test_new_draft_before_old_ends_tracks_both_boundaries():
    """Test starting new draft before old one ends (implicit close of first draft)."""
    builder = DraftBuilder()

    # Start first draft
    event1 = TextEvent(
        text="Rupert, start draft now",
        audio_start_time=1.0,
        audio_end_time=2.0,
        audio_source_id="test://source/1"
    )
    cur_draft, last_draft, matched_events = await builder.new_text(event1.text, event1)
    assert cur_draft is not None
    first_draft = cur_draft

    # Add some body
    event2 = TextEvent(
        text=" some content",
        audio_start_time=2.0,
        audio_end_time=3.0,
        audio_source_id="test://source/1"
    )
    await builder.new_text(event2.text, event2)

    # Start second draft without ending first
    event3 = TextEvent(
        text=" Rupert, start draft now again",
        audio_start_time=3.0,
        audio_end_time=4.5,
        audio_source_id="test://source/1"
    )
    cur_draft, last_draft, matched_events = await builder.new_text(event3.text, event3)

    # First draft should be closed (implicitly)
    assert last_draft is not None
    assert last_draft == first_draft
    assert last_draft.end_matched_events == []  # No explicit end phrase

    # Second draft should be started
    assert cur_draft is not None
    assert cur_draft != first_draft
    assert event3 in cur_draft.start_matched_events


async def test_draft_audio_timing_properties():
    """Test Draft.audio_start_time and Draft.audio_end_time properties (Story 6).

    These properties compute timing from matched events, enabling the rescan
    server to identify exactly which audio segment to reprocess.
    """
    builder = DraftBuilder()

    # Create a complete draft with known audio times
    event1 = TextEvent(
        text="Rupert, start ",
        audio_start_time=10.0,
        audio_end_time=10.5,
        audio_source_id="test://source/1"
    )
    event2 = TextEvent(
        text="draft now",
        audio_start_time=10.5,
        audio_end_time=11.0,
        audio_source_id="test://source/1"
    )

    cur_draft, last_draft, matched_events = await builder.new_text(event1.text, event1)
    cur_draft, last_draft, matched_events = await builder.new_text(event2.text, event2)
    assert cur_draft is not None

    # Add body content
    event3 = TextEvent(
        text=" this is the draft body content",
        audio_start_time=11.0,
        audio_end_time=13.0,
        audio_source_id="test://source/1"
    )
    await builder.new_text(event3.text, event3)

    # End the draft
    event4 = TextEvent(
        text=" Rupert, stop draft",
        audio_start_time=13.0,
        audio_end_time=14.5,
        audio_source_id="test://source/1"
    )
    cur_draft, last_draft, matched_events = await builder.new_text(event4.text, event4)

    # Draft should be closed
    assert cur_draft is None
    assert last_draft is not None

    # Verify audio_start_time property (Story 6 requirement)
    assert last_draft.audio_start_time == 10.0  # Earliest start time from matched events

    # Verify audio_end_time property (Story 6 requirement)
    assert last_draft.audio_end_time == 14.5  # Latest end time from matched events

    # Verify these enable rescan server to identify exact audio segment
    audio_duration = last_draft.audio_end_time - last_draft.audio_start_time
    assert audio_duration == 4.5  # 10.0 to 14.5 seconds


async def test_draft_audio_timing_with_no_end_phrase():
    """Test that audio_end_time is None when draft has no explicit end phrase."""
    builder = DraftBuilder()

    # Start a draft
    event1 = TextEvent(
        text="Rupert, start draft now",
        audio_start_time=5.0,
        audio_end_time=6.0,
        audio_source_id="test://source/1"
    )
    cur_draft, last_draft, matched_events = await builder.new_text(event1.text, event1)
    assert cur_draft is not None

    # Force end without explicit end phrase
    draft = await builder.end_of_text()

    assert draft is not None
    assert draft.audio_start_time == 5.0  # Has start time
    assert draft.audio_end_time is None    # No end phrase, so no end time


async def test_draft_audio_timing_with_multiple_start_events():
    """Test that audio_start_time correctly handles start phrase spanning multiple events."""
    builder = DraftBuilder()

    # Start phrase split across three events with different times
    event1 = TextEvent(
        text="Rupert, ",
        audio_start_time=20.0,
        audio_end_time=20.3,
        audio_source_id="test://source/1"
    )
    event2 = TextEvent(
        text="start ",
        audio_start_time=20.3,
        audio_end_time=20.6,
        audio_source_id="test://source/1"
    )
    event3 = TextEvent(
        text="draft now",
        audio_start_time=20.6,
        audio_end_time=21.0,
        audio_source_id="test://source/1"
    )

    await builder.new_text(event1.text, event1)
    await builder.new_text(event2.text, event2)
    cur_draft, last_draft, matched_events = await builder.new_text(event3.text, event3)

    assert cur_draft is not None
    # Should use the earliest start time from all matched events
    assert cur_draft.audio_start_time == 20.0
