# Palaver — Four-Phase Roadmap to a Perfect Voice-Driven LLM Front-End

This document defines the **four minimal, fully-working phases** that take us from “I speak → noisy transcript” to “I speak freely → perfect, editable, LLM-ready Markdown with zero typing”.

Each phase is deliberately tiny, isolated, testable in < 5 minutes, and can be thrown away or upgraded later without breaking anything that comes after it.

| Phase | Command (final form)               | Core Deliverable                                 | Key New Concept                          | Status |
|-------|------------------------------------|--------------------------------------------------|------------------------------------------|--------|
| 1     | `palaver record`                   | VAD-only segmented audio + raw transcript        | Silero VAD, silence-triggered segments   | Ready now |
| 2     | `palaver mark <session>`           | Interactive line/block marker → `blocks_to_fix.json` | Human-in-the-loop error selection       | Simple TUI |
| 3     | `palaver fix <session>`            | Re-record only the marked blocks with confirm/retry/skip | Targeted dictation repair loop           | Ready now |
| 4     | `palaver build <session>`          | Final perfect Markdown + stitched audio + clean transcript | Assembly + re-transcription of corrected parts | One liner |

After Phase 4 you will have a **complete, offline, private, voice-only note → perfect Markdown** system that already beats every commercial voice-note product in accuracy and privacy.

### Phase 1 – VAD Recorder (`palaver record`)

**Goal**  
Record only when I am actually speak — never save silence or background noise.

**Output** (example)
```
sessions/20251202_194521/
├── seg_0000.wav
├── seg_0001.wav
├── ...
├── transcript_raw.txt      # one line per segment, numbered
└── manifest.json           # exact timestamps, durations, VAD confidence
```

**Features**
- Silero VAD (on CPU, < 4 ms latency)
- 0.5 s pre-roll, 0.8 s post-roll so words aren’t clipped
- Automatic splitting at > 30 s, minimum segment 2 s
- Uses your proven device=3 / 48000 Hz settings

**Command**
```bash
palaver record          # press Enter → speak → press Enter to stop
```

### Phase 2 – Block Marker (`palaver mark`)

**Goal**  
Let me quickly mark which lines/segments are wrong so I don’t pollute the LLM context.

**Input** → `transcript_raw.txt`  
**Output** → `blocks_to_fix.json`

**Interaction (text-only for now)**
```
 1   Today I'm going to talk about noise and how it affects...
 2   I'm going to wait for the end of the target for while I can search...
 3   because the holes are probably left on the...

Enter line numbers/ranges to fix (e.g. 2, 5-7) or press Enter if all good: 2
```

**Why throw-away is fine**  
Phase 6+ will replace this with voice commands (“fix the part about holes”).

### Phase 3 – Targeted Re-Recording (`palaver fix`)

**Goal**  
Fix only the mistakes with a tight, voice-only loop.

**Behaviour for each block**
1. Plays the original bad audio (`aplay`)
2. Shows ±2 lines context
3. “Press Enter when ready to re-record this block…”
4. Records new version (same or longer length)
5. Plays it back immediately
6. Menu: **[k]eep  [r]etry  [s]kip  [q]uit**
7. On keep → overwrites the original wav + updates corrected transcript

**Output**
- Original bad segments replaced in-place
- `transcript_corrected.txt` created

### Phase 4 – Final Assembly (`palaver build`)

**Goal**  
Produce the final artefact I actually want to feed to Claude / GPT / local LLM.

**Actions**
- Re-transcribe every segment with large-v3-turbo (now all perfect)
- Replace any fixed blocks with the corrected text
- Output:
  - `final_20251202_194521.md`
  - `final_20251202_194521.txt`
  - (optional) `final_20251202_194521.wav` stitched

**Command**
```bash
palaver build sessions/20251202_194521
```

