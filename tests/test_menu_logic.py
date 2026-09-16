# tests/test_menu_logic.py — pure-logic tests for ui/menu.py helpers
# These test module-level functions that have no rumps dependency at runtime.
from datetime import date, datetime
from pathlib import Path


# ---------------------------------------------------------------------------
# display_label
# ---------------------------------------------------------------------------

def test_display_label_uses_frontmatter_title(tmp_path):
    """When frontmatter title is present, use it as-is."""
    from ui.menu import display_label
    note = tmp_path / "meeting.md"
    note.write_text("---\ntitle: Q2 Planning\n---\n")
    entry = {"title": "Q2 Planning", "path": note, "time": "09:00", "date": "2026-09-16"}
    assert display_label(entry) == "Q2 Planning"


def test_display_label_slug_with_timestamp_prefix(tmp_path):
    """Folder YYYY-MM-DD-HHhMM-<slug>: strip prefix, title-case the slug."""
    from ui.menu import display_label
    folder = tmp_path / "2026-09-16-13h32-shared-service-onboarding-birva-1-1"
    folder.mkdir(parents=True)
    note = folder / "meeting.md"
    note.write_text("---\ndate: 2026-09-16\ntime: 13:32\n---\n")
    entry = {"title": "", "path": note, "time": "13:32", "date": "2026-09-16"}
    assert display_label(entry) == "Shared Service Onboarding Birva 1 1"


def test_display_label_bare_timestamp_folder_with_h1(tmp_path):
    """Bare YYYY-MM-DD-HHhMM folder: fall back to H1 heading in note body."""
    from ui.menu import display_label
    folder = tmp_path / "2026-09-16-14h00"
    folder.mkdir()
    note = folder / "meeting.md"
    note.write_text("---\ndate: 2026-09-16\ntime: 14:00\n---\n\n# Design Review\n\nBody.\n")
    entry = {"title": "", "path": note, "time": "14:00", "date": "2026-09-16"}
    assert display_label(entry) == "Design Review"


def test_display_label_bare_timestamp_folder_no_h1(tmp_path):
    """Bare YYYY-MM-DD-HHhMM folder with no H1: return the folder name."""
    from ui.menu import display_label
    folder = tmp_path / "2026-09-16-15h30"
    folder.mkdir()
    note = folder / "meeting.md"
    note.write_text("---\ndate: 2026-09-16\ntime: 15:30\n---\n\nJust some body text.\n")
    entry = {"title": "", "path": note, "time": "15:30", "date": "2026-09-16"}
    assert display_label(entry) == "2026-09-16-15h30"


def test_display_label_no_path():
    """When path is None and title is empty, return 'Untitled'."""
    from ui.menu import display_label
    entry = {"title": "", "path": None, "time": "09:00", "date": "2026-09-16"}
    assert display_label(entry) == "Untitled"


def test_display_label_prefers_title_over_slug(tmp_path):
    """frontmatter title wins over slug even when folder has a slug."""
    from ui.menu import display_label
    folder = tmp_path / "2026-09-16-09h00-standup"
    folder.mkdir()
    note = folder / "meeting.md"
    note.write_text("---\ntitle: Daily Standup\n---\n")
    entry = {"title": "Daily Standup", "path": note, "time": "09:00", "date": "2026-09-16"}
    assert display_label(entry) == "Daily Standup"


def test_display_label_degraded_entry_uses_readable(tmp_path):
    """display_label returns the readable label regardless of degraded flag."""
    from ui.menu import display_label
    folder = tmp_path / "2026-09-16-10h00-team-sync"
    folder.mkdir()
    note = folder / "meeting.md"
    note.write_text("---\ndate: 2026-09-16\ntime: 10:00\n---\n")
    entry = {"title": "", "path": note, "time": "10:00", "date": "2026-09-16", "degraded": True}
    assert display_label(entry) == "Team Sync"


# ---------------------------------------------------------------------------
# group_notes_for_menu
# ---------------------------------------------------------------------------

def _entry(date_str: str, time_str: str = "09:00", title: str = "Meeting",
           path: Path | None = None, degraded: bool = False):
    return {"date": date_str, "time": time_str, "title": title, "path": path, "degraded": degraded}


def test_group_notes_today_and_earlier():
    from ui.menu import group_notes_for_menu
    today = date(2026, 9, 16)
    notes = [
        _entry("2026-09-16", "09:00", "Standup"),
        _entry("2026-09-16", "11:00", "FDE Roadshow"),
        _entry("2026-09-15", "10:00", "Design Review"),
    ]
    sections = group_notes_for_menu(notes, today=today)
    assert len(sections) == 2
    assert sections[0]["label"].startswith("Today")
    assert len(sections[0]["entries"]) == 2
    assert sections[1]["label"] == "Earlier this week"
    assert len(sections[1]["entries"]) == 1


def test_group_notes_today_only():
    from ui.menu import group_notes_for_menu
    today = date(2026, 9, 16)
    notes = [_entry("2026-09-16", "14:00", "Only Meeting")]
    sections = group_notes_for_menu(notes, today=today)
    assert len(sections) == 1
    assert len(sections[0]["entries"]) == 1


