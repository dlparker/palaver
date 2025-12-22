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
from palaver.scribe.audio_events import AudioEvent, AudioStopEvent, AudioEventListener
from palaver.scribe.text_events import TextEvent, TextEventListener
from palaver.scribe.draft_events import (DraftEvent,
                                         DraftStartEvent,
                                         DraftEndEvent,
                                         DraftEventListener,
                                         Draft,
                                         Section,
                                         TextMark)
                                         
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

default_draft_start_patterns = []
default_draft_end_patterns = []

for name in ['rupert', 'bubba', 'freddy', 'babbage']:
    for doc_name in ["draft", "document"]:
        for preamble in ["", 'hey', 'wake up']:
            for start in ['start', 'begin', 'new']:
                pattern = f"{preamble} {name} {start} {doc_name}"
                pat = MatchPattern(pattern, [name, doc_name, start])
                default_draft_start_patterns.append(pat)
            for stop in ['stop', 'close', 'end']:
                pattern = f"{preamble} {name} {stop} {doc_name}"
                pat = MatchPattern(pattern, [name, doc_name, stop])
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
                        if score >= 80:
                            break
                    if best_score < 80:
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

    def __init__(self, load_defaults=True):
        self.working_text = ""
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
                end_mark = TextMark(matched.match_start, matched.match_start, "")
                self.current_draft.end_text = end_mark
                self.current_draft.full_text = self.current_draft.text_buffer
                self.current_draft.text_buffer = None
                end_of_new = matched.match_end-matched.match_start
                text_mark = TextMark(0, end_of_new,  matched.matched_text)
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
            self.current_draft.full_text = self.current_draft.text_buffer
            self.current_draft.text_buffer = None
            self.working_text = ""
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
        current_draft,last_draft = await self.builder.new_text(event.text)
        if last_draft:
            # closed a draft
            new_event = DraftEndEvent(draft=last_draft, timestamp=event.timestamp)
            await self.emitter.emit(DraftEvent, new_event)
            self.current_draft = None
            if self.current_draft == last_draft:
                self.current_draft = None
        if current_draft and self.current_draft is None:
            # new draft
            new_event = DraftStartEvent(draft=current_draft, timestamp=event.timestamp)
            await self.emitter.emit(DraftEvent, new_event)
            self.current_draft = current_draft
            
    async def on_audio_event(self, event: AudioEvent):
        if isinstance(event, AudioStopEvent):
            draft = await self.builder.end_of_text()
            if draft:
                new_event = DraftEndEvent(draft=draft, timestamp=event.timestamp)
                await self.emitter.emit(DraftEvent, new_event)

    async def force_end(self):
        draft = await self.builder.end_of_text()
        if draft:
            new_event = DraftEndEvent(draft=draft, timestamp=event.timestamp)
            await self.emitter.emit(DraftEvent, new_event)
        
