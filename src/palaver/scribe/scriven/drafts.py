#!/usr/bin/env python
import asyncio
import logging
import json
import string
from typing import Optional, List
from dataclasses import dataclass, field
from pathlib import Path
from enum import Enum
import time
import uuid

from eventemitter import AsyncIOEventEmitter
from rapidfuzz import fuzz
from palaver.utils.top_error import get_error_handler
from palaver.scribe.audio_events import AudioEvent, AudioStopEvent, AudioEventListener
from palaver.scribe.text_events import TextEvent, TextEventListener
from palaver.scribe.draft_events import (DraftEvent,
                                         DraftStartEvent,
                                         DraftEndEvent,
                                         DraftEventListener,
                                         Draft,
                                         Section,
                                         TextMark)
                                         
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

default_draft_start_patterns = []
default_draft_end_patterns = []

for name in ['rupert', 'freddy',]:
    pattern = f"{name} take this down now"
    pat = MatchPattern(pattern, [name,])
    default_draft_start_patterns.append(pat)

    pattern = f"{name} break break "
    pat = MatchPattern(pattern, [name, 'break'])
    default_draft_end_patterns.append(pat)
    pattern = f"{name} great great"
    pat = MatchPattern(pattern, [name,'great'])
    default_draft_end_patterns.append(pat)
    pattern = f"{name} stop stop"
    pat = MatchPattern(pattern, [name, 'stop'])
    default_draft_end_patterns.append(pat)
    pattern = f"{name} stop now"
    pat = MatchPattern(pattern, [name,'stop'])
    default_draft_end_patterns.append(pat)
    for doc_name in ["draft",]:
        for preamble in ['hey ', 'wake up ']:
            for glue in ['', 'a ', 'the ', 'uh ']:
                for start in ['start', 'new']:
                    pattern = f"{preamble}{name} {start} {glue}{doc_name}"
                    pat = MatchPattern(pattern, [name, doc_name, start])
                    default_draft_start_patterns.append(pat)
                    pattern = f"{preamble}{name} {start} {glue}{doc_name} now"
                    pat = MatchPattern(pattern, [name, doc_name, start])
                    default_draft_start_patterns.append(pat)
            for stop in ['stop', 'close', 'end' ]:
                pattern = f"{preamble}{name} {stop} {doc_name}"
                pat = MatchPattern(pattern, [name, doc_name, stop])
                default_draft_end_patterns.append(pat)
                pattern = f"{preamble}{name} {stop} {doc_name} now"
                pat = MatchPattern(pattern, [name, doc_name, stop])
                default_draft_end_patterns.append(pat)

pat = MatchPattern("break break break")
default_draft_end_patterns.append(pat)
pat = MatchPattern("great great great")
default_draft_end_patterns.append(pat)
pat = MatchPattern("stop stop stop")
default_draft_end_patterns.append(pat)
                

    
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

def match_first(patterns: list[MatchPattern], text: str, ratio_min: float = 85.0) -> list[MatchResult]:
    results = []
    cleaned_text, text_mapping = clean_text_with_mapping(text)
    for pattern_spec in patterns:

        # if there are any required words, make sure they are present first
        if len(pattern_spec.required_words) > 0:
            any_failed = False
            for word in pattern_spec.required_words:
                best_score = 0
                for subw in cleaned_text.split():
                    score = fuzz.ratio(word, subw)
                    best_score = max(score, best_score)
                    if score >= 90:
                        break
                if best_score < 90:
                    #logger.debug("%s required word '%s' low score %f in %s",
                    #             pattern_spec.pattern, word, best_score, cleaned_text)
                    any_failed = True
                    break
            if any_failed:
                continue

        # Sliding window: Check substrings roughly pattern length + fuzz room
        cleaned_pattern, _ = clean_text_with_mapping(pattern_spec.pattern)
        pat_len = len(cleaned_pattern)
        best_results = []  # Collect all above threshold for this pattern
        for start in range(len(cleaned_text) - pat_len + 1):
            end = start + pat_len
            if end > len(cleaned_text):
                end = len(cleaned_text)
            sub = cleaned_text[start:end]
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
            best_results.sort(key=lambda r: r.score)
            top_score = 0
            left_most = 9999999
            best_choice = None
            for res in reversed(best_results):
                top_score = max(res.score, top_score)
                if res.score == top_score:
                    left_most = min(left_most, res.match_start)
                    if res.match_start == left_most:
                        best_choice = res
                else:
                    break    
            results.append(best_choice)
    if results:
        results.sort(key=lambda r: r.match_end)
        top_score = 0
        left_most = 11111111
        longest = 0
        for res in results:
            top_score = max(res.score, top_score)
            if res.score == top_score:
                left_most = min(left_most, res.match_end)
                longest = max(longest, res.match_end-res.match_start)
                if res.match_end == left_most:
                    best_choice = res
                else:
                    # sometimes "foo bar be" gets same score as "foo bar bee", but
                    # taking strictly the left one chooses the shorter. We only
                    # want to be strict about leftmost when two completely differnt
                    # patterns match in the same text, such as a start and end match
                    # in a single line
                    length_diff = res.match_end-res.match_start - best_choice.match_end-best_choice.match_start
                    if res.match_end - best_choice.match_end == length_diff:
                        best_choice = res
        return best_choice
    return None

