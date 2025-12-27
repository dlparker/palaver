import asyncio
from rapidfuzz import fuzz
from pprint import pprint
from palaver.scribe.scriven.drafts import DraftBuilder, logger, MatchResult, MatchPattern
from palaver.scribe.scriven.drafts import default_draft_start_patterns, default_draft_end_patterns
from palaver.scribe.scriven.drafts import clean_text_with_mapping, match_first
from loggers import setup_logging

async def real():

    builder = DraftBuilder()
    
    text2 = "Rupert, start a draft now "
    text3 = " okay here's the text in the body"
    cur_draft, last_draft = await builder.new_text(text2 + text3)
    print(text2 + text3)
    pprint(cur_draft)
    pprint(last_draft)
    text4 = " and some more"
    text5 = " Rupert, stop draft Rupert, stop draft"
    cur_draft, last_draft = await builder.new_text(text4 + text5)
    print(text4 + text5)
    pprint(cur_draft)
    pprint(last_draft)
    cur_draft, last_draft = await builder.new_text("")
    pprint(cur_draft)
    pprint(last_draft)


    text = "Rupert, start draft now okay here's the text in the body Rupert, stop draft Rupert, stop draft"

    cur_draft, last_draft = await builder.new_text(text)
    print(text)
    pprint(cur_draft)
    pprint(last_draft)
    #await builder.end_of_text()
    pprint(cur_draft)
    pprint(last_draft)
    
async def mine():
    text = "Rupert, start a draft now okay here's the text in the body Rupert, stop draft Rupert, stop draft"
    patterns = default_draft_start_patterns + default_draft_end_patterns
    patterns = default_draft_end_patterns + default_draft_start_patterns 
    res = match_first(patterns, text)
    new_text = text[res.match_end:]
    skip_total = res.match_end
    print(text)
    print(res)
    print(text[res.match_start:res.match_end])
    print(skip_total)
    print("*"*80)
    res = match_first(patterns, new_text)
    print(new_text)
    print(f"is {len(new_text)} long")
    print(res)
    print(new_text[res.match_start:res.match_end])
    body = new_text[:res.match_start]
    print(body)
    print(skip_total)
    new_text_2 = new_text[res.match_end:]
    skip_total += res.match_end
    print("*"*80)
    print(new_text_2)
    print(skip_total)
        
    
setup_logging(default_level="WARNING",
              info_loggers=[],
              debug_loggers=['DraftMaker'],
              more_loggers=[])


#asyncio.run(mine())
asyncio.run(real())
