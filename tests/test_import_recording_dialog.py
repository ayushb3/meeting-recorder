# tests/test_import_recording_dialog.py
"""Tests for the pure-helper functions in ui/import_recording_dialog.py.

AppKit code is never called; only the module-level testable functions are
exercised here.
"""
from datetime import datetime

from ui.import_recording_dialog import (
    format_dt_for_field,
    parse_dt_from_field,
    validate_dt_field,
)


# ---------------------------------------------------------------------------
# format_dt_for_field
# ---------------------------------------------------------------------------

def test_format_dt_for_field_roundtrips():
    dt = datetime(2026, 9, 17, 14, 35)
    assert format_dt_for_field(dt) == "2026-09-17 14:35"


def test_format_dt_for_field_zero_padded():
    dt = datetime(2026, 1, 5, 9, 3)
    assert format_dt_for_field(dt) == "2026-01-05 09:03"


# ---------------------------------------------------------------------------
# parse_dt_from_field
# ---------------------------------------------------------------------------

def test_parse_dt_from_field_valid():
    result = parse_dt_from_field("2026-09-17 14:35")
    assert result == datetime(2026, 9, 17, 14, 35)


def test_parse_dt_from_field_strips_whitespace():
    result = parse_dt_from_field("  2026-09-17 14:35  ")
    assert result == datetime(2026, 9, 17, 14, 35)


def test_parse_dt_from_field_none_input():
    assert parse_dt_from_field(None) is None


def test_parse_dt_from_field_empty_string():
    assert parse_dt_from_field("") is None


def test_parse_dt_from_field_blank_whitespace():
    assert parse_dt_from_field("   ") is None


def test_parse_dt_from_field_wrong_format():
    assert parse_dt_from_field("17/09/2026 14:35") is None


def test_parse_dt_from_field_date_only():
    assert parse_dt_from_field("2026-09-17") is None


def test_parse_dt_from_field_invalid_month():
    assert parse_dt_from_field("2026-13-01 10:00") is None


# ---------------------------------------------------------------------------
# validate_dt_field
# ---------------------------------------------------------------------------

def test_validate_dt_field_valid():
    dt, err = validate_dt_field("2026-09-17 14:35")
    assert dt == datetime(2026, 9, 17, 14, 35)
    assert err is None


def test_validate_dt_field_empty_returns_error():
    dt, err = validate_dt_field("")
    assert dt is None
    assert err is not None
    assert "YYYY-MM-DD" in err


def test_validate_dt_field_none_returns_error():
    dt, err = validate_dt_field(None)
    assert dt is None
    assert err is not None


def test_validate_dt_field_wrong_format_returns_error():
    dt, err = validate_dt_field("September 17 2026")
    assert dt is None
    assert err is not None
    assert "YYYY-MM-DD" in err


# ---------------------------------------------------------------------------
# parse_frames_every
# ---------------------------------------------------------------------------

import pytest

from ui.import_recording_dialog import parse_frames_every


@pytest.mark.parametrize("raw", [None, "", "   "])
def test_parse_frames_every_blank_is_off(raw):
    assert parse_frames_every(raw) is None


def test_parse_frames_every_number():
    assert parse_frames_every(" 10 ") == 10


@pytest.mark.parametrize("raw", ["0", "-5", "ten", "1.5", "10s"])
def test_parse_frames_every_rejects_bad_input(raw):
    with pytest.raises(ValueError):
        parse_frames_every(raw)
