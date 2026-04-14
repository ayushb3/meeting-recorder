# transcriber/whisper.py
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path


class TranscriptionError(Exception):
    pass


@dataclass
class Segment:
    start_seconds: float
    text: str
    source: str  # "system" or "mic"


def parse_whisper_json(data: dict, source: str = "system") -> list[Segment]:
    """Parse whisper.cpp JSON output into Segment objects."""
    segments = []
    for seg in data.get("transcription", []):
        timestamp = seg["timestamps"]["from"]  # "HH:MM:SS,mmm"
        h, m, rest = timestamp.split(":")
        s, ms = rest.split(",")
        start_seconds = int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000
        text = seg["text"].strip()
        if text:
            segments.append(Segment(start_seconds=start_seconds, text=text, source=source))
    return segments


def _segments_to_lines(segments: list[Segment]) -> list[str]:
    """Format segments as '[MM:SS] text' lines, prefixing mic-only with '(you)'."""
    lines = []
    for seg in segments:
        total_seconds = int(seg.start_seconds)
        m, s = divmod(total_seconds, 60)
        label = f"[{m:02d}:{s:02d}]"
        prefix = "(you) " if seg.source == "mic" else ""
        lines.append(f"{label} {prefix}{seg.text}")
    return lines


def _similarity(a: str, b: str) -> float:
    """Simple token overlap similarity between two strings (0.0–1.0)."""
    tokens_a = set(a.lower().split())
    tokens_b = set(b.lower().split())
    if not tokens_a or not tokens_b:
        return 0.0
    return len(tokens_a & tokens_b) / max(len(tokens_a), len(tokens_b))


def merge_transcripts(
    system_segments: list[Segment],
    mic_segments: list[Segment],
    similarity_threshold: float = 0.85,
    time_window_seconds: float = 2.0,
) -> list[str]:
    """
    Merge system and mic transcripts into a single sorted transcript.

    Mic segments that closely match a system segment within the time window
    are treated as speaker bleed and discarded. Surviving mic segments
    (unique to mic = your voice on headphones) are kept and labeled '(you)'.
    """
    surviving_mic = []
    for mic_seg in mic_segments:
        is_bleed = False
        for sys_seg in system_segments:
            if abs(mic_seg.start_seconds - sys_seg.start_seconds) <= time_window_seconds:
                if _similarity(mic_seg.text, sys_seg.text) >= similarity_threshold:
                    is_bleed = True
                    break
        if not is_bleed:
            surviving_mic.append(mic_seg)

    merged = sorted(system_segments + surviving_mic, key=lambda s: s.start_seconds)
    return _segments_to_lines(merged)


def transcribe_raw(audio_path: Path, whisper_binary: Path, model: str, source: str) -> list[Segment]:
    """Run whisper.cpp and return raw Segment objects."""
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
    return parse_whisper_json(data, source=source)


def transcribe(audio_path: Path, whisper_binary: Path, model: str) -> list[str]:
    """Run whisper.cpp on a single file and return formatted '[MM:SS] text' lines."""
    segments = transcribe_raw(audio_path, whisper_binary, model, source="system")
    return _segments_to_lines(segments)
