# Meeting Recorder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a macOS menu bar app that records Teams meetings (mic + system audio), transcribes with whisper.cpp, summarizes with Ollama, and writes structured notes to an Obsidian vault.

**Architecture:** Single Python process with `rumps` owning the menu bar. Recording uses two simultaneous `sounddevice` streams (mic + BlackHole). After stop, a background thread runs the sequential pipeline: mix → transcribe → summarize → write note → notify.

**Tech Stack:** Python 3.11+, rumps, sounddevice, scipy, requests, tomllib (stdlib), whisper.cpp (subprocess), ollama (local HTTP), BlackHole 2ch (virtual audio device)

---

## File Map

| File | Responsibility |
|---|---|
| `app.py` | Entry point — instantiates and runs the rumps app |
| `config.toml` | Default user configuration |
| `config.py` | Loads and validates `config.toml`, exposes typed `Config` dataclass |
| `recorder/audio.py` | Opens two sounddevice streams, writes mic + system WAVs |
| `recorder/mixer.py` | Reads two WAVs, averages waveforms, writes mixed WAV |
| `transcriber/whisper.py` | Runs whisper.cpp subprocess, parses JSON output into transcript lines |
| `summarizer/ollama.py` | POST to Ollama `/api/generate`, returns structured summary dict |
| `notes/writer.py` | Formats Obsidian markdown note, writes to week folder |
| `pipeline/processor.py` | Orchestrates mix→transcribe→summarize→write→notify, writes `.error` on failure |
| `ui/menu.py` | `rumps.App` subclass — all menu items, status title, timer, notifications |
| `tests/test_mixer.py` | Unit tests for WAV mixing |
| `tests/test_whisper.py` | Unit tests for whisper output parsing |
| `tests/test_ollama.py` | Unit tests for Ollama response parsing |
| `tests/test_writer.py` | Unit tests for note formatting and path generation |
| `tests/test_config.py` | Unit tests for config loading |
| `tests/test_processor.py` | Integration tests for pipeline orchestration |
| `setup-guide.md` | BlackHole + Multi-Output Device setup walkthrough |
| `requirements.txt` | Python dependencies |

---

## Task 1: Project scaffold and config loader

**Files:**
- Create: `config.py`
- Create: `config.toml`
- Create: `requirements.txt`
- Create: `tests/__init__.py`
- Create: `tests/test_config.py`
- Create: `recorder/__init__.py`
- Create: `transcriber/__init__.py`
- Create: `summarizer/__init__.py`
- Create: `notes/__init__.py`
- Create: `pipeline/__init__.py`
- Create: `ui/__init__.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config.py
import pytest
import tomllib
from pathlib import Path
import tempfile, os

def write_toml(content: str) -> Path:
    f = tempfile.NamedTemporaryFile(suffix=".toml", delete=False, mode="w")
    f.write(content)
    f.close()
    return Path(f.name)

def test_config_loads_defaults():
    from config import load_config, Config
    path = write_toml("""
[paths]
output_dir = "/tmp/meetings"
[audio]
system_device = "BlackHole 2ch"
mic_device = "default"
[whisper]
model = "base"
binary = "/usr/local/bin/whisper-cpp"
[ollama]
model = "llama3.2"
host = "http://localhost:11434"
[processing]
keep_audio = true
min_recording_seconds = 30
low_disk_threshold_mb = 500
""")
    cfg = load_config(path)
    assert cfg.output_dir == Path("/tmp/meetings")
    assert cfg.system_device == "BlackHole 2ch"
    assert cfg.whisper_model == "base"
    assert cfg.ollama_model == "llama3.2"
    assert cfg.keep_audio is True
    assert cfg.min_recording_seconds == 30
    os.unlink(path)

def test_config_missing_file_raises():
    from config import load_config
    with pytest.raises(FileNotFoundError):
        load_config(Path("/nonexistent/config.toml"))
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/I589687/Projects/meeting-recorder
python -m pytest tests/test_config.py -v
```
Expected: `ModuleNotFoundError: No module named 'config'`

- [ ] **Step 3: Create package `__init__.py` files**

```bash
touch tests/__init__.py recorder/__init__.py transcriber/__init__.py \
      summarizer/__init__.py notes/__init__.py pipeline/__init__.py ui/__init__.py
```

- [ ] **Step 4: Write `config.py`**

```python
# config.py
from dataclasses import dataclass
from pathlib import Path
import tomllib


@dataclass
class Config:
    output_dir: Path
    system_device: str
    mic_device: str
    whisper_model: str
    whisper_binary: Path
    ollama_model: str
    ollama_host: str
    keep_audio: bool
    min_recording_seconds: int
    low_disk_threshold_mb: int


def load_config(path: Path) -> Config:
    if not path.exists():
        raise FileNotFoundError(f"Config not found: {path}")
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    return Config(
        output_dir=Path(raw["paths"]["output_dir"]),
        system_device=raw["audio"]["system_device"],
        mic_device=raw["audio"]["mic_device"],
        whisper_model=raw["whisper"]["model"],
        whisper_binary=Path(raw["whisper"]["binary"]),
        ollama_model=raw["ollama"]["model"],
        ollama_host=raw["ollama"]["host"],
        keep_audio=raw["processing"]["keep_audio"],
        min_recording_seconds=raw["processing"]["min_recording_seconds"],
        low_disk_threshold_mb=raw["processing"]["low_disk_threshold_mb"],
    )
```

