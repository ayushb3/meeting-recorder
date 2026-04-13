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
    transcript_block = "\n".join(transcript_lines)
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
