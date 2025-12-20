#!/usr/bin/env python
import asyncio
import logging
import json
from typing import Optional
from dataclasses import dataclass, field
from pprint import pprint
from pathlib import Path
from rapidfuzz import fuzz
from palaver.scribe.text_events import TextEvent
from loggers import setup_logging

@dataclass
class MatchPattern:
    full_string: str
    required: list
    alts: Optional[dict[str, list]] = field(default_factory=dict[str, list])

@dataclass
class MatchResult:
    match_pattern: MatchPattern
    match_start: int
    match_end: int
    matched_text: str
    modified_pattern: str
    score: float
    
def p_matcher(patterns: list[MatchPattern], text:str, ratio_min=80.0, word_ratio_min=70):
    """
    The intent of this code is to make matching of patterns tunable. I have noted
    that certain voices and certain GPU options produce patterns of transcription
    errors. By recording those typical errors as alternates for actual words I want
    to detect, I can improve matching even where the whispercpp transcription cannot
    be guided by using an initial_prompt. 
    
    """
    results = []
    for mp in patterns:
        # first see if the whole pattern matches at a low level
        alignment = fuzz.partial_ratio_alignment(mp.full_string, text)
        if alignment.score < 60.0:
            continue
        # not much of a match, but lets isolate it and be more rigorous
        focus = text[alignment.dest_start:alignment.dest_end]
        all_required = True
        req_aligns = {}
        for req in mp.required:
            req_align = fuzz.partial_ratio_alignment(req, focus)
            if req_align.score > ratio_min:
                actual = focus[req_align.dest_start:req_align.dest_end]
                full_start = alignment.dest_start + req_align.dest_start
                req_aligns[full_start] = dict(req=req, alt=None, actual=actual, align=req_align)
                continue
            if req in mp.alts:
                have_alt = False
                alts = mp.alts[req]
                for alt in alts:
                    alt_align = fuzz.partial_ratio_alignment(alt, focus)
                    if alt_align.score >= word_ratio_min:
                        actual = focus[alt_align.dest_start:alt_align.dest_end]
                        full_start = alignment.dest_start + alt_align.dest_start
                        req_aligns[full_start] = dict(req=req, alt=alt, actual=actual, align=alt_align)
                        have_alt = True
                        break
                if not have_alt:
                    all_required = False
                    break
            else:
                all_required = False
                break
        if not all_required:
            # couldn't match the required elements, so
            # no match
            continue
        # all the required elements are present but it
        # still might be a poor match, so try
        # the match. Note that we use the constructed string that
        # replaces required elements with their matched alternates
        # if any of that happend

        # Build modified_focus by replacing matched alts (or direct matches) with the required
        replacements = []
        for full_start, data in req_aligns.items():
            start_in_focus = data['align'].dest_start
            end_in_focus = data['align'].dest_end
            if data['alt'] is None:
                replacements.append((start_in_focus, end_in_focus, data['req']))
            else:
                replacements.append((start_in_focus, end_in_focus, data['alt']))

        replacements.sort(key=lambda x: x[0])

        modified_pattern = ''
        prev = 0
        for start, end, rep in replacements:
            modified_pattern += focus[prev:start]
            modified_pattern  += rep
            prev = end
        modified_pattern += focus[prev:]

        score = fuzz.partial_ratio(modified_pattern, focus)
        if score >= ratio_min:
            result = MatchResult(match_pattern=mp,
                                 match_start=alignment.dest_start,
                                 match_end=alignment.dest_end,
                                 matched_text=focus,
                                 modified_pattern=modified_pattern,
                                 score=score)
            results.append(result)
    return results
    
    
async def main():
    mp1 = MatchPattern(full_string="rupert wake up",
                       required=["rupert", "wake"],
                       alts={'rupert': ['rufus', 'rubik'],
                             'wake': ['take',]})

    for text in ["here is one rupert wake up and this is the rest",
                 "hey rupert fake up"
                 "hey rufus fake up",
                 ]:
        res = p_matcher([mp1], text)
        print(f"text: {text}")
        pprint(res)
        print("------------")
    

if __name__=="__main__":
    setup_logging(default_level="WARNING",
                  info_loggers=[],
                  debug_loggers=['Commands',],
                  more_loggers=[])
    asyncio.run(main())


