import asyncio
from rapidfuzz import fuzz
from pprint import pprint
from palaver.utils.top_error import TopErrorHandler, TopLevelCallback
from palaver.scribe.scriven.drafts import DraftBuilder, logger, MatchResult, MatchPattern
from palaver.scribe.scriven.drafts import default_draft_start_patterns, default_draft_end_patterns
from palaver.scribe.scriven.drafts import clean_text_with_mapping, match_first
from palaver.scribe.text_events import TextEvent
from loggers import setup_logging

async def real1():

    builder = DraftBuilder()

    
    tstart = 1.0
    tend = tstart + 1
    text1 = TextEvent("Freddy, take", audio_start_time=tstart, audio_end_time=tend)
    drafts = await builder.new_text_event(text1)
    assert not drafts 
    tstart += 0.5
    tend = tstart + 1
    text2 = TextEvent(" this down", audio_start_time=tstart, audio_end_time=tend)
    drafts = await builder.new_text_event(text2)
    assert len(drafts) == 1
    
    tstart += 1.5
    tend = tstart + 1
    text3 = TextEvent(" okay here's the text in the body", audio_start_time=tstart, audio_end_time=tend)
    drafts = await builder.new_text_event(text3)
    assert len(drafts) == 0
    tstart += 1.0
    tend = tstart + 1
    text4 = TextEvent(" and some more", audio_start_time=tstart, audio_end_time=tend)
    drafts = await builder.new_text_event(text4)
    tstart += 2.0
    tend = tstart + 1
    assert len(drafts) == 0
    text5 = TextEvent("Freddy break break", audio_start_time=tstart, audio_end_time=tend)
    drafts = await builder.new_text_event(text5)
    assert len(drafts) == 1
    draft = drafts[0]
    pprint(draft)
    

async def real2():

    builder = DraftBuilder()

    tstart = 10.0
    tend = tstart + 6
    double_1 = """Freddy take this down! Here is body one. Freddy break break! Freddy Take this down. This is body two. Freddy break break."""
    text5b = TextEvent(double_1, audio_start_time=tstart, audio_end_time=tend)
    drafts = await builder.new_text_event(text5b)
    assert len(drafts) == 2
    draft = drafts[0]
    pprint(draft)
    draft = drafts[1]
    pprint(draft)

async def real4():
    builder = DraftBuilder()

    long_text_1 = """Freddy new draft. This is a long text to make sure that it works
    Four score and seven years ago our fathers brought forth on this continent, a new nation, conceived in Liberty, and dedicated to the proposition that all men are created equal.

Now we are engaged in a great civil war, testing whether that nation, or any nation so conceived and so dedicated, can long endure. We are met on a great battle-field of that war. We have come to dedicate a portion of that field, as a final resting place for those who here gave their lives that that nation might live. It is altogether fitting and proper that we should do this. 


But, in a larger sense, we can not dedicate—we can not consecrate—we can not hallow—this ground. The brave men, living and dead, who struggled here, have consecrated it, far above our poor power to add or detract. The world will little note, nor long remember what we say here, but it can never forget what they did here. It is for us the living, rather, to be dedicated here to the unfinished work which they who fought here have thus far so nobly advanced. It is rather for us to be here dedicated to the great task remaining before us—that from these honored dead we take increased devotion to that cause for which they gave the last full measure of devotion—that we here highly resolve that these dead shall not have died in vain—that this nation, under God, shall have a new birth of freedom—and that government of the people, by the people, for the people, shall not perish from the earth.

Freddy break break
    """
    tstart = 20.0
    tend = tstart + 1
    text6 = TextEvent(long_text_1, audio_start_time=tstart, audio_end_time=tend)
    drafts = await builder.new_text_event(text6)
    assert len(drafts) == 1
    draft = drafts[0]
    pprint(draft)

async def real5():

    builder = DraftBuilder()
    
    long_text_2 = """Freddy new draft. This is a long text to make sure that it works
    Four score and seven years ago our fathers brought forth on this continent, a new nation, conceived in Liberty, and dedicated to the proposition that all men are created equal.

Now we are engaged in a great civil war, testing whether that nation, or any nation so conceived and so dedicated, can long endure. We are met on a great battle-field of that war. We have come to dedicate a portion of that field, as a final resting place for those who here gave their lives that that nation might live. It is altogether fitting and proper that we should do this. Freddy break break.

Freddy Take this down.

But, in a larger sense, we can not dedicate—we can not consecrate—we can not hallow—this ground. The brave men, living and dead, who struggled here, have consecrated it, far above our poor power to add or detract. The world will little note, nor long remember what we say here, but it can never forget what they did here. It is for us the living, rather, to be dedicated here to the unfinished work which they who fought here have thus far so nobly advanced. It is rather for us to be here dedicated to the great task remaining before us—that from these honored dead we take increased devotion to that cause for which they gave the last full measure of devotion—that we here highly resolve that these dead shall not have died in vain—that this nation, under God, shall have a new birth of freedom—and that government of the people, by the people, for the people, shall not perish from the earth. Freddy break break
    """
    builder = DraftBuilder()
    tstart = 30.0
    tend = tstart + 1
    text7 = TextEvent(long_text_2, audio_start_time=tstart, audio_end_time=tend)
    drafts = await builder.new_text_event(text7)
    assert len(drafts) == 2
    draft = drafts[0]
    pprint(draft)
    draft = drafts[1]
    pprint(draft)
    return

async def real3():

    builder = DraftBuilder()

    
    tstart = 1.0
    tend = tstart + 1
    text = """Freddy take this down and here is some more stuff freddy break break break. Freddy take this down. Oh good. Freddy Take this down foo bar"""
    text1 = TextEvent(text, audio_start_time=tstart, audio_end_time=tend)
    drafts = await builder.new_text_event(text1)
    await builder.end_of_text()
    pprint(drafts)
    
        
setup_logging(default_level="WARNING",
              info_loggers=[],
              debug_loggers=['DraftMaker'],
              more_loggers=[])

async def main_loop():
    #await real1()
    #await real3()
    print("*"*80 + "  Real1()")
    await real1()
    print("*"*80 + "  Real2()")
    await real2()
    print("*"*80 + "  Real3()")
    await real3()
    print("*"*80 + "  Real4()")
    await real4()
    print("*"*80 + "  Real5()")
    await real5()
    
async def main():
    background_error_dict = None

    class ErrorCallback(TopLevelCallback):
        async def on_error(self, error_dict: dict):
            nonlocal background_error_dict
            background_error_dict = error_dict

    handler = TopErrorHandler(top_level_callback=ErrorCallback(), logger=logger)
    await handler.async_run(main_loop)

asyncio.run(main())
