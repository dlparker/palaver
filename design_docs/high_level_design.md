# Palaver High-Level Design

Voice-driven note-taking system with VAD-based segmentation, Whisper transcription, and command detection.

## Architecture Overview

```
Microphone/File → VAD Segmentation → Transcription → Command Detection → Document Rendering
                       ↓                    ↓              ↓
                  Audio Events        Text Processing   State Machine
                       ↓                    ↓              ↓
                     TUI ←──────────────────┴──────────────┘
```

## Core Components

### 1. Audio Input (`recorder/audio_sources.py`)
- **AudioSource**: Abstract interface for audio input
- **DeviceAudioSource**: Live microphone via sounddevice
- **FileAudioSource**: WAV file playback for testing
- Unified callback interface matching sounddevice convention

### 2. VAD Recording (`recorder/async_vad_recorder.py`)
- Async/await architecture using Silero VAD (PyTorch)
- Two modes:
  - Normal: 0.8s silence threshold
  - Long note: 5s silence threshold (for extended dictation)
- Event-driven design with typed events (SpeechStarted, SpeechEnded, etc.)
- Real-time audio chunk processing at 48kHz → downsample to 16kHz for VAD
- Session management with timestamped directories

### 3. Transcription (`recorder/transcription.py`)
- **WhisperTranscriber**: Multiprocess worker pool calling whisper-cli
- **SimulatedTranscriber**: Instant mock transcription for testing
- Job queue → Worker processes → Result queue architecture
- Workers: 2 parallel processes by default
- Model: multilang_whisper_large3_turbo.ggml

### 4. Text Processing (`recorder/text_processor.py`)
- Consumes transcription results from queue
- Writes incremental transcripts (raw + processed)
- Command detection via ActionPhrase matching
- State machine: idle → waiting_for_title → collecting_body → idle
- Triggers VAD mode changes via callbacks

### 5. Command System (`commands/`)
- **CommandDoc**: Abstract base class defining command workflows
  - `command_phrase`: Trigger phrase ("start new note")
  - `speech_buckets`: Ordered list of input segments
  - `render()`: Generate output files
- **SpeechBucket**: Specification for capturing speech
  - Relative timing multipliers (segment_size, start_window, termination_silence)
  - Multiply global base values from RecorderConfig
- **SimpleNote**: Title + body workflow, renders to markdown
- Fuzzy command matching via rapidfuzz (LooseActionPhrase)

### 6. Action Phrase Matching (`recorder/action_phrases.py`)
- **LooseActionPhrase**: Fuzzy word-overlap matching
- Ignores filler words (a, the, um, etc.)
- Configurable threshold (0.66 default = 2 of 3 words must match)
- Prefix filtering to strip transcription artifacts ("clerk," → "")

### 7. Session Management (`recorder/session.py`)
- Timestamped directories: `sessions/YYYYMMDD_HHMMSS/`
- Contents: WAV segments, transcripts, manifest.json
- Manifest: metadata, segment list, timestamps

### 8. Configuration (`config/recorder_config.py`)
- YAML-based or defaults
- Base timing values:
  - base_segment_size: 5.0s (chunk duration)
  - base_start_window: 2.0s (timeout for bucket start)
  - base_termination_silence: 0.8s (silence to end bucket)
- VAD thresholds, transcription settings
- Command matching thresholds

### 9. TUI (`tui/recorder_tui.py`)
- Textual-based terminal interface
- Real-time transcript monitor
- Recording mode display (normal/long note/speaking indicator)
- Queue status (pending/completed transcriptions)
- Completed notes list
- Event-driven updates from AsyncVADRecorder callbacks

## Data Flow

1. **Audio Capture**: AudioSource delivers 30ms chunks → VAD analysis
2. **Speech Detection**: VAD identifies speech/silence → accumulate audio buffer
3. **Segmentation**: Silence threshold met → save WAV, queue transcription
4. **Transcription**: Worker process runs whisper-cli → result to queue
5. **Text Processing**: Result consumed → command detection → state updates
6. **Mode Switching**: Commands trigger VAD mode changes → TextProcessor callback
7. **Event Emission**: All stages emit typed events → TUI/monitoring
8. **Document Rendering**: Command workflow complete → CommandDoc.render()

## Key Design Patterns

- **Event-driven**: Typed dataclass events propagate through system
- **Async/await**: AsyncVADRecorder, TUI integration
- **Multiprocess**: Transcription workers (CPU-bound whisper-cli)
- **Abstract interfaces**: AudioSource, Transcriber, CommandDoc
- **Queue-based**: Thread-safe job/result queues between components
- **State machine**: Text processor manages note workflow states
- **Relative timing**: SpeechBucket multipliers × global base values

## File Locations

- `src/palaver/recorder/`: VAD, transcription, text processing
- `src/palaver/commands/`: Command system (CommandDoc, SpeechBucket, SimpleNote)
- `src/palaver/config/`: Configuration management
- `src/palaver/tui/`: Terminal UI
- `scripts/direct_recorder.py`: CLI entry point
- `tests/`: pytest suite
- `sessions/`: Runtime output (audio, transcripts, notes)
- `models/`: Whisper model files