- [ ] **Step 5: Write `config.toml`**

```toml
# config.toml
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

- [ ] **Step 6: Write `requirements.txt`**

```
rumps>=0.4.0
sounddevice>=0.4.6
scipy>=1.11.0
requests>=2.31.0
numpy>=1.24.0
pytest>=7.4.0
```

- [ ] **Step 7: Install dependencies**

```bash
cd /Users/I589687/Projects/meeting-recorder
pip install -r requirements.txt
```
Expected: all packages install without error.

- [ ] **Step 8: Run tests to verify they pass**

```bash
python -m pytest tests/test_config.py -v
```
Expected: `2 passed`

- [ ] **Step 9: Commit**

```bash
git init
git add config.py config.toml requirements.txt tests/ recorder/__init__.py \
        transcriber/__init__.py summarizer/__init__.py notes/__init__.py \
        pipeline/__init__.py ui/__init__.py
git commit -m "feat: project scaffold and config loader"
```

---

## Task 2: WAV mixer

**Files:**
- Create: `recorder/mixer.py`
- Create: `tests/test_mixer.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_mixer.py
import numpy as np
import tempfile, os
from pathlib import Path
from scipy.io import wavfile


def make_wav(path: Path, data: np.ndarray, rate: int = 16000):
    wavfile.write(str(path), rate, data.astype(np.int16))


def test_mix_produces_average():
    from recorder.mixer import mix_wavs
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        a = np.array([1000, 2000, 3000], dtype=np.int16)
        b = np.array([3000, 2000, 1000], dtype=np.int16)
        make_wav(d / "mic.wav", a)
        make_wav(d / "sys.wav", b)
        out = d / "mixed.wav"
        mix_wavs(d / "mic.wav", d / "sys.wav", out)
        rate, data = wavfile.read(str(out))
        assert rate == 16000
        np.testing.assert_array_equal(data, np.array([2000, 2000, 2000], dtype=np.int16))


def test_mix_handles_length_mismatch():
    from recorder.mixer import mix_wavs
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        a = np.array([1000, 2000, 3000, 4000], dtype=np.int16)
        b = np.array([2000, 2000], dtype=np.int16)
        make_wav(d / "mic.wav", a)
        make_wav(d / "sys.wav", b)
        out = d / "mixed.wav"
        mix_wavs(d / "mic.wav", d / "sys.wav", out)
        _, data = wavfile.read(str(out))
        assert len(data) == 4  # pads shorter to length of longer
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_mixer.py -v
```
Expected: `ImportError: cannot import name 'mix_wavs'`

- [ ] **Step 3: Write `recorder/mixer.py`**

```python
# recorder/mixer.py
from pathlib import Path
import numpy as np
from scipy.io import wavfile


