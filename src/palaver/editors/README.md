# Palaver Editors

This directory contains the interactive editors for transcript correction.

## Phase 2: Simple Marker (`marker.py`)

**Purpose**: Quickly mark which segments need re-recording without editing them.

**Usage**:
```bash
python editors/marker.py sessions/20251202_194521
```

**Keybindings**:
- `j / ↓` : Next segment
- `k / ↑` : Previous segment
- `space / !` : Toggle mark for fixing
- `a` : Mark all segments
- `c` : Clear all marks
- `p` : Play current segment audio (requires `aplay`)
- `s` : Save and quit
- `q` : Quit without saving

**Input**: `sessions/YYYYMMDD_HHMMSS/transcript_raw.txt`

**Output**: `sessions/YYYYMMDD_HHMMSS/blocks_to_fix.json`

Example output:
```json
{
  "session": "20251202_194521",
  "marked_for_fix": [1, 2, 7, 12],
  "total_segments": 15,
  "timestamp": "2025-12-02T14:05:22Z"
}
```

**Design Philosophy**:
- Minimal, fast workflow
- No editing capabilities (prevents scope creep)
- Visual marking interface
- Audio playback for verification
- Outputs simple list for Phase 3 processing

---

## Phase 3: Re-Recorder (`rerecorder.py`)

**Purpose**: Fix transcription errors by re-recording only marked segments.

**Usage**:
```bash
python editors/rerecorder.py sessions/20251202_194521
```

**Workflow for each marked segment**:
1. Shows segment with ±2 lines context
2. Plays original audio
3. Press Enter to re-record
4. Records new audio (VAD-based, same as Phase 1)
5. Plays back recorded audio
6. Choose action:
   - `[k]` Keep (save WAV, transcribe, update transcript)
   - `[r]` Retry (record again)
   - `[s]` Skip (discard and move to next)
   - `[q]` Quit

**Input**:
- `blocks_to_fix.json` (from Phase 2)
- `transcript_raw.txt`
- Original `seg_NNNN.wav` files

**Output**:
- Overwrites bad `seg_NNNN.wav` files in-place
- Creates `transcript_corrected.txt` (final corrected version)
- Creates `rerecording_log.json` (metadata)

**Features**:
- Voice-only correction loop (no keyboard typing)
- VAD-based recording (automatic silence detection)
- Immediate playback for verification
- In-place WAV replacement
- Automatic re-transcription of corrected segments

**Requirements**:
- Audio playback: `aplay`, `paplay`, or `ffplay`
- Whisper transcription model in `models/` directory

---

## Phase 3+: Interactive Editor (`interactive_editor.py`)

**Purpose**: Full-featured text editor for advanced transcript correction.

**Status**: Available for future use. Includes:
- Inline text editing
- Split/merge segment operations
- Tag system (!, ?, *)
- Real-time correction output

**Note**: Original Grok-generated code. Not needed for voice-only workflow, but available for manual editing if desired.

---

## Complete Workflow

### End-to-End Process:

**Phase 1 - Record**:
```bash
uv run recorder/vad_recorder_v2.py
# Produces: sessions/20251202_194521/
#   - seg_NNNN.wav files
#   - transcript_raw.txt
#   - transcript_incremental.txt
```

**Phase 2 - Mark Bad Segments**:
```bash
python editors/marker.py sessions/20251202_194521
# Review transcript, mark errors
# Produces: blocks_to_fix.json
```

**Phase 3 - Re-Record Fixes**:
```bash
python editors/rerecorder.py sessions/20251202_194521
# Re-record marked segments
# Produces: transcript_corrected.txt
```

**Phase 4 - Final Assembly** (coming soon):
```bash
python editors/assembler.py sessions/20251202_194521
# Produces: final_20251202_194521.md
```

### Quick Example Session

```bash
# 1. Record your thoughts
uv run recorder/vad_recorder_v2.py
# [speak into mic, press Enter when done]

# 2. Review and mark mistakes
python editors/marker.py sessions/20251202_194521
# [use spacebar to mark bad segments, press 's' to save]

# 3. Fix mistakes by re-recording
python editors/rerecorder.py sessions/20251202_194521
# [re-record each marked segment, choose k/r/s/q]

# 4. Final output ready!
cat sessions/20251202_194521/transcript_corrected.txt
```
