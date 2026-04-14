from datetime import datetime
from pathlib import Path


def week_folder(dt: datetime) -> str:
    """Return ISO week folder name, e.g. '2026-W16'."""
    year, week, _ = dt.isocalendar()
    return f"{year}-W{week:02d}"


def note_filename(dt: datetime) -> str:
    """Return the note filename. When inside a session folder, just 'meeting.md'."""
    return "meeting.md"


def format_note(
    dt: datetime,
    duration_seconds: int,
    summary: str,
    transcript_lines: list[str],
    audio_files: list[Path] | None = None,
) -> str:
    duration_min = duration_seconds // 60
    transcript_block = "\n".join(transcript_lines)
    audio_block = ""
    if audio_files:
        embeds = "\n".join(f"![[{f.name}]]" for f in audio_files if f.exists())
        if embeds:
            audio_block = f"\n## Audio\n\n{embeds}\n"
    return f"""---
date: {dt.strftime("%Y-%m-%d")}
time: {dt.strftime("%H:%M")}
duration: {duration_min}m
tags: [meeting, transcript]
---

# Meeting — {dt.strftime("%Y-%m-%d %H:%M")}

{summary}
{audio_block}
## Full Transcript

{transcript_block}
"""


def write_note(
    dt: datetime,
    duration_seconds: int,
    summary: str,
    transcript_lines: list[str],
    output_dir: Path,
    audio_files: list[Path] | None = None,
) -> Path:
    """Write the Obsidian note into output_dir. Returns the note path."""
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / note_filename(dt)
    if path.exists():
        raise FileExistsError(f"Note already exists: {path}")
    path.write_text(format_note(dt, duration_seconds, summary, transcript_lines, audio_files), encoding="utf-8")
    return path
