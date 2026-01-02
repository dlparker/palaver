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
from pprint import pformat

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
                                         Draft)
                                         
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

@dataclass
class TextEventIndex:
    # Tracking which text event provided the text for one of the
    # string buffers. The buffer may contain all or part of the
    # text in the event, since draft boudaries control the buffers
    # but may span multiple text_events with words before or
    # after the boundary signal
    text_event: TextEvent
    start_pos: int # index into search text
    end_pos: int # index into search text
    
class DraftBuilder:

    def __init__(self, load_defaults=True):
        self.search_text = "" # all text that should be searched, constructed from outer text and state
        self.search_text_events = [] # the text events that built the search text
        self.draft_start_patterns = []
        self.draft_end_patterns = []
        self.current_draft = None
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
        # make sure they serialize
        job = TextEventJob(text_event)
        await self.job_queue.put(job)
        #logger.debug("Queued job %s", job)
        result = await self.result_queue.get()
        #logger.debug("De-queued result %s", result)
        return result.drafts
    
    async def new_text_event_op(self,  text_event):
        start_pos = len(self.search_text)
        if start_pos > 0 and not self.search_text[-1].isspace() and not text_event.text[0].isspace():
            self.search_text += ' '
        logger.debug("Adding text '%s' to search '%s'", text_event.text, self.search_text)
        self.search_text += text_event.text
        end_pos = len(self.search_text)
        tei = TextEventIndex(text_event, start_pos, end_pos)
        self.search_text_events.append(tei)

        # make the search a little more efficient by ordering the match patterns
        if self.current_draft:
            patterns =  self.draft_end_patterns + self.draft_start_patterns 
        else:
            patterns = self.draft_start_patterns + self.draft_end_patterns 

        last_draft = self.current_draft
        drafts = []
        matched = match_first(patterns, self.search_text)
        if not matched:
            logger.debug("No match triggered by '%s', on search_text '%s'",
                                text_event.text, self.search_text)
            return []

        while matched:
            # figure out where it is in the search string, really
            actual_start, actual_end = self.find_real_range(matched)
            logger.debug('-'*80)
            logger.debug("matched = %s", matched)
            logger.debug("Actuals = %d to %d",actual_start, actual_end)
            logger.debug("on search text '%s'", self.search_text)
            logger.debug('-'*80)

            # Need to find the audio start and stop times.
            # This logic is deliberately laborious because
            # the algorithm is not easy to grok, so efficient
            # code would be hard to understand, an the
            # total cost is tiny

            start_tei = end_tei = None
            for tei in self.search_text_events:
                if tei.end_pos >= actual_start:
                    start_tei = tei
                    break
            if not start_tei:
                raise Exception(f"can't find starting text event index for match {matched}")
            for tei in self.search_text_events:
                if tei.end_pos >= actual_end:
                    end_tei = tei
                    break
            if not end_tei:
                logger.error('-'*80)
                logger.error('all events')
                for tei in self.search_text_events:
                    logger.error("%s", pformat(tei))
                logger.error('search text %s', self.search_text_events)
                logger.error('-'*80)
                raise Exception(f"can't find ending text event index for match {matched}")

            use_teis = []
            remaining_teis = []
            for tei in self.search_text_events:
                if tei.start_pos >= start_tei.start_pos and tei.end_pos <= end_tei.end_pos:
                    use_teis.append(tei)
                if tei.start_pos >= end_tei.start_pos:
                    remaining_teis.append(tei)
            # We do some trimming at this point, don't really care if signal
            # is draft start or draft end, for trimming what matters is
            # the state
            
            if self.current_draft:
                keep_text = self.search_text[:actual_start]
                self.current_draft.full_text = keep_text[:]
            else:
                keep_text = self.search_text[actual_end:]
                self.search_text = keep_text[:]

            # The possibilites are:
            # 1. We have a current draft, and :
            #    a. This is an end match, so we end it
            #    b. This is a start match, so we end the current before starting the new one
            # 2. We have no current draft and:
            #    a. This is a start match, so we start one
            #    b. This is an end match, so the user's a moron and we ingore everything he said till now
            if self.current_draft:
                # 1.a and 1.b
                # the positions aren't useful, legacy, but the text is
                self.current_draft.end_text = matched.matched_text
                self.current_draft.full_text = keep_text
                self.current_draft.audio_end_time = use_teis[-1].text_event.audio_end_time
                logger.info("Ending draft on signal match '%s' score %f audio_start = %f audio_end = %f",
                            matched.matched_text, matched.score,
                            self.current_draft.audio_start_time,
                            self.current_draft.audio_end_time)
                logger.debug("Draft is '%s' from texts '%s'", pformat(self.current_draft), pformat(use_teis))
                if self.current_draft not in drafts:
                    drafts.append(self.current_draft)
                self.current_draft = None
                self.search_text_events = remaining_teis
                self.search_text = self.search_text[actual_end:]
            elif matched.match_pattern in self.draft_end_patterns:
                # 2.b
                self.search_text_events = remaining_teis
                logger.info("Draft end signal '%s' detected when no draft current, ignoring",
                            matched.matched_text)
            if matched.match_pattern in self.draft_start_patterns:
                # 1.a and 2.a
                # the positions aren't useful, legacy, but the text is
                self.current_draft = Draft(start_text=matched.matched_text,
                                   audio_start_time = use_teis[0].text_event.audio_start_time)
                logger.info("New draft starting on pattern '%s' score %f",
                            matched.match_pattern.pattern, matched.score)
                drafts.append(self.current_draft)
                
                self.search_text_events = use_teis
                for tei in remaining_teis:
                    if tei not in use_teis:
                        self.search_text_events.append(tei)
            matched = match_first(patterns, self.search_text)
        return drafts
        
    def find_real_range(self, matched):
        # figure out where in the searched text the actual pattern
        # was, since the fuzzy search mangles the text removing
        # extra whitespace and punctuation.
        # Find the first word in matched starting at the index
        # it says in the search text, which should be close
        # enough to let us zoom in, and not likely to 
        # let us stumble upon a different match
        adj_start = matched.match_start
        adj_end = matched.match_end 
        msplit = matched.matched_text.lower().rstrip().split(' ')
        buff = self.search_text[adj_start:].lower()
        index = buff.find(msplit[0])
        if index == -1:
            # the input text might have some extra spaces
            # before the match which will get stripped out
            # during match. Take a swing at it backing up some
            # to catch it. The number 5 is air pulled
            start = max(0, adj_start-5)
            index = buff.find(msplit[0])
            if index == -1:
                raise Exception(f"Can't find matched text '{matched.matched_text}' in '{self.search_text}'")
        actual_start = adj_start + index
        # This is a bit tricky. We use "break break" or "break break break" in
        # some end patterns, so we need to find each of the words in the match string, not
        # just the last one.
        cursor = actual_start
        for nw in msplit[1:]:
            index = self.search_text[cursor:].lower().find(nw.lower())
            if index == -1:
                raise Exception(f"Can't find matched text '{matched.matched_text}' in '{self.search_text}'")
            cursor += index + len(nw)
        actual_end = cursor
        # need to advance till space or end so that we capture punctuation at the end
        while actual_end < len(self.search_text) and not self.search_text[actual_end].isspace():
            actual_end += 1
        logger.debug("find_real_range says '%s' is at %d:%d of search text so '%s'",
                     matched.matched_text, actual_start, actual_end, 
                     self.search_text[actual_start:actual_end])
        return actual_start, actual_end


    async def end_of_text(self):
        if self.current_draft:
            logger.info("Ending current draft on call to end_of_text")
            self.current_draft.end_text = "forced end"
            self.current_draft.full_text = self.search_text
            self.current_draft.audio_end_time = self.search_text_events[-1].text_event.audio_end_time
            logger.debug("Draft is '%s' from texts '%s'", pformat(self.current_draft),
                         pformat(self.search_text_events))
            draft = self.current_draft
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
            
            
