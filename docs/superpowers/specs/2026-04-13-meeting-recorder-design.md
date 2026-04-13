# Meeting Recorder — Design Spec
**Date:** 2026-04-13  
**Status:** Approved

---

## Overview

A macOS menu bar app (Python + rumps) that records Microsoft Teams meetings by capturing both mic and system audio locally, transcribes with whisper.cpp, summarizes with Ollama, and writes structured notes directly into an Obsidian vault. Everything runs locally with free models — no cloud services.

---

## Architecture

**Approach:** Single Python process with subprocess workers. The menu bar app owns the process; recording and transcription are spawned as subprocesses. The pipeline is sequential and runs in a background thread so the menu bar stays responsive.

```
meeting-recorder/
├── app.py                  # Entry point — starts rumps menu bar app
├── config.toml             # User config (model, paths, audio devices)
├── recorder/
│   ├── audio.py            # Captures mic + system audio via sounddevice/BlackHole
│   └── mixer.py            # Merges two WAV streams into one combined file
├── transcriber/
│   └── whisper.py          # Calls whisper.cpp subprocess, parses JSON output
├── summarizer/
│   └── ollama.py           # HTTP POST to Ollama /api/generate
├── notes/
│   └── writer.py           # Formats and writes Obsidian markdown note
├── pipeline/
│   └── processor.py        # Orchestrates: record → transcribe → summarize → save
└── ui/
    └── menu.py             # rumps MenuBarApp — buttons, status, notifications
```

---

## System Audio Capture

macOS does not expose system audio to applications by default. **BlackHole** (free, open source virtual audio device) is required as a one-time user setup:

1. Install BlackHole 2ch
2. Open Audio MIDI Setup → create a Multi-Output Device combining BlackHole + speakers
3. Set the Multi-Output Device as system output (Teams audio routes through it)
4. Set BlackHole 2ch as input in `config.toml`

The app ships a `setup-guide.md` that walks through this step by step.

`recorder/audio.py` opens two simultaneous `sounddevice` input streams:
- Mic stream: `mic_device` from config (default system input)
- System stream: `system_device` from config (BlackHole 2ch)

Both streams write to separate WAV files. After recording stops, `mixer.py` combines them into a single mixed WAV for whisper.cpp.

---

## Processing Pipeline

Runs in a background thread after "Stop Recording" is clicked. Non-blocking for the menu bar.

```
Record → Mix → Transcribe → Summarize → Write Note → Notify
```

### Stage details

**Record:** Two simultaneous sounddevice streams → `*-audio-mic.wav` + `*-audio-system.wav`

**Mix:** Combine both WAVs into `*-audio-mixed.wav` (averaged waveform merge)

**Transcribe:** 
```bash
whisper-cpp --model <model> --output-json --language auto <mixed.wav>
```
Output is parsed into timestamped transcript lines: `[HH:MM] text`

**Summarize:** Single Ollama prompt requesting structured output:
```
Given this meeting transcript, produce:
1. A 2-3 sentence TL;DR
2. Topics covered
3. Key decisions made
4. Action items (person + task + deadline if mentioned)

Transcript:
{transcript}
```

**Write Note:** Formats and writes Obsidian markdown to the output path.

**Notify:** macOS notification via `rumps.notification()`.

### Error handling

- Any stage failure stops the pipeline and writes a `.error` file alongside where output would have been (stage name + error message)
- A "Reprocess" menu item scans for `.error` files and re-runs the pipeline from the transcription stage onward (audio already exists — no re-recording needed)
- If Ollama is unreachable: note is saved with transcript only, flagged with `⚠ Summary unavailable`
- If whisper.cpp fails: raw audio is kept regardless of `keep_audio` config setting
- Recordings under 30 seconds are discarded (accidental trigger guard)
- Recording stops automatically if available disk space drops below 500MB

---

## File & Folder Organization

**Output root** (configurable, default: `/Users/I589687/Documents/WorkNotes/Work/Meetings/`):

```
Work/Meetings/
├── 2026-W15/
│   ├── 2026-04-13-14h30-meeting.md
│   ├── 2026-04-13-14h30-audio-mic.wav
│   ├── 2026-04-13-14h30-audio-system.wav
│   └── 2026-04-13-14h30-audio-mixed.wav
└── 2026-W16/
    └── ...
```

Week folders use ISO week number format: `YYYY-WXX`.

---

## Obsidian Note Format

```markdown
---
date: 2026-04-13
time: 14:30
duration: 47m
tags: [meeting, transcript]
---

# Meeting — 2026-04-13 14:30

## TL;DR
- Decided to ship feature X by Friday
- John to follow up on API access

## Summary
### Topics Covered
...
### Key Decisions
...
### Action Items
- [ ] John — follow up on API access by EOW
- [ ] Sarah — review design spec

## Full Transcript
**[00:00]** ...
**[01:23]** ...
```

---

## Menu Bar UI

States:
- `● Idle` — grey dot, app ready
- `● Recording... (0:04:23)` — red dot, live elapsed timer
- `● Processing...` — yellow dot
- `● Ready` — green dot briefly, returns to Idle
- `⚠ Error — click to view` — opens error log file

Menu items:
- Start Recording / Stop Recording (toggles)
- Reprocess Last Meeting (active only if `.error` file exists)
- Open Today's Note (opens in default app)
- Preferences (opens `config.toml` in default editor)
- Quit

---

## Configuration (`config.toml`)

```toml
[paths]
output_dir = "/Users/I589687/Documents/WorkNotes/Work/Meetings"

[audio]
system_device = "BlackHole 2ch"
mic_device = "default"

[whisper]
model = "large-v3"
binary = "/usr/local/bin/whisper-cpp"

[ollama]
model = "llama3.2"
host = "http://localhost:11434"

[processing]
keep_audio = true
min_recording_seconds = 30
low_disk_threshold_mb = 500
```

---

## Dependencies

| Package | Purpose |
|---|---|
| `rumps` | macOS menu bar app framework |
| `sounddevice` | Audio capture (mic + BlackHole streams) |
| `scipy` | WAV file read/write and mixing |
| `tomllib` (stdlib 3.11+) | Config parsing |
| `requests` | Ollama HTTP API calls |

External tools (user-installed):
- `whisper.cpp` binary
- `ollama` running locally
- BlackHole 2ch virtual audio device

---

## Setup Requirements

1. macOS (tested target: Ventura+)
2. Python 3.11+
3. BlackHole 2ch installed + Multi-Output Device configured
4. `whisper.cpp` compiled and on PATH (or path set in config)
5. `ollama` running (`ollama serve`)
6. Desired Ollama model pulled (`ollama pull llama3.2`)
