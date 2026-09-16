# tests/test_menu_logic.py — pure-logic tests for ui/menu.py helpers
# These test module-level functions that have no rumps dependency at runtime.
from datetime import date, datetime
from pathlib import Path

import pytest


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
    folder = tmp_path / "2026-09-16" / "2026-W38" / "2026-09-16-13h32-shared-service-onboarding-birva-1-1"
    folder.mkdir(parents=True)
    note = folder / "meeting.md"
    note.write_text("---\ndate: 2026-09-16\ntime: 13:32\n---\n")
    entry = {"title": "", "path": note, "time": "13:32", "date": "2026-09-16"}
    result = display_label(entry)
    assert result == "Shared Service Onboarding Birva 1 1"


def test_display_label_bare_timestamp_folder_with_h1(tmp_path):
    """Bare YYYY-MM-DD-HHhMM folder: fall back to H1 heading in note body."""
    from ui.menu import display_label
    folder = tmp_path / "2026-09-16-14h00"
    folder.mkdir()
    note = folder / "meeting.md"
    note.write_text("---\ndate: 2026-09-16\ntime: 14:00\n---\n\n# Design Review\n\nBody.\n")
    entry = {"title": "", "path": note, "time": "14:00", "date": "2026-09-16"}
    result = display_label(entry)
    assert result == "Design Review"


def test_display_label_bare_timestamp_folder_no_h1(tmp_path):
    """Bare YYYY-MM-DD-HHhMM folder with no H1: return the folder name."""
    from ui.menu import display_label
    folder = tmp_path / "2026-09-16-15h30"
    folder.mkdir()
    note = folder / "meeting.md"
    note.write_text("---\ndate: 2026-09-16\ntime: 15:30\n---\n\nJust some body text.\n")
    entry = {"title": "", "path": note, "time": "15:30", "date": "2026-09-16"}
    result = display_label(entry)
    assert result == "2026-09-16-15h30"


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


# ---------------------------------------------------------------------------
# group_notes_for_menu
# ---------------------------------------------------------------------------

def _entry(date_str: str, time_str: str = "09:00", title: str = "Meeting", path: Path | None = None):
    return {"date": date_str, "time": time_str, "title": title, "path": path, "degraded": False}


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
    # Today section present, no Earlier section
    assert len(sections) == 1
    assert len(sections[0]["entries"]) == 1


def test_group_notes_empty_today_section_always_present():
    from ui.menu import group_notes_for_menu
    today = date(2026, 9, 16)
    notes = [_entry("2026-09-15", "09:00", "Yesterday")]
    sections = group_notes_for_menu(notes, today=today)
    # Today section present but empty
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