class DraftBuilder:

    def __init__(self, load_defaults=True):
        self.working_text = ""
        self.draft_text = ""
        self.draft_start_patterns = []
        self.draft_end_patterns = []
        self.current_draft = None
        self.section_start_patterns = []
        self.section_end_patterns = []
        self.current_section = None
        self.roll_size = 100
        if load_defaults:
            for sp in default_draft_start_patterns:
                self.add_draft_start_pattern(sp)
            for ep in default_draft_end_patterns:
                self.add_draft_end_pattern(ep)

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
                if self.current_draft:
                    self.draft_text += self.working_text[:-self.roll_size]
                self.working_text = self.working_text[-self.roll_size:]
            return self.current_draft, last_draft if last_draft != self.current_draft else None
        if self.current_draft:
            self.draft_text += self.working_text[:matched.match_start]
        if matched.match_pattern in self.draft_start_patterns:
            if not self.current_draft:
                text_mark = TextMark(0, matched.match_end-matched.match_start,  matched.matched_text)
                text_mark = TextMark(matched.match_start, matched.match_end, matched.matched_text)
                self.current_draft = Draft(start_text=text_mark)
                self.working_text = self.working_text[matched.match_end:]
                logger.debug("New draft starting (none current) on pattern '%s'",
                             matched.match_pattern.pattern)
                logger.debug("working_text now '%s'", self.working_text)
            else:
                logger.debug("New draft starting (one already current) on pattern %s", matched.match_pattern)
                end_mark = TextMark(matched.match_start, matched.match_start, "")
                self.current_draft.end_text = end_mark
                self.current_draft.full_text = self.draft_text
                self.draft_text = ''
                end_of_new = matched.match_end-matched.match_start
                text_mark = TextMark(0, end_of_new,  matched.matched_text)
                self.current_draft = Draft(start_text=text_mark)
                self.working_text = self.working_text[matched.match_end:]
                logger.debug("closed doc, working_text now '%s'", self.working_text)
        elif matched.match_pattern in self.draft_end_patterns:
            if not self.current_draft:
                self.working_text = self.working_text[matched.match_end:]
                logger.warning("Got end of draft signal when no current draft! truncating working to %d",
                               len(self.working_text))
            else:
                logger.debug("Ending current draft on pattern %s score %f", matched.match_pattern, matched.score)
                end_mark = TextMark(matched.match_start, matched.match_end, matched.matched_text)
                self.current_draft.end_text = end_mark
                self.current_draft.full_text = self.draft_text
                self.draft_text = ''
                self.working_text = self.working_text[matched.match_end:]
                logger.debug("closed doc, working_text now '%s'", self.working_text)
                self.current_draft = None
        return self.current_draft, last_draft if last_draft != self.current_draft else None

    async def end_of_text(self):
        if self.current_draft:
            logger.debug("Ending current draft on call to end_of_text")
            end = len(self.working_text)
            end_mark = TextMark(end, end, "")
            self.current_draft.end_text = end_mark
            self.current_draft.full_text = self.draft_text
            if self.current_draft.full_text.strip()  == '':
                self.current_draft.full_text = self.working_text
            draft = self.current_draft
            self.current_draft = None
            return draft
        return None
            

class DraftMaker(TextEventListener, AudioEventListener):


    def __init__(self):
        self.builder = DraftBuilder()
        self.current_draft = None
        self.emitter = AsyncIOEventEmitter()

    def add_event_listener(self, e_listener: DraftEventListener) -> None:
        self.emitter.on(DraftEvent, e_listener.on_draft_event)

    async def on_text_event(self, event: TextEvent):
        await self.handle_text_event(event)
        
    async def handle_text_event(self, event:TextEvent, new_text=None):
        if new_text is None:
            check_text = event.text
        else:
            check_text = new_text
        found_signal = False
        current_draft,last_draft = await self.builder.new_text(check_text)
        if last_draft:
            # closed a draft
            new_event = DraftEndEvent(draft=last_draft, timestamp=event.audio_end_time)
            await self.emitter.emit(DraftEvent, new_event)
            self.current_draft = None
            if self.current_draft == last_draft:
                self.current_draft = None
            found_signal = True
        if current_draft and self.current_draft is None:
            # new draft
            new_event = DraftStartEvent(draft=current_draft, timestamp=event.audio_start_time)
            await self.emitter.emit(DraftEvent, new_event)
            self.current_draft = current_draft
            found_signal = True
        if found_signal and len(self.builder.working_text) > 10:
            # there may be addition draft signals in the text, for instance if
            # the text is very long
            await self.handle_text_event(event, '')
            
    async def on_audio_event(self, event: AudioEvent):
        pass
    
    async def force_end(self):
        draft = await self.builder.end_of_text()
        if draft:
            new_event = DraftEndEvent(draft=draft, timestamp=time.time())
            await self.emitter.emit(DraftEvent, new_event)
            logger.info("Emitted draft end on force_end")
        
