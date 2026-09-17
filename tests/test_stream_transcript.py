import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from stream_transcript import (  # noqa: E402
    clean_text,
    find_gaps,
    format_lines,
    is_system_notice,
    parse_label,
)


class TestParseLabel:
    def test_surname_comma_firstname_is_one_name(self):
        # Splitting on the comma would mangle this.
        assert parse_label("Okafor, Dara 0 minutes 3 seconds") == ("Okafor, Dara", 3)

    def test_minutes_and_seconds(self):
        assert parse_label("Lindqvist, Mira 12 minutes 34 seconds") == ("Lindqvist, Mira", 754)

    def test_singular_units(self):
        assert parse_label("Someone 1 minute 1 second") == ("Someone", 61)

    def test_hours_are_carried(self):
        # A meeting over an hour would otherwise silently wrap to 05:02.
        assert parse_label("Someone 1 hours 5 minutes 2 seconds") == ("Someone", 3902)

    def test_seconds_only(self):
        assert parse_label("Someone 45 seconds") == ("Someone", 45)

    def test_minutes_only_when_seconds_are_zero(self):
        # Observed live: at exactly 18:00 the panel omits the seconds entirely.
        # Requiring seconds silently dropped 7 real lines from a 263-row scrape.
        assert parse_label("Okafor, Dara 18 minutes") == ("Okafor, Dara", 1080)

    def test_hours_only(self):
        assert parse_label("Someone 2 hours") == ("Someone", 7200)

    def test_hours_and_minutes_no_seconds(self):
        assert parse_label("Someone 1 hours 30 minutes") == ("Someone", 5400)

    def test_name_with_multiple_commas(self):
        assert parse_label("Smith, John, Jr. 2 minutes 0 seconds") == ("Smith, John, Jr.", 120)

    @pytest.mark.parametrize("label", [None, "", " ", "   "])
    def test_blank_label_returns_none(self, label):
        # The system notice carries a blank aria-label.
        assert parse_label(label) == (None, None)

    def test_label_with_no_time_unit_returns_none(self):
        # Every unit is optional, so guard against matching an empty suffix.
        assert parse_label("Bob Jones started transcription") == (None, None)

    def test_timestamp_with_no_speaker_returns_none(self):
        assert parse_label("5 minutes 3 seconds") == (None, None)


class TestCleanText:
    def test_strips_duplicated_label(self):
        label = "Okafor, Dara 0 minutes 3 seconds"
        raw = "Okafor, Dara\n0 minutes 3 seconds0:03\nOkafor, Dara 0 minutes 3 seconds\nThank you."
        assert clean_text(raw, label) == "Thank you."

    def test_keeps_speaker_name_inside_the_utterance(self):
        # A blind replace would turn this into "is right." — the speaker's own
        # name is legitimate content when it appears mid-sentence.
        label = "Ana, Bob 5 minutes 0 seconds"
        raw = "Ana, Bob 5 minutes 0 seconds Ana, Bob is right about that."
        assert clean_text(raw, label) == "Ana, Bob is right about that."

    def test_keeps_another_persons_name(self):
        label = "Okafor, Dara 18 minutes"
        raw = "Okafor, Dara 18 minutes Thank you, Sam."
        assert clean_text(raw, label) == "Thank you, Sam."

    def test_collapses_whitespace(self):
        assert clean_text("some   spaced\n\ntext", None) == "some spaced text"

    def test_survives_missing_label(self):
        assert clean_text("Plain text.", None) == "Plain text."


class TestFormatLines:
    def test_orders_by_posinset_not_dict_order(self):
        rows = {
            3: ("B 0 minutes 20 seconds", "third"),
            1: ("A 0 minutes 3 seconds", "first"),
            2: ("A 0 minutes 11 seconds", "second"),
        }
        lines, dropped = format_lines(rows)
        assert lines == [
            "[00:03] A: first",
            "[00:11] A: second",
            "[00:20] B: third",
        ]
        assert dropped == []

    def test_reports_system_notice_as_dropped(self):
        rows = {
            1: (" ", "Bob started transcription"),
            2: ("Ana 0 minutes 3 seconds", "Hello."),
        }
        lines, dropped = format_lines(rows)
        assert lines == ["[00:03] Ana: Hello."]
        assert dropped == [1]

    def test_minute_boundary_line_is_not_dropped(self):
        # The regression: these parsed as None and vanished without a trace.
        rows = {1: ("Okafor, Dara 18 minutes", "Thank you, Sam.")}
        lines, dropped = format_lines(rows)
        assert lines == ["[18:00] Okafor, Dara: Thank you, Sam."]
        assert dropped == []

    def test_over_an_hour_does_not_wrap(self):
        rows = {1: ("Ana 1 hours 2 minutes 5 seconds", "Late.")}
        lines, _ = format_lines(rows)
        assert lines == ["[62:05] Ana: Late."]

    def test_empty_input(self):
        assert format_lines({}) == ([], [])


class TestIsSystemNotice:
    @pytest.mark.parametrize("raw", [
        "Lindqvist, Mira started transcription",
        "Ferreira, Luis stopped transcription",
    ])
    def test_blank_label_with_transcription_text(self, raw):
        assert is_system_notice((" ", raw)) is True

    def test_real_row_is_not_a_notice(self):
        # A dropped row with a real label is a parse failure, not a notice.
        assert is_system_notice(("Ana 5 minutes", "Some words.")) is False

    def test_blank_label_but_unrelated_text_is_not_a_notice(self):
        assert is_system_notice((" ", "Some other text")) is False


class TestFindGaps:
    def test_no_gaps(self):
        assert find_gaps({1, 2, 3}, 3) == []

    def test_single_missing(self):
        assert find_gaps({1, 3}, 3) == [(2, 2)]

    def test_contiguous_run(self):
        assert find_gaps({1, 5}, 5) == [(2, 4)]

    def test_multiple_runs(self):
        assert find_gaps({1, 4, 7}, 8) == [(2, 3), (5, 6), (8, 8)]

    def test_missing_tail(self):
        # The failure mode that matters: a sweep that stopped early.
        assert find_gaps({1, 2}, 10) == [(3, 10)]

    def test_nothing_collected(self):
        assert find_gaps(set(), 3) == [(1, 3)]
