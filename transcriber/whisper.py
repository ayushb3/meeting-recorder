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
        s = rest.split(",")[0]  # e.g. "05"
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
    if not json_path.exists():
        raise TranscriptionError(f"whisper.cpp produced no output file at {json_path}")
    with open(json_path) as f:
        data = json.load(f)
    return parse_whisper_json(data)
