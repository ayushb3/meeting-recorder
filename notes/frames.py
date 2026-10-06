"""Embed captured video frames into a meeting transcript.

Shared by the Stream scraper (scripts/, not bundled into the app) and the local
import pipeline, so both place frames the same way.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path


def frame_filename(seconds: int) -> str:
    """Name by timestamp, not ordinal — survives a re-run with different steps."""
    minutes, secs = divmod(int(seconds), 60)
    return f"frame-{minutes:02d}{secs:02d}.png"


def interleave_frames(lines: list[str], frames: list[tuple[int, str]]) -> list[str]:
    """Insert Obsidian embeds into the transcript at the points they belong to.

    *frames* is (seconds, filename). A frame is emitted before the first
    transcript line at or after its timestamp, so the note reads as
    slide -> discussion -> slide. Frames past the last line land at the end.
    """
    if not frames:
        return lines

    def line_seconds(line: str) -> int | None:
        match = re.match(r"\[(\d+):(\d\d)\]", line)
        if not match:
            return None
        return int(match.group(1)) * 60 + int(match.group(2))

    pending = sorted(frames)
    out: list[str] = []
    for line in lines:
        current = line_seconds(line)
        while pending and current is not None and pending[0][0] <= current:
            _, name = pending.pop(0)
            out.extend([f"![[{name}]]", ""])
        out.append(line)
    for _, name in pending:
        out.extend(["", f"![[{name}]]"])
    return out


def place_frames(
    frames: list[tuple[int, str]], frames_dir: Path, session_dir: Path
) -> list[tuple[int, str]]:
    """Copy frames into the session folder so Obsidian embeds resolve.

    Returns the (seconds, filename) pairs that actually landed. A frame that is
    missing or cannot be copied is reported and skipped, not fatal.
    """
    landed: list[tuple[int, str]] = []
    for seconds, name in frames:
        source = frames_dir / name
        if not source.exists():
            continue
        try:
            shutil.copy2(source, session_dir / name)
            landed.append((seconds, name))
        except OSError as exc:
            print(f"  could not place {name}: {exc}")
    return landed
