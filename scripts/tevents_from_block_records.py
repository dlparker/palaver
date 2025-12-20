#!/usr/bin/env python
import asyncio
import logging
import json
import argparse
from pprint import pprint
from pathlib import Path
from palaver.scribe.text_events import TextEvent
from palaver.scribe.command_events import (ScribeCommand,
                                           ScribeCommandEvent,
                                           CommandEventListener)

from palaver.scribe.scriven.wire_commands import CommandDispatch
from loggers import setup_logging
import soundfile as sf
import sounddevice as sd


async def get_text_events(path, printing=False):

    with open(path) as f:
        events = json.load(f)

    t_events = {}
    for event in events:
        if "ScribeCommandEvent" in event['classname']:
            if printing:
                print('-------------')
                pprint(event)
            command = ScribeCommand(**event['properties']['command'])
            if 'properties' in event['properties']['text_event']:
                kwargs = event['properties']['text_event']['properties']
            else:
                kwargs = event['properties']['text_event']
            text_event = TextEvent(**kwargs)
            if text_event.event_id not in t_events:
                t_events[text_event.event_id] = text_event
            if event['properties']['attention_text_event'] != None:
                atten_text_event = TextEvent(**event['properties']['attention_text_event'])
            else:
                atten_text_event = None
            if atten_text_event.event_id not in t_events:
                t_events[atten_text_event.event_id] = atten_text_event
            scev = ScribeCommandEvent(event_id=event['properties']['event_id'],
                                      command=command,
                                      attention_text_event=atten_text_event,
                                      pattern=event['properties']['pattern'],
                                      text_event = text_event,
                                      segment_index = 0,
                                      matched_text = event['properties']['matched_text'],
                                      timestamp = event['properties']['timestamp'])
            if printing:
                pprint(scev)
                print('-------------')
        elif "TextEvent" in event['classname']:
            kwargs = event['properties']
            text_event = TextEvent(**kwargs)
            if text_event.event_id not in t_events:
                t_events[text_event.event_id] = text_event
    return t_events

async def main():

    parser = argparse.ArgumentParser(
        description="extract text events from block recorder meta_events.json",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        'file_path',
        type=Path,
        nargs='?',
        help=''
    )
    args = parser.parse_args()
    if args.file_path is None:
        parser.error("Must supply a file path")
    
    for item in (await get_text_events(args.file_path)).values():
        pprint(item)
        
if __name__=="__main__":

    asyncio.run(main())


