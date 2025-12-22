#!/usr/bin/env python
import asyncio
import logging
import json
import string
from typing import Optional, List
from dataclasses import dataclass, field
from pprint import pprint
from pathlib import Path
from enum import Enum
import time
import uuid

from rapidfuzz import fuzz
from palaver.scribe.text_events import TextEvent
from loggers import setup_logging
logger = logging.getLogger('DraftMaker')

@dataclass
class MatchPattern:
    pattern: str
    required_words: Optional[list[str]] = field(default_factory=list[str])
    
@dataclass
class MatchResult:
    match_pattern: MatchPattern
    match_start: int
    match_end: int
    matched_text: str
    score: float

@dataclass
class TextMark:
    start: int
    end: int
    text: str
    
@dataclass
class Section:
    draft: 'Draft'
    start_text: TextMark
    end_text: Optional[TextMark] = None
    full_text: Optional[str] = field(default_factory=str)
    timestamp: float = field(default_factory=time.time)
    section_id: str = field(default_factory=lambda: str(uuid.uuid4()))

@dataclass
class Draft:
    start_text: TextMark
    end_text: Optional[TextMark] = None
    sections: Optional[list[Section]] = field(default_factory=list[Section])
    full_text: Optional[str] = field(default_factory=str)
    text_buffer: Optional[str] = field(default_factory=str)
    timestamp: float = field(default_factory=time.time)
    draft_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    @property
    def trimmed_text(self):
        start = self.full_text.find(self.start_text.text) + len(self.start_text.text)
        if self.end_text.text == '':
            end = len(self.full_text)
        else:
            end = self.full_text.find(self.end_text.text)
        return self.full_text[start:end]

PUNCTUATION_REMOVER = str.maketrans("", "", string.punctuation)

def clean_text_with_mapping(text: str) -> tuple[str, list[int]]:
    """
    Clean text (lowercase + strip punctuation) and return cleaned string + list of original indices for each kept char.
    """
    cleaned = []
    mapping = []  # Original positions for each char in cleaned
    for i, char in enumerate(text):
        lower_char = char.lower()
        if lower_char not in string.punctuation:  # Keep non-punct (after lower)
            cleaned.append(lower_char)
            mapping.append(i)  # Record original index
    return "".join(cleaned), mapping

