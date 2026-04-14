# Meeting Recorder

A macOS menu bar app that records Microsoft Teams (or any system audio) meetings locally, transcribes with whisper.cpp, summarizes with Ollama, and writes structured notes directly into your Obsidian vault. Everything runs on-device — no cloud, no subscriptions.

## How it works

```
Mic + System Audio
       │
       ▼
  AudioRecorder  ──── mic.wav
                 ──── system.wav
                         │
                         ▼
                      Mixer  ──── mixed.wav
                         │
                         ▼
                   whisper.cpp  ──── transcript lines
                         │
                         ▼
                      Ollama  ──── structured summary
                         │
                         ▼
               Obsidian note (.md)
```

After you click **Stop Recording**, a background thread runs the full pipeline and writes a note to your vault. The menu bar icon shows status throughout.

## Prerequisites

| Tool | Purpose | Install |
|---|---|---|
| **BlackHole 2ch** | Capture system audio (Teams, browser, etc.) | [existential.audio/blackhole](https://existential.audio/blackhole/) |
| **whisper.cpp** | Local speech-to-text transcription | `brew install whisper-cpp` |
| **Ollama** | Local LLM summarization | [ollama.com](https://ollama.com) |

See [setup-guide.md](setup-guide.md) for full hardware configuration (BlackHole + Multi-Output Device).

## Setup

```bash
# 1. Clone and enter the repo
git clone https://github.com/ayushb3/meeting-recorder
cd meeting-recorder

# 2. Create virtualenv and install dependencies
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 3. Download a whisper model
curl -L -o /opt/homebrew/Cellar/whisper-cpp/$(brew info whisper-cpp --json | python3 -c "import sys,json; print(json.load(sys.stdin)[0]['installed'][0]['version'])")/share/whisper-cpp/ggml-large-v3.bin \
  "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3.bin"

# 4. Pull an Ollama model
ollama serve &
ollama pull llama3.3:70b

# 5. Edit config.toml to match your setup (paths, devices, models)

# 6. Run
.venv/bin/python app.py
```

## Configuration

Edit `config.toml`:

```toml
[paths]
output_dir = "/path/to/your/Obsidian/vault/Meetings"

[audio]
system_device = "BlackHole 2ch"   # virtual audio device
mic_device = "MacBook Pro Microphone"

[whisper]
model = "/path/to/ggml-large-v3.bin"
binary = "/opt/homebrew/bin/whisper-cli"

[ollama]
model = "llama3.3:70b"            # or llama3.1:8b for faster summarization
host = "http://localhost:11434"

[processing]
keep_audio = true                 # keep .wav files alongside notes
min_recording_seconds = 30        # discard accidental short recordings
low_disk_threshold_mb = 500       # auto-stop if disk gets low
```

## Usage

1. Start Ollama: `ollama serve`
2. Run the app: `.venv/bin/python app.py`
3. A `●` icon appears in the menu bar
4. Click **Start Recording** before your meeting
5. Click **Stop Recording** when done
6. The pipeline runs in the background — you'll get a macOS notification when the note is ready

### Menu bar states

| Icon | Meaning |
|---|---|
| `●` | Idle, ready to record |
| `● Recording... (0:04:23)` | Recording in progress with live timer |
| `● Processing...` | Pipeline running (mix → transcribe → summarize) |
| `● Ready` | Note written successfully |
| `⚠ Error — click to view` | Pipeline failed — use Reprocess to retry |

### Output format

Notes are written to week-based folders in your output directory:

```
Meetings/
└── 2026-W16/
    ├── 2026-04-13-14h30-meeting.md
    ├── 2026-04-13-14h30-audio-mic.wav
    ├── 2026-04-13-14h30-audio-system.wav
    └── 2026-04-13-14h30-audio-mixed.wav
```

Each note includes YAML frontmatter, a TL;DR, topics, decisions, action items, and a full timestamped transcript.

## Error recovery

If the pipeline fails at any stage (e.g. whisper crashes, Ollama is down), a `.error` file is written alongside the audio. The **Reprocess Last Meeting** menu item re-runs the pipeline from the transcription stage — your audio is always preserved on failure.

## Development

```bash
# Run tests
.venv/bin/python -m pytest tests/ -v

# Run with verbose logging
.venv/bin/python app.py   # logs print to terminal at INFO level
```

## Stack

- **Python 3.11+** with `rumps` for the menu bar
- **sounddevice** + **soundfile** for dual-stream audio capture
- **scipy** for WAV mixing
- **whisper.cpp** (subprocess) for transcription
- **Ollama** (local HTTP) for summarization
- **tomllib** (stdlib) for config