def test_group_notes_empty_today_section_always_present():
    from ui.menu import group_notes_for_menu
    today = date(2026, 9, 16)
    notes = [_entry("2026-09-15", "09:00", "Yesterday")]
    sections = group_notes_for_menu(notes, today=today)
    today_sections = [s for s in sections if s["label"].startswith("Today")]
    assert today_sections
    assert len(today_sections[0]["entries"]) == 0


def test_group_notes_today_label_format():
    from ui.menu import group_notes_for_menu
    today = date(2026, 9, 16)
    sections = group_notes_for_menu([], today=today)
    # 2026-09-16 is a Wednesday
    assert sections[0]["label"] == "Today — Wed 16 Sep"


def test_group_notes_no_notes():
    from ui.menu import group_notes_for_menu
    today = date(2026, 9, 16)
    sections = group_notes_for_menu([], today=today)
    assert len(sections) == 1
    assert sections[0]["entries"] == []


def test_group_notes_degraded_entry_in_today():
    from ui.menu import group_notes_for_menu
    today = date(2026, 9, 16)
    notes = [_entry("2026-09-16", "15:00", "", degraded=True)]
    sections = group_notes_for_menu(notes, today=today)
    assert sections[0]["entries"][0]["degraded"] is True


# ---------------------------------------------------------------------------
# resolve_session_dt
# ---------------------------------------------------------------------------

def test_resolve_session_dt_timestamp_prefix(tmp_path):
    """Standard YYYY-MM-DD-HHhMM folder parses correctly."""
    from ui.menu import resolve_session_dt
    d = tmp_path / "2026-09-16-14h35"
    d.mkdir()
    dt = resolve_session_dt(d)
    assert dt == datetime(2026, 9, 16, 14, 35)


def test_resolve_session_dt_timestamp_plus_slug(tmp_path):
    """YYYY-MM-DD-HHhMM-slug folder: prefix parse succeeds."""
    from ui.menu import resolve_session_dt
    d = tmp_path / "2026-09-16-09h00-daily-standup"
    d.mkdir()
    dt = resolve_session_dt(d)
    assert dt == datetime(2026, 9, 16, 9, 0)


def test_resolve_session_dt_frontmatter_fallback(tmp_path):
    """Bare-slug folder (no timestamp prefix): fall back to note frontmatter."""
    from ui.menu import resolve_session_dt
    d = tmp_path / "q2-planning-session"
    d.mkdir()
    note = d / "meeting.md"
    note.write_text("---\ndate: 2026-08-20\ntime: 11:30\n---\n")
    dt = resolve_session_dt(d)
    assert dt == datetime(2026, 8, 20, 11, 30)


def test_resolve_session_dt_mtime_fallback(tmp_path):
    """Bare-slug folder with no note: fall back to directory mtime."""
    from ui.menu import resolve_session_dt
    d = tmp_path / "some-meeting-no-note"
    d.mkdir()
    # Just verify it doesn't raise and returns a datetime
    dt = resolve_session_dt(d)
    assert isinstance(dt, datetime)


def test_resolve_session_dt_missing_dir_does_not_raise(tmp_path):
    """Non-existent directory: stat fails, should return datetime.now()-ish without raising."""
    from ui.menu import resolve_session_dt
    d = tmp_path / "completely-absent"
    # d does not exist — stat() will fail, should fall through to datetime.now()
    dt = resolve_session_dt(d)
    assert isinstance(dt, datetime)


# ---------------------------------------------------------------------------
# _find_error_only_dirs
# ---------------------------------------------------------------------------

def _make_week_dir(base: Path, week: str) -> Path:
    d = base / week
    d.mkdir(parents=True, exist_ok=True)
    return d


def test_find_error_only_dirs_empty(tmp_path):
    from ui.menu import _find_error_only_dirs
    assert _find_error_only_dirs(tmp_path) == []


def test_find_error_only_dirs_detects_erroronly(tmp_path):
    """Session with only a .error file (no meeting.md) should be returned."""
    from ui.menu import _find_error_only_dirs
    week = _make_week_dir(tmp_path, "2026-W38")
    sess = week / "2026-09-16-10h00"
    sess.mkdir()
    (sess / "transcribe.error").write_text("stage: transcribe\nerror: something\n")
    result = _find_error_only_dirs(tmp_path)
    assert sess in result


def test_find_error_only_dirs_ignores_session_with_note(tmp_path):
    """Session with both .error AND meeting.md should NOT be returned (list_notes handles it)."""
    from ui.menu import _find_error_only_dirs
    week = _make_week_dir(tmp_path, "2026-W38")
    sess = week / "2026-09-16-14h00-standup"
    sess.mkdir()
    (sess / "summarize.error").write_text("stage: summarize\nerror: ollama down\n")
    (sess / "meeting.md").write_text("---\ndate: 2026-09-16\ntime: 14:00\n---\n")
    result = _find_error_only_dirs(tmp_path)
    assert sess not in result


def test_find_error_only_dirs_nonexistent_output_dir(tmp_path):
    """Non-existent output_dir should return empty list without raising."""
    from ui.menu import _find_error_only_dirs
    missing = tmp_path / "does_not_exist"
    assert _find_error_only_dirs(missing) == []