FIX_STOP_WORDS=True
def match_first(patterns: list[MatchPattern], text: str, ratio_min: float = 80.0) -> List[MatchResult]:
    """
    Sliding window fuzzy matcher to first match for any pattern above threshold.
    """
    cleaned_text, text_mapping = clean_text_with_mapping(text)
    results = []
    for pattern_spec in patterns:
        cleaned_pattern, _ = clean_text_with_mapping(pattern_spec.pattern)
        
        # Sliding window: Check substrings roughly pattern length + fuzz room
        pat_len = len(cleaned_pattern)
        best_results = []  # Collect all above threshold for this pattern
        
        for start in range(len(cleaned_text) - pat_len + 1):
            # Window slightly larger than pattern for fuzz
            end = start + pat_len + 10  # Padding for inserts/deletes
            if end > len(cleaned_text):
                end = len(cleaned_text)
            sub = cleaned_text[start:end]

            # if there are any required words, make sure they are present first
            if len(pattern_spec.required_words) > 0:
                any_failed = False
                for word in pattern_spec.required_words:
                    best_score = 0
                    for subw in sub.split():
                        score = fuzz.ratio(word, subw)
                        best_score = max(score, best_score)
                        if score >= 90:
                            break
                    if best_score < 90:
                        # this generates a ton of messages because of the whole
                        # window thing
                        #logger.debug("%s required word '%s' low score %f in %s",
                        #            pattern_spec.pattern, word, best_score, sub)
                        any_failed = True
                        break
                if any_failed:
                    continue
            
            alignment = fuzz.partial_ratio_alignment(cleaned_pattern, sub)
            if alignment.score >= ratio_min:
                # now check for required words if any
                # Alignment indices are relative to sub; adjust to full cleaned_text
                abs_start = start + alignment.dest_start
                abs_end = start + alignment.dest_end
                orig_start = text_mapping[abs_start] if abs_start < len(text_mapping) else len(text)
                orig_end = text_mapping[abs_end - 1] + 1 if abs_end > 0 else len(text)
                matched_string = text[orig_start:orig_end]
                result = MatchResult(
                    match_pattern=pattern_spec,
                    match_start=orig_start,
                    match_end=orig_end,
                    matched_text=matched_string,
                    score=alignment.score
                )
                best_results.append(result)
        
        if best_results:
            best_results.sort(key=lambda r: r.match_start)
            results.extend(best_results)
    
    if results:
        # find the one that starts first and is longest

        results.sort(key=lambda r: r.match_start)
        start_pos = results[0].match_start
        max_len_item = None
        for item in results:
            if item.match_start > start_pos:
                continue
            if max_len_item is None:
                max_len_item = item
            elif item.match_end > max_len_item.match_end:
                max_len_item = item
            
            # The matched string can be off, as the alignment dodad does not
            # work like you'd think, so we need to do some addition checks and maybe
            # adjustments. The code here may fix things that happen when your input
            # string includes an "a" or a "the", or a "uh"
            if FIX_STOP_WORDS:
                msplit = max_len_item.matched_text.lower().split()
                first, last = msplit[0], msplit[-1]
                pattern = max_len_item.match_pattern.pattern 
                psplit = pattern.lower().split()
                pfirst, plast = psplit[0], psplit[-1]
                if first not in psplit or last not in psplit:
                    new_start = orig_start
                    new_end = orig_end
                    if first not in pattern.lower():
                        if first in pfirst:
                            pos = orig_start - len(pfirst)
                            try:
                                index = text[pos:].find(pfirst)
                                new_start = pos + index
                            except ValueError:
                                pass
                    if last not in pattern.lower():
                        if last in plast:
                            new_sub = text[new_start:]
                            try:
                                index = new_sub.find(plast)
                                new_end = index + len(plast)
                            except ValueError:
                                pass
                    max_len_item.matched_text = text[new_start:new_end]
                    max_len_item.match_start = new_start
                    max_len_item.match_end = new_end
        logger.debug("Returning result %f for pattern '%s(%s)' found '%s' %d to %d",
                     max_len_item.score,
                     max_len_item.match_pattern.pattern,
                     max_len_item.match_pattern.required_words,
                     max_len_item.matched_text,
                     max_len_item.match_start,
                     max_len_item.match_end)
        return max_len_item
    return None

