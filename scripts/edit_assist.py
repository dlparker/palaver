#!/usr/bin/env python
"""
Edit assistant that sends drafts to LLM for transcription error correction.

Uses SQLDraftRecorder to query drafts from database and sends them to
ollama with an editing prompt.
"""

import asyncio
import argparse
import sys
import time
from pathlib import Path
from datetime import datetime

from ollama import AsyncClient

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from palaver.scribe.recorders.sql_drafts import SQLDraftRecorder, DraftRecord
from palaver.scribe.draft_events import Draft, DraftStartEvent, DraftEndEvent, TextMark


# Test draft with common transcription errors
TEST_DRAFT_TEXT = """now. Their going to the store to buy supplies for the meeting. I think we need too get more time to complete the project because there are still many tasks remaining. The team has been working hard but its not quiet enough. We should schedule another meeting to discuss the timeline and make sure everyone is on the same page. break."""


async def generate_test_draft(db_dir: Path) -> int:
    """Generate a test draft with transcription errors and save to database.

    Returns the database ID of the created draft.
    """
    print(f"Generating test draft in {db_dir}")

    recorder = SQLDraftRecorder(output_dir=db_dir)

    # Create a draft with test text containing errors
    start_mark = TextMark(start=0, end=4, text="now.")
    end_mark = TextMark(
        start=len(TEST_DRAFT_TEXT) - 6,
        end=len(TEST_DRAFT_TEXT),
        text="break."
    )

    draft = Draft(
        start_text=start_mark,
        end_text=end_mark,
        full_text=TEST_DRAFT_TEXT,
        timestamp=time.time()
    )

    # Save draft by emitting start and end events
    await recorder.on_draft_event(DraftStartEvent(draft=draft))
    await recorder.on_draft_event(DraftEndEvent(draft=draft))

    # Get the draft we just saved
    drafts = recorder.get_all_drafts()
    if drafts:
        latest = drafts[-1]
        print(f"Created test draft with ID: {latest.id}")
        print(f"Draft directory: {latest.directory_path}")
        print(f"Draft text: {latest.full_text[:100]}...")
        return latest.id
    else:
        print("ERROR: Failed to create test draft")
        return None


async def load_latest_draft(db_dir: Path) -> DraftRecord:
    """Load the most recent draft from the database."""
    recorder = SQLDraftRecorder(output_dir=db_dir)
    drafts = recorder.get_all_drafts()

    if not drafts:
        print(f"ERROR: No drafts found in {db_dir / 'drafts.db'}")
        return None

    latest = drafts[-1]
    print(f"Loaded draft ID {latest.id} from {latest.created_at}")
    print(f"Draft text ({len(latest.full_text)} chars): {latest.full_text[:100]}...")
    return latest


def load_prompt_preamble(version: str = 'v3_concise') -> str:
    """Load the editing prompt preamble.

    Args:
        version: 'v2' for detailed format with positions,
                 'v3_concise' for simplified format without positions (default)
    """
    if version == 'v3_concise':
        prompt_path = Path(__file__).parent.parent / "src/palaver/scribe/llm_edit/prompt_v3_concise.org"
    elif version == 'v2':
        prompt_path = Path(__file__).parent.parent / "src/palaver/scribe/llm_edit/prompt_v2.org"
    else:
        print(f"WARNING: Unknown prompt version '{version}', using v3_concise")
        prompt_path = Path(__file__).parent.parent / "src/palaver/scribe/llm_edit/prompt_v3_concise.org"

    if not prompt_path.exists():
        print(f"WARNING: Prompt file not found at {prompt_path}")
        print("Using minimal fallback prompt")
        return """You are an editing assistant that reviews voice transcriptions.
Identify and suggest corrections for errors in the text below.
Output your analysis as JSON with the structure shown in the examples.
"""

    with open(prompt_path, 'r') as f:
        return f.read()


async def send_to_llm(draft_text: str, ollama_host: str, model: str, prompt_version: str = 'v3_concise') -> dict:
    """Send draft text with prompt to ollama and return the response."""
    client = AsyncClient(host=ollama_host)

    # Build the full prompt
    preamble = load_prompt_preamble(version=prompt_version)
    full_prompt = f"""{preamble}

*** Draft to Analyze

{draft_text}

*** Your Analysis

Please analyze the above draft and output your suggestions in JSON format:
"""

    print(f"\nSending to {model} at {ollama_host}...")
    print(f"Prompt length: {len(full_prompt)} chars")

    messages = [{'role': 'user', 'content': full_prompt}]

    response = await client.chat(
        model=model,
        messages=messages,
        format='json'  # Request JSON output format
    )

    return response


async def main():
    parser = argparse.ArgumentParser(
        description="Send drafts to LLM for transcription error correction"
    )
    parser.add_argument(
        '--db-dir',
        type=Path,
        required=True,
        help='Directory containing drafts.db database'
    )
    parser.add_argument(
        '--generate-test',
        action='store_true',
        help='Generate a test draft with transcription errors'
    )
    parser.add_argument(
        '--draft-id',
        type=int,
        help='Specific draft ID to process (default: latest)'
    )
    parser.add_argument(
        '--ollama-host',
        default='http://192.168.100.242:11434',
        help='Ollama server host (default: http://192.168.100.242:11434)'
    )
    parser.add_argument(
        '--model',
        default='llama3.1:8b-instruct-q4_K_M',
        help='Ollama model to use (default: llama3.1:8b-instruct-q4_K_M)'
    )
    parser.add_argument(
        '--prompt-version',
        default='v3_concise',
        choices=['v2', 'v3_concise'],
        help='Prompt version: v2 (detailed with positions) or v3_concise (simplified, default)'
    )

    args = parser.parse_args()

    # Ensure db-dir exists
    args.db_dir.mkdir(parents=True, exist_ok=True)

    # Generate test draft if requested
    if args.generate_test:
        draft_id = await generate_test_draft(args.db_dir)
        if draft_id is None:
            return 1
        print()

    # Load the draft
    if args.draft_id:
        recorder = SQLDraftRecorder(output_dir=args.db_dir)
        draft = recorder.get_draft_by_id(args.draft_id)
        if not draft:
            print(f"ERROR: Draft ID {args.draft_id} not found")
            return 1
        print(f"Loaded draft ID {draft.id}")
    else:
        draft = await load_latest_draft(args.db_dir)
        if not draft:
            return 1

    # Send to LLM
    try:
        response = await send_to_llm(draft.full_text, args.ollama_host, args.model, args.prompt_version)

        print("\n" + "=" * 80)
        print("LLM RESPONSE")
        print("=" * 80)
        print()

        # The response content should be JSON
        assistant_message = response['message']['content']
        print(assistant_message)
        print()

    except Exception as e:
        print(f"\nERROR calling LLM: {e}")
        import traceback
        traceback.print_exc()
        return 1

    return 0


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
