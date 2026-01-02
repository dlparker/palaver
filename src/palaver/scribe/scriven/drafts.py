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
                                         DraftRescanEvent,
                                         DraftEventListener,
                                         Draft,
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

for name in ['freddy', 'rupert', ]:
    pattern = f"{name} take this down"
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
        for preamble in ['', 'hey ', 'wake up ']:
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

@dataclass
class TextEventJob:
    text_event: TextEvent
    drafts: list[Draft] = field(default_factory=list[Draft])
    
class DraftBuilder:

    def __init__(self, load_defaults=True):
        self.working_text = ""
        self.search_text = ""
        self.input_text = ""
        self.search_pos = 0
        self.draft_text = ""
        self.draft_start_patterns = []
        self.draft_end_patterns = []
        self.current_draft = None
        self.section_start_patterns = []
        self.section_end_patterns = []
        self.current_section = None
        self.roll_size = 100
        self.job_queue = asyncio.Queue()
        self.result_queue = asyncio.Queue()
        self.text_event_map = []  # [(TextEvent, start_pos_in_buffer, end_pos_in_buffer), ...]
        if load_defaults:
            for sp in default_draft_start_patterns:
                self.add_draft_start_pattern(sp)
            for ep in default_draft_end_patterns:
                self.add_draft_end_pattern(ep)

    def add_draft_start_pattern(self, pattern: MatchPattern):
        self.draft_start_patterns.append(pattern)
        self.runner_task = get_error_handler().wrap_task(self.job_runner)

    def add_draft_end_pattern(self, pattern: MatchPattern):
        self.draft_end_patterns.append(pattern)

    def find_events_for_match(self, match_start, match_end=None):
        """Find TextEvent(s) that overlap with the matched position range.

        If match_end is None, finds event containing match_start (the first character).
        Returns list of TextEvents ordered by position in buffer.
        """
        if match_end is None:
            match_end = match_start + 1

        events = []
        for event, start, end in self.text_event_map:
            if start < match_end and end > match_start:
                events.append((event, start, end))

        if len(events) == 0:
            breakpoint()
        return [e[0] for e in sorted(events, key=lambda x: x[1])]

    def _trim_event_map_from_start(self, trim_to):
        """Adjust text_event_map when trimming characters from start of buffer.

        Args:
            trim_to: Position in buffer up to which we're trimming (exclusive)
        """
        new_map = []
        for event, start, end in self.text_event_map:
            if end > trim_to:  # Event extends beyond trim point
                new_start = max(0, start - trim_to)
                new_end = end - trim_to
                new_map.append((event, new_start, new_end))
        self.text_event_map = new_map

    async def job_runner(self):
        try:
            while True:
                job = await self.job_queue.get()
                #logger.debug("Dequeued job %s", job)
                job.drafts = await self.new_text_event_op(job.text_event)
                await self.result_queue.put(job)
                #logger.debug("Queued Result %s", job)
        except asyncio.exceptions.CancelledError:
            return
                
    async def new_text_event(self, text_event):
        job = TextEventJob(text_event)
        await self.job_queue.put(job)
        #logger.debug("Queued job %s", job)
        result = await self.result_queue.get()
        #logger.debug("De-queued result %s", result)
        return result.drafts
    
    async def new_text_event_op(self,  text_event):
        start_pos = len(self.input_text)
        if start_pos > 0 and self.input_text[-1] != ' ' and text_event.text[0] != ' ':
            self.input_text += ' ' 
        self.input_text += text_event.text
        end_pos = len(self.input_text)
        self.text_event_map.append((text_event, start_pos, end_pos))

        if self.current_draft:
            patterns =  self.draft_end_patterns + self.draft_start_patterns 
        else:
            patterns = self.draft_start_patterns + self.draft_end_patterns 

        last_draft = self.current_draft
        drafts = []
        matched = match_first(patterns, self.input_text[self.search_pos:])
        if not matched:
            if self.current_draft:
                self.draft_text += self.input_text[start_pos:]
            # we don't update the search position because the current
            # text might be part of the next signal, so we need to keep
            # it
            return []

        while matched:
            actual_start, actual_end = self.find_real_range(matched)
            matched_texts = self.find_events_for_match(actual_start, actual_end)

            # The possibilites are:
            # 1. We have a current draft, and :
            #    a. This is an end match, so we end it
            #    b. This is a start match, so we end the current before starting the new one
            # 2. We have no current draft and:
            #    a. This is a start match, so we start one
            #    b. This is an end match, so the user's a moron and we ingore everything he said till now
            if matched.match_pattern in self.draft_end_patterns and not self.current_draft:
                # 2.b. from above
                self._trim_event_map_from_start(actual_end)
                self.input_text = self.input_text[actual_end:]
                self.search_pos = 0
                logger.warning("Got end of draft signal %s when no current draft! truncating input to %d",
                               matched.matched_text,
                               len(self.input_text))
                logger.debug("Input after out of sync match '%s' now '%s'", 
                               matched.matched_text,
                               self.input_text)
            elif self.current_draft:
                # 1.a or 1.b. from above
                # either and end or a new start, either way we copy new text up to the match
                draft = self.finish_current_draft(matched)
                new_drafts = []
                found = False
                for dr in drafts:
                    if dr.draft_id == draft.draft_id:
                        new_drafts.append(draft)
                        found = True
                        continue
                    new_drafts.append(dr)
                if not found:
                    new_drafts.append(draft)
                drafts = new_drafts
            if matched.match_pattern in self.draft_start_patterns:
                next_matched = match_first(patterns, self.input_text[actual_end:])
                # 1.b. or 2.a. from above
                if next_matched:
                    next_pos = next_matched.match_start + actual_end
                else:
                    next_pos = None
                self.current_draft = self.make_new_draft(matched, next_pos)
                drafts.append(self.current_draft)
            # stuff above messes with the input text, so search again
            matched = match_first(patterns, self.input_text[self.search_pos:])
        return drafts
    
    def finish_current_draft(self, matched):
        logger.info("Ending current draft on pattern %s score %f", matched.match_pattern, matched.score)
        
        actual_start, actual_end = self.find_real_range(matched)
        draft_pos = len(self.draft_text)
        if actual_start > draft_pos:
            self.draft_text += self.input_text[draft_pos:actual_start]
        self.current_draft.full_text = self.draft_text
        # the positions aren't useful, legacy, but the text is
        end_mark = TextMark(actual_start, actual_end, matched.matched_text)
        self.current_draft.end_text = end_mark
        matched_texts = self.find_events_for_match(actual_start, actual_end)
        self.current_draft.end_matched_events = matched_texts
        self.current_draft.audio_end_time = matched_texts[0].audio_end_time
        # cleanup
        self.draft_text = ''
        # trim till after the match
        self.input_text = self.input_text[actual_end:]
        self.search_pos = 0
        self._trim_event_map_from_start(actual_end)
        logger.debug("closed draft, input_text now '%s'", self.input_text)
        res = self.current_draft
        self.current_draft = None
        return res

    def make_new_draft(self, matched, next_match_pos=None):
        actual_start, actual_end = self.find_real_range(matched)
        
        if not next_match_pos:
            self.draft_text += self.input_text[actual_end:]
        else:
            self.draft_text += self.input_text[actual_end:next_match_pos]
            
        self.input_text = self.input_text[actual_end:]
        self.search_pos = 0
        # the positions aren't useful, legacy, but the text is
        text_mark = TextMark(actual_start, actual_end,  matched.matched_text)
        matched_texts = self.find_events_for_match(actual_start, actual_end)
        self.current_draft = Draft(start_text=text_mark,
                                   audio_start_time = matched_texts[0].audio_start_time)
        #self._trim_event_map_from_start(matched.match_end)
        logger.info("New draft starting on pattern '%s' score %f",
                     matched.match_pattern.pattern, matched.score)
        logger.debug("input_text now '%s'", self.input_text)
        return self.current_draft
        
    def find_real_range(self, matched):
        # Find the first word in matched starting at the index
        # it says.
        adj_start = matched.match_start  + self.search_pos
        adj_end = matched.match_end  + self.search_pos
        msplit = matched.matched_text.lower().rstrip().split(' ')
        buff = self.input_text[adj_start:].lower()
        index = buff.find(msplit[0])
        if index == -1:
            # the input text might have some extra spaces
            # before the match which will get stripped out
            # during match. Take a swing at it.
            start = max(0, adj_start-5)
            index = buff.find(msplit[0])
            if index == -1:
                substr = self.input_text[start:]
                breakpoint()
                raise Exception(f"Can't find matched text '{matched.matched_text}' in '{substr}'")
        actual_start = index
        last = msplit[-1]
        index = self.input_text[actual_start:].lower().find(last.lower())
        if index == -1:
            substr = self.input_text[actual_start:]
            raise Exception(f"Can't find matched text '{matched.matched_text}' in '{substr}'")
        actual_end = index
        # need to advance till space or end
        while actual_end < len(self.input_text) and not self.input_text[actual_end].isspace():
            actual_end += 1
        logger.debug("find_real_range says '%s' is at %d:%d of '%s' so '%s'",
                     matched.matched_text, actual_start, actual_end, self.input_text,
                     self.input_text[actual_start:actual_end])
        return actual_start, actual_end

    async def end_of_text(self):
        if self.current_draft:
            logger.info("Ending current draft on call to end_of_text")
            draft = self.finish_current_draft(matched)
            self.current_draft = None
            return draft
        return None
            

