# Meeting Recorder

A macOS menu bar app that records meetings (mic + system audio), transcribes with whisper.cpp, summarizes with a local LLM (Ollama), and writes structured notes into your Obsidian vault. Everything runs on-device — no cloud, no subscriptions.

## How it works

```
Mic ─────────────────────────────────────────────┐
                                                  ▼
System Audio (BlackHole) ──────────────► Two-pass transcription
                                                  │
                                          whisper.cpp (local)
                                                  │
                                         Merge + dedup bleed
                                                  │
                                          Ollama summary + title
                                                  │
                                        Obsidian note (.md)
```

After you click **Stop Recording**, a modal lets you name the meeting and add context for the AI. The pipeline then runs in the background — transcription, summary, and a note saved to your vault. If you skip naming, the LLM picks a title automatically.

---

## Prerequisites

### 1. BlackHole 2ch — capture system audio

Download and install from [existential.audio/blackhole](https://existential.audio/blackhole/).

Then set up a Multi-Output Device so Teams/browser audio goes to both your speakers and BlackHole:

1. Open **Audio MIDI Setup** (Applications → Utilities)
2. Click **+** → **Create Multi-Output Device**
3. Check **BlackHole 2ch** and your speakers/headphones
4. Right-click the new device → **Use This Device for Sound Output**

### 2. whisper.cpp — local transcription

```bash
brew install whisper-cpp
```

Download the large-v3 model (~3 GB):

```bash
# Find your installed version
brew info whisper-cpp

# Download the model (adjust version path as needed)
curl -L -o /opt/homebrew/Cellar/whisper-cpp/1.8.4/share/whisper-cpp/ggml-large-v3.bin \
  "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3.bin"
```

### 3. Ollama — local LLM summarization

Download from [ollama.com](https://ollama.com), then:

```bash
ollama pull llama3.1:8b   # ~5 GB, works well on 16 GB+ RAM
ollama serve              # start before using the app
```

---

## Running the app

### Option A — pre-built app bundle (recommended)

Build the `.app` once:

```bash
git clone https://github.com/ayushb3/meeting-recorder
cd meeting-recorder
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/pyinstaller meeting_recorder.spec
```

This produces `dist/Meeting Recorder.app`. Double-click it (or copy to `/Applications`).

**First launch:** the app opens your config file automatically. Fill in your paths, save, then relaunch.

> To share with a teammate: zip `dist/Meeting Recorder.app` and send it. On first open they right-click → Open to bypass Gatekeeper, then edit the config that pops up.

### Option B — run from terminal (dev)

```bash
git clone https://github.com/ayushb3/meeting-recorder
cd meeting-recorder
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python app.py
```

---

## Configuration

On first launch the app copies a template to:

```
~/Library/Application Support/MeetingRecorder/config.toml
```

Edit that file — **Preferences** in the menu bar opens it directly.

```toml
[paths]
output_dir = "~/Documents/Obsidian/Meetings"   # where notes are saved

[audio]
system_device = "BlackHole 2ch"
mic_device = "MacBook Pro Microphone"          # run: python3 -c "import sounddevice; print(sounddevice.query_devices())"

[whisper]
binary = "/opt/homebrew/bin/whisper-cli"
model  = "/opt/homebrew/Cellar/whisper-cpp/1.8.4/share/whisper-cpp/ggml-large-v3.bin"

[ollama]
model = "llama3.1:8b"
host  = "http://localhost:11434"

[processing]
keep_audio             = true   # keep .wav files alongside notes
min_recording_seconds  = 30     # discard accidental short recordings
low_disk_threshold_mb  = 500    # auto-stop if disk is low
```

---

## Usage

1. Start Ollama: `ollama serve`
2. Launch **Meeting Recorder** from the menu bar
3. Click **Start Recording** before your meeting starts
4. Click **Stop Recording** when done
5. A modal appears — enter a meeting name and optional context for the AI summary
6. Wait for the notification: "Note saved — *Your Meeting Title*"

### Menu items

| Item | When available |
|---|---|
| Start / Stop Recording | Always |
| Set Meeting Name… | While recording (pre-names the folder) |
| Reprocess Last Meeting | Only when a `.error` file exists |
| Open Today's Note | Always (shows notification if none found) |
| Preferences | Opens config file in default editor |

---

## Output structure

```
~/Documents/Obsidian/Meetings/
└── 2026-W16/
    └── q2-planning-with-design-team/
        ├── meeting.md
        ├── audio-mic.wav
        └── audio-system.wav
```

Each note:

```markdown
---
date: 2026-04-14
time: 09:54
duration: 19m
tags: [meeting, transcript]
title: Q2 Planning With Design Team
---

## TL;DR
...

## Topics Covered
...

## Key Decisions
...

## Action Items
- Alice — send revised timeline by Friday

## Audio

![[audio-mic.wav]]
![[audio-system.wav]]

## Full Transcript
[00:00] Hello everyone...
```

---

## Error recovery

If the pipeline fails (whisper crash, Ollama down, disk full), a `.error` file is written next to the audio files. Audio is always preserved. Use **Reprocess Last Meeting** from the menu to retry — it re-runs the full pipeline on the saved audio.

---

## Development

```bash
# Run tests
.venv/bin/pytest tests/ -v

# Rebuild the app bundle
.venv/bin/pyinstaller meeting_recorder.spec
```

---

## Stack

| Component | Library |
|---|---|
| Menu bar UI | `rumps` |
| Audio capture | `sounddevice` + `soundfile` |
| Transcription | `whisper.cpp` (subprocess) |
| Summarization | Ollama local HTTP API |
| Config | `tomllib` (stdlib) |
| Bundling | PyInstaller 6.x |
