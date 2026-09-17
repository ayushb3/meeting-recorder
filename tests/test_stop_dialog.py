# tests/test_stop_dialog.py
"""Unit tests for pure helpers in ui/stop_dialog.py.

AppKit window construction is NOT tested here (requires a running event loop
and macOS display server). Only module-level pure functions are exercised.
"""


# ---------------------------------------------------------------------------
# normalise_text_input
# ---------------------------------------------------------------------------

def test_normalise_blank_string_returns_none():
    from ui.stop_dialog import normalise_text_input
    assert normalise_text_input("") is None


def test_normalise_whitespace_only_returns_none():
    from ui.stop_dialog import normalise_text_input
    assert normalise_text_input("   ") is None


def test_normalise_none_returns_none():
    from ui.stop_dialog import normalise_text_input
    assert normalise_text_input(None) is None


def test_normalise_strips_whitespace():
    from ui.stop_dialog import normalise_text_input
    assert normalise_text_input("  Hello  ") == "Hello"


def test_normalise_preserves_internal_whitespace():
    from ui.stop_dialog import normalise_text_input
    result = normalise_text_input("  Q2 planning with design team  ")
    assert result == "Q2 planning with design team"


def test_normalise_returns_stripped_string():
    from ui.stop_dialog import normalise_text_input
    assert normalise_text_input("My Meeting") == "My Meeting"


def test_normalise_multiline_non_blank():
    from ui.stop_dialog import normalise_text_input
    result = normalise_text_input("line1\nline2\n")
    assert result == "line1\nline2"


def test_normalise_newlines_only_returns_none():
    from ui.stop_dialog import normalise_text_input
    assert normalise_text_input("\n\n\n") is None


def test_normalise_tab_only_returns_none():
    from ui.stop_dialog import normalise_text_input
    assert normalise_text_input("\t") is None
