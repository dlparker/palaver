import asyncio
from rapidfuzz import fuzz
from pprint import pprint
from palaver.scribe.scriven.drafts import DraftBuilder, logger, MatchResult, MatchPattern
from palaver.scribe.scriven.drafts import default_draft_start_patterns, default_draft_end_patterns
from palaver.scribe.scriven.drafts import clean_text_with_mapping, match_first
from loggers import setup_logging

async def real():

    builder = DraftBuilder()
    
    text1 = "Rupert, start "
    cur_draft, last_draft, events = await builder.new_text(text1)
    assert cur_draft is None
    assert last_draft is None
    text2 = "draft now "
    text3 = " okay here's the text in the body"
    cur_draft, last_draft, events = await builder.new_text(text2 + text3)
    print(text2 + text3)
    assert cur_draft is not None
    assert last_draft is None
    pprint(cur_draft)
    pprint(last_draft)
    text4 = " and some more"
    text5 = " Rupert, stop draft Rupert, stop draft"
    cur_draft, last_draft, events = await builder.new_text(text4 + text5)
    print(text4 + text5)
    pprint(cur_draft)
    pprint(last_draft)
    cur_draft, last_draft, events = await builder.new_text("")
    pprint(cur_draft)
    pprint(last_draft)


    text = "Rupert, start draft now okay here's the text in the body Rupert, stop draft Rupert, stop draft"

    cur_draft, last_draft, events = await builder.new_text(text)
    print(text)
    assert cur_draft is not None
    assert last_draft is None
    pprint(cur_draft)
    pprint(last_draft)
    cur_draft, last_draft, events = await builder.new_text("")
    
    assert cur_draft is  None
    assert last_draft is not None
    pprint(cur_draft)
    pprint(last_draft)
    
        
setup_logging(default_level="WARNING",
              info_loggers=[],
              debug_loggers=['DraftMaker'],
              more_loggers=[])


asyncio.run(real())
