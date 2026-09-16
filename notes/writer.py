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
    meeting_name: str | None = None,
) -> str:
    duration_min = duration_seconds // 60
    transcript_block = "\n".join(transcript_lines)
    audio_block = ""
    if audio_files:
        embeds = "\n".join(f"![[{f.name}]]" for f in audio_files if f.exists())
        if embeds:
            audio_block = f"\n## Audio\n\n{embeds}\n"
    title = meeting_name if meeting_name else f"Meeting {dt.strftime('%Y-%m-%d %H:%M')}"
    return f"""---
date: {dt.strftime("%Y-%m-%d")}
time: {dt.strftime("%H:%M")}
duration: {duration_min}m
tags: [meeting, transcript]
title: {title}
---

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
    overwrite: bool = False,
    meeting_name: str | None = None,
) -> Path:
    """Write the Obsidian note into output_dir. Returns the note path."""
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / note_filename(dt)
    if path.exists() and not overwrite:
        raise FileExistsError(f"Note already exists: {path}")
    path.write_text(
        format_note(dt, duration_seconds, summary, transcript_lines, audio_files, meeting_name),
        encoding="utf-8",
    )
    return path


def _frontmatter(note_path: Path) -> dict[str, str]:
    """Parse the simple `key: value` frontmatter block at the top of a note."""
    fields: dict[str, str] = {}
    try:
        lines = note_path.read_text().splitlines()
    except OSError:
        return fields
    if not lines or lines[0].strip() != "---":
        return fields
    for line in lines[1:]:
        if line.strip() == "---":
            break
        key, sep, value = line.partition(":")
        if sep:
            fields[key.strip()] = value.strip()
    return fields


def find_notes_for_date(output_dir: Path, dt: datetime) -> list[Path]:
    """Return every note recorded on dt's date, oldest first.

    Looks at the note's own frontmatter rather than its folder name: session
    folders get renamed to a title slug once the meeting has one, so the folder
    name carries no date for any successfully-named meeting.
    """
    week_dir = output_dir / week_folder(dt)
    if not week_dir.exists():
        return []
    target = dt.strftime("%Y-%m-%d")
    dated: list[tuple[str, Path]] = []
    for note in week_dir.glob("*/meeting.md"):
        fields = _frontmatter(note)
        if fields.get("date") == target:
            dated.append((fields.get("time", ""), note))
    return [note for _, note in sorted(dated, key=lambda pair: pair[0])]
