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