class DraftMaker(TextEventListener, AudioEventListener):


    def __init__(self):
        self.builder = DraftBuilder()
        self.current_draft = None
        self.emitter = AsyncIOEventEmitter()

    def add_draft_event_listener(self, e_listener: DraftEventListener) -> None:
        self.emitter.on(DraftEvent, e_listener.on_draft_event)

    async def on_text_event(self, event: TextEvent):
        await self.handle_text_event(event)
        
    async def handle_text_event(self, event:TextEvent):
        drafts = await self.builder.new_text_event(event)
        for draft in drafts:
            if draft.end_text is None:
                new_event = DraftStartEvent(draft=draft, timestamp=draft.audio_end_time)
                await self.emitter.emit(DraftEvent, new_event)
                self.current_draft = draft
            else:
                new_event = DraftEndEvent(draft=draft, timestamp=draft.audio_end_time)
                await self.emitter.emit(DraftEvent, new_event)
                if self.current_draft and self.current_draft.draft_id == draft.draft_id:
                    self.current_draft = None

            
    async def on_audio_event(self, event: AudioEvent):
        pass
    
    async def force_end(self):
        draft = await self.builder.end_of_text()
        if draft:
            new_event = DraftEndEvent(draft=draft, timestamp=time.time())
            await self.emitter.emit(DraftEvent, new_event)
            logger.info("Emitted draft end on force_end")
        
    async def import_draft(self, draft):
        if draft.parent_draft_id:
            new_event = DraftRescanEvent(original_draft_id=draft.parent_draft_id,
                                         draft=draft, timestamp=draft.timestamp)
            await self.emitter.emit(DraftEvent, new_event)
            logger.info("Emitted imported draft as rescan event")
            
            