class DraftBuilder:

    def __init__(self):
        self.working_text = ""
        self.draft_start_patterns = []
        self.draft_end_patterns = []
        self.current_draft = None
        self.section_start_patterns = []
        self.section_end_patterns = []
        self.current_section = None
        self.roll_size = 100

    def add_draft_start_pattern(self, pattern: MatchPattern):
        self.draft_start_patterns.append(pattern)

    def add_draft_end_pattern(self, pattern: MatchPattern):
        self.draft_end_patterns.append(pattern)

    async def new_text(self, text):
        """
        NOTE! Removes any sequences of multiple spaces collapsing them
        to one space each. This is probable not something that will happen
        when the text is coming from whispercpp transcription, and it
        messes up the process of identifying the pattern matching source
        string, because rapid fuzz ignores them and returns bogus indices
        as a result.
        """
        if len(self.working_text) > 0 and not self.working_text[-1].isspace():
            self.working_text += " "
        self.working_text += " ".join(text.split())
        logger.debug("Adding %d bytes to working text, now '%s'",
                     len(text), self.working_text) 
        patterns = self.draft_start_patterns + self.draft_end_patterns 
        patterns += self.section_start_patterns + self.section_end_patterns 

        last_draft = self.current_draft
        matched = match_first(patterns, self.working_text)
        if not matched:
            logger.debug("No match in '%s'", self.working_text)
            if len(self.working_text) > self.roll_size:
                logger.debug("Rolled working text back to %d bytes", len(self.working_text))
                self.working_text = self.working_text[-self.roll_size:]
            if self.current_draft:
                self.current_draft.text_buffer += f"{text} "
            return self.current_draft, last_draft if last_draft != self.current_draft else None
        if matched.match_pattern in self.draft_start_patterns:
            if not self.current_draft:
                text_mark = TextMark(0, matched.match_end-matched.match_start,  matched.matched_text)
                text_mark = TextMark(matched.match_start, matched.match_end, matched.matched_text)
                self.current_draft = Draft(start_text=text_mark)
                self.working_text = ""
                logger.debug("New draft starting (none current) on pattern %s, truncating working to %d",
                             matched.match_pattern.pattern, len(self.working_text))
            else:
                logger.debug("New draft starting (one already current) on pattern %s", matched.match_pattern)
                end_of_new = matched.match_end-matched.match_start
                end_mark = TextMark(0, end_of_new,  matched.matched_text)
                self.current_draft.end_text = end_mark
                self.current_draft.full_text = self.current_draft.text_buffer
                self.current_draft.text_buffer = None
                self.current_draft = Draft(start_text=text_mark)
                self.working_text = ""
                logger.debug("closed doc, working_text now %d long", len(self.working_text))
        elif matched.match_pattern in self.draft_end_patterns:
            if not self.current_draft:
                self.working_text = ""
                logger.warning("Got end of draft signal when no current draft! truncating working to %d",
                               len(self.working_text))
            else:
                logger.debug("Ending current draft on pattern %s", matched.match_pattern)
                end_mark = TextMark(matched.match_start, matched.match_end, matched.matched_text)
                self.current_draft.end_text = end_mark
                self.current_draft.full_text = self.current_draft.text_buffer
                self.current_draft.text_buffer = None
                self.working_text = ""
                logger.debug("closed doc, working_text now %d long", len(self.working_text))
                self.current_draft = None
        return self.current_draft, last_draft if last_draft != self.current_draft else None

    async def end_of_text(self):
        if self.current_draft:
            end = len(self.working_text)
            end_mark = TextMark(end, end, "")
            self.current_draft.end_text = end_mark
            self.working_text = ""
            draft = self.current_draft
            self.current_draft = None
            return draft
        return None
            
    
async def t_bounds():
    # Simulate streaming chunks
    chunks = [
        "Rupert wake up, new document Document content, line 1. ",
        "Rupert stop, close document Rupert wake up, new document ",
        "Second Document Rupert listen, end document "
    ]
    
    builder = DraftBuilder()
    builder.add_draft_start_pattern(MatchPattern("rupert wake up new document"))
    builder.add_draft_end_pattern(MatchPattern("rupert stop close document"))
    builder.add_draft_end_pattern(MatchPattern("rupert listen end document"))
    
    current_draft = None
    drafts = {}
    for chunk in chunks:
        print(f"\nProcessing chunk: '{chunk}'")
        current_draft,last_draft = await builder.new_text(chunk)
        print(f"current={current_draft}\nlast_draft={last_draft}")
        if last_draft:
            drafts[last_draft.draft_id] = last_draft


    chunks2 = [
        " Rupert wake up, the new document",
        " Begining of third document. ",
        " Gonna add several lines to this one. ",
        " Additional lines.",
        " Rupert stop, close document ",
        " Here's some stuff that is not in a document ",
        " Rupert wake up, new document ",
        " Not gonna close this one properly ",
        " Rupert wake up, new document ",
        " This one started while another was open ",
        " Rupert stop, close document ",
    ]
            
    for chunk in chunks2:
        print(f"\nProcessing chunk: '{chunk}'")
        current_draft,last_draft = await builder.new_text(chunk)
        print(f"current={current_draft}\nlast_draft={last_draft}")
        if last_draft:
            drafts[last_draft.draft_id] = last_draft

            
    draft = await builder.end_of_text()
    if draft:
        drafts[draft.draft_id] = draft
    for draft in drafts.values():
        print('------------------')
        pprint(draft)
        print(draft.trimmed_text)
        
if __name__=="__main__":
    setup_logging(default_level="WARNING",
                  info_loggers=[],
                  debug_loggers=[logger.name,],
                  more_loggers=[logger,])
    asyncio.run(t_bounds())
