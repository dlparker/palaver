import pytest
import logging
from palaver.utils.top_error import TopErrorHandler, TopLevelCallback
from palaver.scribe.scriven.drafts import DraftBuilder
from palaver.scribe.text_events import TextEvent
logger = logging.getLogger('test_code')

async def test_one_draft_multiple_texts():

    async def ops():
        builder = DraftBuilder()
        tstart = 1.0
        tend = tstart + 1
        text_event1 = TextEvent("Freddy, take", audio_start_time=tstart, audio_end_time=tend)
        drafts = await builder.new_text_event(text_event1)
        assert not drafts 
        tstart += 0.5
        tend = tstart + 1
        text_event2 = TextEvent(" this down", audio_start_time=tstart, audio_end_time=tend)
        drafts = await builder.new_text_event(text_event2)
        assert len(drafts) == 1

        tstart += 1.5
        tend = tstart + 1
        text_event3 = TextEvent(" okay here's the text in the body", audio_start_time=tstart, audio_end_time=tend)
        drafts = await builder.new_text_event(text_event3)
        assert len(drafts) == 0
        tstart += 1.0
        tend = tstart + 1
        text_event4 = TextEvent(" and some more", audio_start_time=tstart, audio_end_time=tend)
        drafts = await builder.new_text_event(text_event4)
        tstart += 2.0
        tend = tstart + 1
        assert len(drafts) == 0
        text_event5 = TextEvent("Freddy break break", audio_start_time=tstart, audio_end_time=tend)
        drafts = await builder.new_text_event(text_event5)
        assert len(drafts) == 1
        draft = drafts[0]
        assert draft.full_text.strip() == "okay here's the text in the body and some more"
        assert draft.audio_start_time == 1.0
        assert draft.audio_end_time == tend

    background_error_dict = None
    class ErrorCallback(TopLevelCallback):
        async def on_error(self, error_dict: dict):
            nonlocal background_error_dict
            background_error_dict = error_dict
    handler = TopErrorHandler(top_level_callback=ErrorCallback(), logger=logger)
    await handler.async_run(ops)


async def test_two_drafts_one_text():

    async def ops():
        builder = DraftBuilder()
        tstart = 1.0
        tend = tstart + 2
        double_1 = """Freddy take this down! Here is body one. Freddy break break! Freddy Take this down. This is body two. Freddy break break."""
        text_event = TextEvent(double_1, audio_start_time=tstart, audio_end_time=tend)
        drafts = await builder.new_text_event(text_event)
        assert len(drafts) == 2
        draft_0 = drafts[0]
        assert draft_0.full_text.strip() == "Here is body one."
        assert draft_0.audio_start_time == 1.0
        assert draft_0.audio_end_time == tend
        draft_1 = drafts[1]
        assert draft_1.full_text.strip() == "This is body two."
        assert draft_1.audio_start_time == 1.0
        assert draft_1.audio_end_time == tend
        
    background_error_dict = None
    class ErrorCallback(TopLevelCallback):
        async def on_error(self, error_dict: dict):
            nonlocal background_error_dict
            background_error_dict = error_dict
    handler = TopErrorHandler(top_level_callback=ErrorCallback(), logger=logger)
    await handler.async_run(ops)
    

async def test_three_drafts_one_text():

    async def ops():
        builder = DraftBuilder()
        tstart = 1.0
        tend = tstart + 5
        # this one broke an earlier version
        text = """Freddy take this down and here is some more stuff freddy break break break. Freddy take this down. Oh good. Freddy Take this down foo bar"""

        text_event = TextEvent(text, audio_start_time=tstart, audio_end_time=tend)
        drafts = await builder.new_text_event_op(text_event)
        assert len(drafts) == 3
        draft_0 = drafts[0]
        assert draft_0.full_text.strip() == "and here is some more stuff"
        assert draft_0.audio_start_time == tstart
        assert draft_0.audio_end_time == tend
        draft_1 = drafts[1]
        assert draft_1.full_text.strip() == "Oh good."
        assert draft_1.audio_start_time == tstart
        assert draft_1.audio_end_time == tend
        draft_2 = drafts[2]
        assert draft_2.end_text is None
        draft_2b =  await builder.end_of_text()
        assert draft_2b.full_text.strip() == "foo bar"
        assert draft_2b.audio_start_time == tstart
        assert draft_2b.audio_end_time == tend


        
    background_error_dict = None
    class ErrorCallback(TopLevelCallback):
        async def on_error(self, error_dict: dict):
            nonlocal background_error_dict
            background_error_dict = error_dict
    handler = TopErrorHandler(top_level_callback=ErrorCallback(), logger=logger)
    await handler.async_run(ops)

async def test_g_a_two_drafts_one_text():

    async def ops():
        builder = DraftBuilder()
        tstart = 1.0
        tend = tstart + 60
        long_text = """Freddy new draft. This is a long text to make sure that it works
    Four score and seven years ago our fathers brought forth on this continent, a new nation, conceived in Liberty, and dedicated to the proposition that all men are created equal.

Now we are engaged in a great civil war, testing whether that nation, or any nation so conceived and so dedicated, can long endure. We are met on a great battle-field of that war. We have come to dedicate a portion of that field, as a final resting place for those who here gave their lives that that nation might live. It is altogether fitting and proper that we should do this. Freddy break break.

Freddy Take this down.

But, in a larger sense, we can not dedicate—we can not consecrate—we can not hallow—this ground. The brave men, living and dead, who struggled here, have consecrated it, far above our poor power to add or detract. The world will little note, nor long remember what we say here, but it can never forget what they did here. It is for us the living, rather, to be dedicated here to the unfinished work which they who fought here have thus far so nobly advanced. It is rather for us to be here dedicated to the great task remaining before us—that from these honored dead we take increased devotion to that cause for which they gave the last full measure of devotion—that we here highly resolve that these dead shall not have died in vain—that this nation, under God, shall have a new birth of freedom—and that government of the people, by the people, for the people, shall not perish from the earth. Freddy break break
    """

        text_event = TextEvent(long_text, audio_start_time=tstart, audio_end_time=tend)
        drafts = await builder.new_text_event_op(text_event)
        assert len(drafts) == 2
        draft_0 = drafts[0]
        assert draft_0.full_text.strip().endswith("we should do this.")
        assert draft_0.audio_start_time == tstart
        assert draft_0.audio_end_time == tend
        draft_1 = drafts[1]
        assert draft_1.full_text.strip().startswith("But, in a larger sense")
        assert draft_1.audio_start_time == tstart
        assert draft_1.audio_end_time == tend


        
    background_error_dict = None
    class ErrorCallback(TopLevelCallback):
        async def on_error(self, error_dict: dict):
            nonlocal background_error_dict
            background_error_dict = error_dict
    handler = TopErrorHandler(top_level_callback=ErrorCallback(), logger=logger)
    await handler.async_run(ops)


