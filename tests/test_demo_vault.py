# tests/test_demo_vault.py — unit tests for scripts/demo_vault.py pure logic
import sys
from pathlib import Path

# Make scripts/ importable
_SCRIPTS = Path(__file__).parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))


# ---------------------------------------------------------------------------
# _is_real_data
# ---------------------------------------------------------------------------

def test_is_real_data_absent_dir(tmp_path):
    from demo_vault import _is_real_data
    absent = tmp_path / "not_there"
    assert _is_real_data(absent) is False


def test_is_real_data_empty_dir(tmp_path):
    from demo_vault import _is_real_data
    empty = tmp_path / "empty"
    empty.mkdir()
    assert _is_real_data(empty) is False


def test_is_real_data_non_empty_dir(tmp_path):
    from demo_vault import _is_real_data
    non_empty = tmp_path / "has_files"
    non_empty.mkdir()
    (non_empty / "something.md").write_text("hi")
    assert _is_real_data(non_empty) is True


def test_is_real_data_nested_file(tmp_path):
    from demo_vault import _is_real_data
    nested = tmp_path / "vault"
    (nested / "week" / "session").mkdir(parents=True)
    (nested / "week" / "session" / "meeting.md").write_text("---\n---\n")
    assert _is_real_data(nested) is True


# ---------------------------------------------------------------------------
# _build_dt
# ---------------------------------------------------------------------------

def test_build_dt_today(freezer=None):
    from datetime import date, datetime
    from demo_vault import _build_dt
    dt = _build_dt(days_ago=0, hour=9, minute=30)
    assert dt.date() == date.today()
    assert dt.hour == 9
    assert dt.minute == 30


def test_build_dt_days_ago():
    from datetime import date, timedelta
    from demo_vault import _build_dt
    dt = _build_dt(days_ago=3, hour=14, minute=0)
    expected_date = date.today() - timedelta(days=3)
    assert dt.date() == expected_date
    assert dt.hour == 14


# ---------------------------------------------------------------------------
# _session_folder_name
# ---------------------------------------------------------------------------

def test_session_folder_name_format():
    from datetime import datetime
    from demo_vault import _session_folder_name
    dt = datetime(2026, 9, 16, 9, 5)
    assert _session_folder_name(dt) == "2026-09-16-09h05"


def test_session_folder_name_padding():
    from datetime import datetime
    from demo_vault import _session_folder_name
    dt = datetime(2026, 1, 7, 8, 3)
    assert _session_folder_name(dt) == "2026-01-07-08h03"


# ---------------------------------------------------------------------------
# generate_demo_vault — integration (writes real files to tmp_path)
# ---------------------------------------------------------------------------

def test_generate_creates_notes(tmp_path):
    """generate_demo_vault produces meeting.md files inside week-dated directories."""
    from demo_vault import generate_demo_vault
    generate_demo_vault(tmp_path)
    notes = list(tmp_path.rglob("meeting.md"))
    assert len(notes) >= 4, f"Expected at least 4 notes, got {len(notes)}"


def test_generate_creates_degraded_session(tmp_path):
    """At least one session has both meeting.md and a *.error file."""
    from demo_vault import generate_demo_vault
    generate_demo_vault(tmp_path)
    degraded = [
        d for d in tmp_path.rglob("*.error")
        if (d.parent / "meeting.md").exists()
    ]
    assert len(degraded) >= 1, "Expected at least one degraded session"


def test_generate_creates_error_only_session(tmp_path):
    """At least one session has a *.error file but no meeting.md."""
    from demo_vault import generate_demo_vault
    generate_demo_vault(tmp_path)
    error_only = [
        d for d in tmp_path.rglob("*.error")
        if not (d.parent / "meeting.md").exists()
    ]
    assert len(error_only) >= 1, "Expected at least one error-only session"


def test_generate_notes_have_correct_frontmatter(tmp_path):
    """Generated notes contain the expected frontmatter keys."""
    from demo_vault import generate_demo_vault
    generate_demo_vault(tmp_path)
    notes = list(tmp_path.rglob("meeting.md"))
    assert notes, "No notes generated"
    text = notes[0].read_text()
    assert "date:" in text
    assert "time:" in text
    assert "duration:" in text
    assert "tags:" in text
    assert "title:" in text


def test_generate_notes_are_listable_by_list_notes(tmp_path):
    """list_notes() can read the generated vault and returns entries."""
    from demo_vault import generate_demo_vault
    generate_demo_vault(tmp_path)

    # Add repo root so notes.writer is importable
    import sys
    repo_root = Path(__file__).parent.parent
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    from notes.writer import list_notes
    entries = list_notes(tmp_path, weeks=4)
    assert len(entries) >= 4
    titles = {e["title"] for e in entries}
    assert "Weekly Standup" in titles
    assert "Design Review" in titles


def test_generate_today_entries_are_grouped_correctly(tmp_path):
    """group_notes_for_menu puts today's meetings in the Today section."""
    from demo_vault import generate_demo_vault
    generate_demo_vault(tmp_path)

    import sys
    repo_root = Path(__file__).parent.parent
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    from notes.writer import list_notes
    from ui.menu import group_notes_for_menu
    from datetime import date

    entries = list_notes(tmp_path, weeks=4)
    sections = group_notes_for_menu(entries, today=date.today())

    today_section = sections[0]
    assert today_section["label"].startswith("Today")
    today_titles = {e["title"] for e in today_section["entries"]}
    assert "Weekly Standup" in today_titles
    assert "Design Review" in today_titles


# ---------------------------------------------------------------------------
# Safety guard — main() refuses non-empty dir without --force
# ---------------------------------------------------------------------------

def test_main_refuses_non_empty_without_force(tmp_path):
    from demo_vault import main
    # Put a file in tmp_path so it counts as non-empty
    (tmp_path / "existing.txt").write_text("real data")
    rc = main([str(tmp_path)])
    assert rc != 0, "Expected non-zero exit code when target is non-empty"


def test_main_accepts_non_empty_with_force(tmp_path):
    from demo_vault import main
    (tmp_path / "existing.txt").write_text("real data")
    rc = main([str(tmp_path), "--force"])
    assert rc == 0


def test_main_accepts_absent_dir(tmp_path):
    from demo_vault import main
    absent = tmp_path / "brand_new"
    rc = main([str(absent)])
    assert rc == 0
    assert absent.exists()


def test_main_refuses_second_run_without_force(tmp_path):
    """Running twice without --force should fail on the second run."""
    from demo_vault import main
    target = tmp_path / "demo"
    assert main([str(target)]) == 0
    assert main([str(target)]) != 0


def test_main_second_run_with_force_succeeds(tmp_path):
    from demo_vault import main
    target = tmp_path / "demo"
    assert main([str(target)]) == 0
    assert main([str(target), "--force"]) == 0