def mix_wavs(mic_path: Path, system_path: Path, output_path: Path) -> None:
    """Average two WAV files into one. Pads the shorter to the length of the longer."""
    rate_a, data_a = wavfile.read(str(mic_path))
    rate_b, data_b = wavfile.read(str(system_path))

    # Ensure mono int16
    if data_a.ndim > 1:
        data_a = data_a.mean(axis=1).astype(np.int16)
    if data_b.ndim > 1:
        data_b = data_b.mean(axis=1).astype(np.int16)

    # Pad shorter array
    max_len = max(len(data_a), len(data_b))
    data_a = np.pad(data_a, (0, max_len - len(data_a)))
    data_b = np.pad(data_b, (0, max_len - len(data_b)))

    mixed = ((data_a.astype(np.int32) + data_b.astype(np.int32)) // 2).astype(np.int16)
    wavfile.write(str(output_path), rate_a, mixed)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_mixer.py -v
```
Expected: `2 passed`

- [ ] **Step 5: Commit**

```bash
git add recorder/mixer.py tests/test_mixer.py
git commit -m "feat: WAV mixer with length-mismatch padding"
```

---

## Task 3: Whisper.cpp runner and transcript parser

**Files:**
- Create: `transcriber/whisper.py`
- Create: `tests/test_whisper.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_whisper.py
import json
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock


SAMPLE_WHISPER_JSON = {
    "transcription": [
        {"timestamps": {"from": "00:00:00,000", "to": "00:00:05,000"}, "text": " Hello everyone."},
        {"timestamps": {"from": "00:00:05,000", "to": "00:00:12,000"}, "text": " Let's get started."},
        {"timestamps": {"from": "00:01:30,000", "to": "00:01:35,000"}, "text": " Any questions?"},
    ]
}


def test_parse_whisper_json():
    from transcriber.whisper import parse_whisper_json
    lines = parse_whisper_json(SAMPLE_WHISPER_JSON)
    assert lines == [
        "[00:00] Hello everyone.",
        "[00:00] Let's get started.",
        "[01:30] Any questions?",
    ]


def test_transcribe_calls_subprocess(tmp_path):
    from transcriber.whisper import transcribe
    audio = tmp_path / "audio.wav"
    audio.touch()
    json_out = tmp_path / "audio.wav.json"
    json_out.write_text(json.dumps(SAMPLE_WHISPER_JSON))

    with patch("transcriber.whisper.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        result = transcribe(
            audio_path=audio,
            whisper_binary=Path("/usr/local/bin/whisper-cpp"),
            model="base",
        )

    mock_run.assert_called_once()
    call_args = mock_run.call_args[0][0]
    assert str(audio) in call_args
    assert "--output-json" in call_args
    assert lines_present(result)


def lines_present(lines):
    return len(lines) == 3 and lines[0].startswith("[00:00]")


def test_transcribe_raises_on_nonzero_exit(tmp_path):
    from transcriber.whisper import TranscriptionError
    from transcriber.whisper import transcribe
    audio = tmp_path / "audio.wav"
    audio.touch()

    with patch("transcriber.whisper.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stderr="error")
        with pytest.raises(TranscriptionError):
            transcribe(audio, Path("/usr/local/bin/whisper-cpp"), "base")

import pytest
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_whisper.py -v
```
Expected: `ImportError: cannot import name 'parse_whisper_json'`

- [ ] **Step 3: Write `transcriber/whisper.py`**

```python
# transcriber/whisper.py
import json
import subprocess
from pathlib import Path


class TranscriptionError(Exception):
    pass


def parse_whisper_json(data: dict) -> list[str]:
    """Parse whisper.cpp JSON output into '[MM:SS] text' lines."""
    lines = []
    for seg in data.get("transcription", []):
        timestamp = seg["timestamps"]["from"]  # "HH:MM:SS,mmm"
        h, m, rest = timestamp.split(":")
        s = rest.split(",")[0]
        total_minutes = int(h) * 60 + int(m)
        label = f"[{total_minutes:02d}:{s}]"
        text = seg["text"].strip()
        lines.append(f"{label} {text}")
    return lines


def transcribe(audio_path: Path, whisper_binary: Path, model: str) -> list[str]:
    """Run whisper.cpp on audio_path and return parsed transcript lines."""
    cmd = [
        str(whisper_binary),
        "--model", model,
        "--output-json",
        "--language", "auto",
        str(audio_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise TranscriptionError(f"whisper.cpp failed: {result.stderr}")

    json_path = Path(str(audio_path) + ".json")
    with open(json_path) as f:
        data = json.load(f)
    return parse_whisper_json(data)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_whisper.py -v
```
Expected: `3 passed`

- [ ] **Step 5: Commit**

```bash
git add transcriber/whisper.py tests/test_whisper.py
git commit -m "feat: whisper.cpp runner and transcript parser"
```

---

## Task 4: Ollama summarizer

**Files:**
- Create: `summarizer/ollama.py`
- Create: `tests/test_ollama.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ollama.py
import pytest
from unittest.mock import patch, MagicMock
import json


SAMPLE_OLLAMA_RESPONSE = """\
## TL;DR
- We agreed to ship the feature by Friday.
- John will follow up on API access.

## Topics Covered
- Sprint planning
- API access issue

## Key Decisions
- Ship by Friday

## Action Items
- John — follow up on API access by EOW
"""


def test_summarize_returns_text():
    from summarizer.ollama import summarize

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"response": SAMPLE_OLLAMA_RESPONSE}

    with patch("summarizer.ollama.requests.post", return_value=mock_response):
        result = summarize(
            transcript_lines=["[00:00] Hello.", "[00:01] Let's discuss the sprint."],
            model="llama3.2",
            host="http://localhost:11434",
        )

    assert "TL;DR" in result
    assert "Action Items" in result


def test_summarize_raises_on_connection_error():
    from summarizer.ollama import OllamaUnavailableError, summarize
    import requests

    with patch("summarizer.ollama.requests.post", side_effect=requests.ConnectionError):
        with pytest.raises(OllamaUnavailableError):
            summarize(["[00:00] Hello."], "llama3.2", "http://localhost:11434")
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_ollama.py -v
```
Expected: `ImportError: cannot import name 'summarize'`

- [ ] **Step 3: Write `summarizer/ollama.py`**

```python
# summarizer/ollama.py
import requests


class OllamaUnavailableError(Exception):
    pass


PROMPT_TEMPLATE = """\
Given this meeting transcript, produce:
1. A 2-3 sentence TL;DR
2. Topics covered
3. Key decisions made
4. Action items (person + task + deadline if mentioned)

Use markdown headings (## TL;DR, ## Topics Covered, ## Key Decisions, ## Action Items).
Format action items as: - Person — task by deadline

Transcript:
{transcript}
"""


def summarize(transcript_lines: list[str], model: str, host: str) -> str:
    """Send transcript to Ollama and return the summary text."""
    transcript = "\n".join(transcript_lines)
    prompt = PROMPT_TEMPLATE.format(transcript=transcript)
    try:
        response = requests.post(
            f"{host}/api/generate",
            json={"model": model, "prompt": prompt, "stream": False},
            timeout=120,
        )
    except requests.ConnectionError as e:
        raise OllamaUnavailableError(f"Cannot reach Ollama at {host}") from e

    response.raise_for_status()
    return response.json()["response"]
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_ollama.py -v
```
Expected: `2 passed`

- [ ] **Step 5: Commit**

```bash
git add summarizer/ollama.py tests/test_ollama.py
git commit -m "feat: Ollama summarizer with connection error handling"
```

---

## Task 5: Obsidian note writer

**Files:**
- Create: `notes/writer.py`
- Create: `tests/test_writer.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_writer.py
import pytest
from pathlib import Path
from datetime import datetime
import tempfile


def test_week_folder_name():
    from notes.writer import week_folder
    dt = datetime(2026, 4, 13, 14, 30)
    assert week_folder(dt) == "2026-W16"


def test_note_filename():
    from notes.writer import note_filename
    dt = datetime(2026, 4, 13, 14, 30)
    assert note_filename(dt) == "2026-04-13-14h30-meeting.md"


def test_note_content_structure():
    from notes.writer import format_note
    dt = datetime(2026, 4, 13, 14, 30)
    transcript_lines = ["[00:00] Hello.", "[00:01] Welcome everyone."]
    summary = "## TL;DR\n- Feature ships Friday.\n\n## Action Items\n- John — API access by EOW"
    duration_seconds = 2820  # 47 minutes

    content = format_note(dt, duration_seconds, summary, transcript_lines)

    assert "date: 2026-04-13" in content
    assert "time: 14:30" in content
    assert "duration: 47m" in content
    assert "## TL;DR" in content
    assert "## Full Transcript" in content
    assert "[00:00] Hello." in content


def test_write_note_creates_file():
    from notes.writer import write_note
    dt = datetime(2026, 4, 13, 14, 30)
    with tempfile.TemporaryDirectory() as d:
        output_dir = Path(d)
        path = write_note(
            dt=dt,
            duration_seconds=100,
            summary="## TL;DR\n- Test",
            transcript_lines=["[00:00] Hello."],
            output_dir=output_dir,
        )
        assert path.exists()
        assert path.parent.name == "2026-W16"
        assert path.name == "2026-04-13-14h30-meeting.md"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_writer.py -v
```
Expected: `ImportError: cannot import name 'week_folder'`

- [ ] **Step 3: Write `notes/writer.py`**

```python
# notes/writer.py
from datetime import datetime
from pathlib import Path


def week_folder(dt: datetime) -> str:
    """Return ISO week folder name, e.g. '2026-W16'."""
    year, week, _ = dt.isocalendar()
    return f"{year}-W{week:02d}"


def note_filename(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d-%Hh%M-meeting.md")


def format_note(
    dt: datetime,
    duration_seconds: int,
    summary: str,
    transcript_lines: list[str],
) -> str:
    duration_min = duration_seconds // 60
    transcript_block = "\n".join(f"**{line}**" if line.startswith("[") else line
                                  for line in transcript_lines)
    # Normalise transcript formatting
    transcript_block = "\n".join(
        f"**{line.split(']')[0]}]** {']'.join(line.split(']')[1:]).strip()}"
        if "]" in line else line
        for line in transcript_lines
    )
    return f"""---
date: {dt.strftime("%Y-%m-%d")}
time: {dt.strftime("%H:%M")}
duration: {duration_min}m
tags: [meeting, transcript]
---

# Meeting — {dt.strftime("%Y-%m-%d %H:%M")}

{summary}

## Full Transcript

{transcript_block}
"""


def write_note(
    dt: datetime,
    duration_seconds: int,
    summary: str,
    transcript_lines: list[str],
    output_dir: Path,
) -> Path:
    """Write the Obsidian note to the correct week folder. Returns the note path."""
    folder = output_dir / week_folder(dt)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / note_filename(dt)
    path.write_text(format_note(dt, duration_seconds, summary, transcript_lines))
    return path
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_writer.py -v
```
Expected: `4 passed`

- [ ] **Step 5: Commit**

```bash
git add notes/writer.py tests/test_writer.py
git commit -m "feat: Obsidian note writer with week folder organization"
```

---

## Task 6: Audio recorder

**Files:**
- Create: `recorder/audio.py`

> Note: This module wraps `sounddevice` which requires real hardware. Tests mock the sounddevice layer. Manual testing required to verify actual capture.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_audio.py
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock, call
import numpy as np
import tempfile


def test_recorder_writes_two_files(tmp_path):
    from recorder.audio import AudioRecorder

    mock_stream = MagicMock()
    mock_stream.__enter__ = MagicMock(return_value=mock_stream)
    mock_stream.__exit__ = MagicMock(return_value=False)

    with patch("recorder.audio.sf.SoundFile") as mock_sf, \
         patch("recorder.audio.sd.InputStream", return_value=mock_stream):
        recorder = AudioRecorder(
            mic_device="default",
            system_device="BlackHole 2ch",
            output_dir=tmp_path,
            session_name="2026-04-13-14h30",
        )
        recorder.start()
        recorder.stop()

    assert recorder.mic_path.name == "2026-04-13-14h30-audio-mic.wav"
    assert recorder.system_path.name == "2026-04-13-14h30-audio-system.wav"


def test_recorder_duration():
    from recorder.audio import AudioRecorder
    import time

    with patch("recorder.audio.sf.SoundFile"), \
         patch("recorder.audio.sd.InputStream"):
        recorder = AudioRecorder("default", "BlackHole 2ch", Path("/tmp"), "test")
        recorder._start_time = time.time() - 65
        assert recorder.elapsed_seconds() >= 65
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_audio.py -v
```
Expected: `ImportError: cannot import name 'AudioRecorder'`

- [ ] **Step 3: Add `soundfile` to requirements and install**

```
# Add to requirements.txt:
soundfile>=0.12.1
```

```bash
pip install soundfile
```

- [ ] **Step 4: Write `recorder/audio.py`**

```python
# recorder/audio.py
import queue
import threading
import time
from pathlib import Path

import numpy as np
import sounddevice as sd
import soundfile as sf

SAMPLE_RATE = 16000
CHANNELS = 1
DTYPE = "int16"
BLOCK_SIZE = 1024


class AudioRecorder:
    """Records mic and system audio simultaneously to separate WAV files."""

    def __init__(
        self,
        mic_device: str,
        system_device: str,
        output_dir: Path,
        session_name: str,
    ):
        self.mic_device = mic_device
        self.system_device = system_device
        self.mic_path = output_dir / f"{session_name}-audio-mic.wav"
        self.system_path = output_dir / f"{session_name}-audio-system.wav"
        self._mic_q: queue.Queue = queue.Queue()
        self._sys_q: queue.Queue = queue.Queue()
        self._stop_event = threading.Event()
        self._start_time: float | None = None

    def start(self) -> None:
        self._stop_event.clear()
        self._start_time = time.time()
        self._mic_stream = sd.InputStream(
            device=self.mic_device,
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype=DTYPE,
            blocksize=BLOCK_SIZE,
            callback=lambda d, f, t, s: self._mic_q.put(d.copy()),
        )
        self._sys_stream = sd.InputStream(
            device=self.system_device,
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype=DTYPE,
            blocksize=BLOCK_SIZE,
            callback=lambda d, f, t, s: self._sys_q.put(d.copy()),
        )
        self._mic_stream.start()
        self._sys_stream.start()
        self._mic_writer = threading.Thread(
            target=self._write_loop, args=(self._mic_q, self.mic_path), daemon=True
        )
        self._sys_writer = threading.Thread(
            target=self._write_loop, args=(self._sys_q, self.system_path), daemon=True
        )
        self._mic_writer.start()
        self._sys_writer.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._mic_stream.stop()
        self._sys_stream.stop()
        self._mic_writer.join(timeout=5)
        self._sys_writer.join(timeout=5)

    def elapsed_seconds(self) -> float:
        if self._start_time is None:
            return 0.0
        return time.time() - self._start_time

    def _write_loop(self, q: queue.Queue, path: Path) -> None:
        with sf.SoundFile(
            str(path), mode="w", samplerate=SAMPLE_RATE, channels=CHANNELS, subtype="PCM_16"
        ) as f:
            while not self._stop_event.is_set() or not q.empty():
                try:
                    data = q.get(timeout=0.1)
                    f.write(data)
                except queue.Empty:
                    continue
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
python -m pytest tests/test_audio.py -v
```
Expected: `2 passed`

- [ ] **Step 6: Commit**

```bash
git add recorder/audio.py tests/test_audio.py requirements.txt
git commit -m "feat: dual-stream audio recorder (mic + system)"
```

---

## Task 7: Pipeline processor

**Files:**
- Create: `pipeline/processor.py`
- Create: `tests/test_processor.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_processor.py
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock, call
from datetime import datetime
import tempfile


def test_pipeline_success_writes_note(tmp_path):
    from pipeline.processor import run_pipeline, PipelineResult

    with patch("pipeline.processor.mix_wavs") as mock_mix, \
         patch("pipeline.processor.transcribe", return_value=["[00:00] Hello."]) as mock_tr, \
         patch("pipeline.processor.summarize", return_value="## TL;DR\n- Done.") as mock_sum, \
         patch("pipeline.processor.write_note", return_value=tmp_path / "note.md") as mock_wn:

        result = run_pipeline(
            mic_path=tmp_path / "mic.wav",
            system_path=tmp_path / "sys.wav",
            session_dt=datetime(2026, 4, 13, 14, 30),
            duration_seconds=120,
            output_dir=tmp_path,
            whisper_binary=Path("/usr/local/bin/whisper-cpp"),
            whisper_model="base",
            ollama_model="llama3.2",
            ollama_host="http://localhost:11434",
            keep_audio=True,
        )

    assert result.success is True
    assert result.note_path == tmp_path / "note.md"
    mock_mix.assert_called_once()
    mock_tr.assert_called_once()
    mock_sum.assert_called_once()
    mock_wn.assert_called_once()


def test_pipeline_ollama_failure_saves_note_without_summary(tmp_path):
    from pipeline.processor import run_pipeline
    from summarizer.ollama import OllamaUnavailableError

    with patch("pipeline.processor.mix_wavs"), \
         patch("pipeline.processor.transcribe", return_value=["[00:00] Hello."]), \
         patch("pipeline.processor.summarize", side_effect=OllamaUnavailableError("down")), \
         patch("pipeline.processor.write_note", return_value=tmp_path / "note.md") as mock_wn:

        result = run_pipeline(
            mic_path=tmp_path / "mic.wav",
            system_path=tmp_path / "sys.wav",
            session_dt=datetime(2026, 4, 13, 14, 30),
            duration_seconds=120,
            output_dir=tmp_path,
            whisper_binary=Path("/usr/local/bin/whisper-cpp"),
            whisper_model="base",
            ollama_model="llama3.2",
            ollama_host="http://localhost:11434",
            keep_audio=True,
        )

    assert result.success is True
    # Note written with fallback summary containing warning
    _, kwargs = mock_wn.call_args
    assert "Summary unavailable" in kwargs.get("summary", "") or \
           "Summary unavailable" in mock_wn.call_args[0][2]


def test_pipeline_whisper_failure_writes_error_file(tmp_path):
    from pipeline.processor import run_pipeline
    from transcriber.whisper import TranscriptionError

    (tmp_path / "mic.wav").touch()
    (tmp_path / "sys.wav").touch()

    with patch("pipeline.processor.mix_wavs"), \
         patch("pipeline.processor.transcribe", side_effect=TranscriptionError("failed")):

        result = run_pipeline(
            mic_path=tmp_path / "mic.wav",
            system_path=tmp_path / "sys.wav",
            session_dt=datetime(2026, 4, 13, 14, 30),
            duration_seconds=120,
            output_dir=tmp_path,
            whisper_binary=Path("/usr/local/bin/whisper-cpp"),
            whisper_model="base",
            ollama_model="llama3.2",
            ollama_host="http://localhost:11434",
            keep_audio=True,
        )

    assert result.success is False
    error_files = list(tmp_path.rglob("*.error"))
    assert len(error_files) == 1
    assert "transcribe" in error_files[0].read_text()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_processor.py -v
```
Expected: `ImportError: cannot import name 'run_pipeline'`

- [ ] **Step 3: Write `pipeline/processor.py`**

```python
# pipeline/processor.py
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from notes.writer import note_filename, week_folder, write_note
from recorder.mixer import mix_wavs
from summarizer.ollama import OllamaUnavailableError, summarize
from transcriber.whisper import TranscriptionError, transcribe


@dataclass
class PipelineResult:
    success: bool
    note_path: Path | None = None
    error_stage: str | None = None
    error_message: str | None = None


def run_pipeline(
    mic_path: Path,
    system_path: Path,
    session_dt: datetime,
    duration_seconds: int,
    output_dir: Path,
    whisper_binary: Path,
    whisper_model: str,
    ollama_model: str,
    ollama_host: str,
    keep_audio: bool,
) -> PipelineResult:
    week_dir = output_dir / week_folder(session_dt)
    week_dir.mkdir(parents=True, exist_ok=True)
    session_name = session_dt.strftime("%Y-%m-%d-%Hh%M")
    mixed_path = week_dir / f"{session_name}-audio-mixed.wav"

    def write_error(stage: str, message: str) -> PipelineResult:
        error_path = week_dir / f"{session_name}-{stage}.error"
        error_path.write_text(f"stage: {stage}\nerror: {message}\n")
        return PipelineResult(success=False, error_stage=stage, error_message=message)

    # Move raw audio to week folder
    dest_mic = week_dir / mic_path.name
    dest_sys = week_dir / system_path.name
    if mic_path != dest_mic:
        shutil.move(str(mic_path), dest_mic)
    if system_path != dest_sys:
        shutil.move(str(system_path), dest_sys)

    # Mix
    try:
        mix_wavs(dest_mic, dest_sys, mixed_path)
    except Exception as e:
        return write_error("mix", str(e))

    # Transcribe
    try:
        transcript_lines = transcribe(mixed_path, whisper_binary, whisper_model)
    except TranscriptionError as e:
        return write_error("transcribe", str(e))

    # Summarize (non-fatal if Ollama down)
    try:
        summary = summarize(transcript_lines, ollama_model, ollama_host)
    except OllamaUnavailableError:
        summary = "⚠ Summary unavailable — Ollama was not reachable during processing."

    # Write note
    try:
        note_path = write_note(
            dt=session_dt,
            duration_seconds=duration_seconds,
            summary=summary,
            transcript_lines=transcript_lines,
            output_dir=output_dir,
        )
    except Exception as e:
        return write_error("write_note", str(e))

    # Clean up mixed audio if keep_audio is False
    if not keep_audio:
        mixed_path.unlink(missing_ok=True)
        dest_mic.unlink(missing_ok=True)
        dest_sys.unlink(missing_ok=True)

    return PipelineResult(success=True, note_path=note_path)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_processor.py -v
```
Expected: `3 passed`

- [ ] **Step 5: Commit**

```bash
git add pipeline/processor.py tests/test_processor.py
git commit -m "feat: pipeline processor with error file and Ollama fallback"
```

---

## Task 8: Menu bar UI

**Files:**
- Create: `ui/menu.py`
- Create: `app.py`

> `rumps` requires macOS and cannot be unit tested headlessly. Smoke-test manually. Write `app.py` last.

- [ ] **Step 1: Write `ui/menu.py`**

```python
# ui/menu.py
import threading
import time
from datetime import datetime
from pathlib import Path

import rumps

from config import Config
from pipeline.processor import run_pipeline
from recorder.audio import AudioRecorder


class MeetingRecorderApp(rumps.App):
    def __init__(self, config: Config):
        super().__init__("●", quit_button=None)
        self.config = config
        self._recorder: AudioRecorder | None = None
        self._recording = False
        self._timer_thread: threading.Thread | None = None
        self._session_dt: datetime | None = None

        self.menu = [
            rumps.MenuItem("Start Recording", callback=self.toggle_recording),
            rumps.MenuItem("Reprocess Last Meeting", callback=self.reprocess),
            rumps.MenuItem("Open Today's Note", callback=self.open_note),
            rumps.separator,
            rumps.MenuItem("Preferences", callback=self.open_prefs),
            rumps.MenuItem("Quit", callback=rumps.quit_application),
        ]
        self._set_idle()

    def _set_idle(self):
        self.title = "●"
        self.menu["Reprocess Last Meeting"].set_callback(
            self.reprocess if self._has_error_files() else None
        )

    def _has_error_files(self) -> bool:
        return bool(list(self.config.output_dir.rglob("*.error")))

    def toggle_recording(self, sender):
        if not self._recording:
            self._start_recording()
        else:
            self._stop_recording()

    def _start_recording(self):
        self._recording = True
        self._session_dt = datetime.now()
        session_name = self._session_dt.strftime("%Y-%m-%d-%Hh%M")

        # Use a temp dir; processor will move files to week folder
        import tempfile
        self._tmp_dir = Path(tempfile.mkdtemp())

        self._recorder = AudioRecorder(
            mic_device=self.config.mic_device,
            system_device=self.config.system_device,
            output_dir=self._tmp_dir,
            session_name=session_name,
        )
        self._recorder.start()
        self.menu["Start Recording"].title = "Stop Recording"
        self._timer_thread = threading.Thread(target=self._update_timer, daemon=True)
        self._timer_thread.start()

    def _stop_recording(self):
        self._recording = False
        self._recorder.stop()
        duration = int(self._recorder.elapsed_seconds())
        self.title = "● Processing..."
        self.menu["Start Recording"].title = "Start Recording"

        mic_path = self._recorder.mic_path
        sys_path = self._recorder.system_path
        session_dt = self._session_dt

        if duration < self.config.min_recording_seconds:
            rumps.notification("Meeting Recorder", "", "Recording too short — discarded.")
            self._set_idle()
            return

        threading.Thread(
            target=self._run_pipeline,
            args=(mic_path, sys_path, session_dt, duration),
            daemon=True,
        ).start()

    def _run_pipeline(self, mic_path, sys_path, session_dt, duration):
        result = run_pipeline(
            mic_path=mic_path,
            system_path=sys_path,
            session_dt=session_dt,
            duration_seconds=duration,
            output_dir=self.config.output_dir,
            whisper_binary=self.config.whisper_binary,
            whisper_model=self.config.whisper_model,
            ollama_model=self.config.ollama_model,
            ollama_host=self.config.ollama_host,
            keep_audio=self.config.keep_audio,
        )
        if result.success:
            self.title = "● Ready"
            rumps.notification("Meeting Recorder", "", f"Note saved: {result.note_path.name}")
            time.sleep(3)
            self._set_idle()
        else:
            self.title = "⚠ Error — click to view"
            rumps.notification(
                "Meeting Recorder", "Processing failed",
                f"Stage: {result.error_stage}. Click menu to reprocess."
            )

    def _update_timer(self):
        while self._recording:
            elapsed = int(self._recorder.elapsed_seconds())
            m, s = divmod(elapsed, 60)
            self.title = f"● Recording... ({m:02d}:{s:02d})"

            # Disk space check
            import shutil
            stat = shutil.disk_usage(self.config.output_dir)
            free_mb = stat.free // (1024 * 1024)
            if free_mb < self.config.low_disk_threshold_mb:
                self._stop_recording()
                rumps.notification("Meeting Recorder", "Disk space low", "Recording stopped.")
                break
            time.sleep(1)

    def reprocess(self, _):
        error_files = list(self.config.output_dir.rglob("*.error"))
        if not error_files:
            return
        # Find most recent
        error_file = sorted(error_files)[-1]
        session_name = error_file.stem.rsplit("-", 1)[0]
        week_dir = error_file.parent
        mic_path = week_dir / f"{session_name}-audio-mic.wav"
        sys_path = week_dir / f"{session_name}-audio-system.wav"
        # Parse datetime from session name: YYYY-MM-DD-HHhMM
        dt = datetime.strptime(session_name, "%Y-%m-%d-%Hh%M")
        error_file.unlink()
        self.title = "● Processing..."
        threading.Thread(
            target=self._run_pipeline,
            args=(mic_path, sys_path, dt, 0),
            daemon=True,
        ).start()

    def open_note(self, _):
        import subprocess
        today = datetime.now()
        from notes.writer import week_folder, note_filename
        note = self.config.output_dir / week_folder(today) / note_filename(today)
        if note.exists():
            subprocess.run(["open", str(note)])
        else:
            rumps.notification("Meeting Recorder", "", "No note found for today.")

    def open_prefs(self, _):
        import subprocess
        subprocess.run(["open", str(Path("config.toml").resolve())])
```

- [ ] **Step 2: Write `app.py`**

```python
# app.py
from pathlib import Path
from config import load_config
from ui.menu import MeetingRecorderApp

if __name__ == "__main__":
    config = load_config(Path(__file__).parent / "config.toml")
    config.output_dir.mkdir(parents=True, exist_ok=True)
    MeetingRecorderApp(config).run()
```

- [ ] **Step 3: Smoke test manually**

```bash
python app.py
```
Expected: Menu bar icon `●` appears. Menu opens with "Start Recording", "Reprocess Last Meeting" (greyed out), "Open Today's Note", "Preferences", "Quit".

- [ ] **Step 4: Commit**

```bash
git add ui/menu.py app.py
git commit -m "feat: rumps menu bar UI with recording toggle, timer, and pipeline dispatch"
```

---

## Task 9: Setup guide and `.gitignore`

**Files:**
- Create: `setup-guide.md`
- Create: `.gitignore`

- [ ] **Step 1: Write `setup-guide.md`**

```markdown
# Meeting Recorder — Setup Guide

## 1. Install BlackHole 2ch

Download from: https://existential.audio/blackhole/
Run the installer. No reboot needed.

## 2. Configure Multi-Output Device

1. Open **Audio MIDI Setup** (Applications > Utilities)
2. Click **+** at bottom left > **Create Multi-Output Device**
3. Check both **BlackHole 2ch** and your speakers/headphones
4. Right-click the new device > **Use This Device for Sound Output**

Now Teams audio goes to your speakers AND gets captured by BlackHole.

## 3. Install whisper.cpp

```bash
git clone https://github.com/ggerganov/whisper.cpp
cd whisper.cpp
make
# Download a model:
bash ./models/download-ggml-model.sh large-v3
```

Note the path to the `whisper-cli` binary and update `config.toml`:
```toml
[whisper]
binary = "/path/to/whisper.cpp/whisper-cli"
model = "large-v3"
```

## 4. Install and start Ollama

Download from: https://ollama.com
```bash
ollama serve          # start the server
ollama pull llama3.2  # or whichever model you configured
```

## 5. Install Python dependencies

```bash
pip install -r requirements.txt
```

## 6. Configure the app

Edit `config.toml` to set your output directory and devices.

## 7. Run

```bash
python app.py
```
```

- [ ] **Step 2: Write `.gitignore`**

```
__pycache__/
*.py[cod]
*.wav
*.error
.env
*.egg-info/
dist/
build/
.DS_Store
```

- [ ] **Step 3: Commit**

```bash
git add setup-guide.md .gitignore
git commit -m "docs: setup guide and gitignore"
```

---

## Task 10: Full test suite run and validation

- [ ] **Step 1: Run all tests**

```bash
python -m pytest tests/ -v
```
Expected: all tests pass. No failures, no errors.

- [ ] **Step 2: Verify project structure**

```bash
find . -type f -name "*.py" | sort
```
Expected output:
```
./app.py
./config.py
./notes/__init__.py
./notes/writer.py
./pipeline/__init__.py
./pipeline/processor.py
./recorder/__init__.py
./recorder/audio.py
./recorder/mixer.py
./summarizer/__init__.py
./summarizer/ollama.py
./transcriber/__init__.py
./transcriber/whisper.py
./tests/__init__.py
./tests/test_audio.py
./tests/test_config.py
./tests/test_mixer.py
./tests/test_ollama.py
./tests/test_processor.py
./tests/test_writer.py
./ui/__init__.py
./ui/menu.py
```

- [ ] **Step 3: Final commit**

```bash
git add -A
git commit -m "chore: verified full test suite passes"
```
