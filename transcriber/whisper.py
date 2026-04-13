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
        s_raw = int(rest.split(",")[0])
        total_minutes = int(h) * 60 + int(m)
        s = (s_raw // 10) * 10
        label = f"[{total_minutes:02d}:{s:02d}]"
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
