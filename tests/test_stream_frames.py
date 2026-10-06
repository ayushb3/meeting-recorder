"""Frame capture for Stream recordings — timestamp parsing and note interleaving.

Playwright never runs here; the page object is patched and the calls asserted.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from stream_transcript import (  # noqa: E402
    capture_frames,
    frame_filename,
    interleave_frames,
    parse_timestamps,
)


class TestParseTimestamps:
    def test_mm_ss(self):
        assert parse_timestamps("7:46") == [466]

    def test_several(self):
        assert parse_timestamps("7:46,19:40,24:36") == [466, 1180, 1476]

    def test_h_mm_ss(self):
        assert parse_timestamps("1:02:15") == [3735]

    def test_bare_seconds(self):
        assert parse_timestamps("90") == [90]

    def test_sorted_and_deduplicated(self):
        assert parse_timestamps("19:40,7:46,7:46") == [466, 1180]

    def test_tolerates_spaces_and_empties(self):
        assert parse_timestamps(" 7:46 , , 19:40 ") == [466, 1180]

    @pytest.mark.parametrize("spec", [None, "", "   ", ","])
    def test_empty_specs_yield_nothing(self, spec):
        assert parse_timestamps(spec) == []

    @pytest.mark.parametrize("spec", ["banana", "7:xx", "1:2:3:4", "7;46", "-5"])
    def test_rejects_garbage(self, spec):
        with pytest.raises(ValueError, match="Not a timestamp"):
            parse_timestamps(spec)

    def test_rejects_past_the_end(self):
        # Seeking past the end yields the final frame for every such entry,
        # which looks like a working capture and is not.
        with pytest.raises(ValueError, match="past the end"):
            parse_timestamps("10:00", duration=300)

    def test_allows_exactly_the_duration(self):
        assert parse_timestamps("5:00", duration=300) == [300]


class TestFrameFilename:
    def test_named_by_timestamp_not_ordinal(self):
        # A re-run with different steps must not renumber existing frames.
        assert frame_filename(466) == "frame-0746.png"

    def test_pads_single_digits(self):
        assert frame_filename(3) == "frame-0003.png"

    def test_past_an_hour_keeps_counting_minutes(self):
        assert frame_filename(3735) == "frame-6215.png"


class TestInterleaveFrames:
    def test_no_frames_returns_lines_unchanged(self):
        # The regression that matters most: no frames, no behaviour change.
        lines = ["[00:03] Ana: Hello.", "[00:20] Bob: Hi."]
        assert interleave_frames(lines, []) == lines

    def test_frame_lands_before_the_line_it_precedes(self):
        lines = ["[00:03] Ana: Hello.", "[00:20] Bob: Look at this."]
        out = interleave_frames(lines, [(20, "frame-0020.png")])
        assert out == [
            "[00:03] Ana: Hello.",
            "![[frame-0020.png]]",
            "",
            "[00:20] Bob: Look at this.",
        ]

    def test_frame_before_the_first_line(self):
        out = interleave_frames(["[01:00] Ana: Late."], [(0, "frame-0000.png")])
        assert out[0] == "![[frame-0000.png]]"

    def test_frame_past_the_last_line_lands_at_the_end(self):
        out = interleave_frames(["[00:03] Ana: Hi."], [(600, "frame-1000.png")])
        assert out[-1] == "![[frame-1000.png]]"

    def test_several_frames_in_order(self):
        lines = ["[00:10] A: one", "[00:30] B: two", "[00:50] C: three"]
        frames = [(30, "frame-0030.png"), (10, "frame-0010.png")]
        out = interleave_frames(lines, frames)
        assert out.index("![[frame-0010.png]]") < out.index("![[frame-0030.png]]")
        assert out.index("![[frame-0030.png]]") < out.index("[00:30] B: two")

    def test_lines_without_timestamps_are_passed_through(self):
        lines = ["## Heading", "[00:30] A: hi"]
        out = interleave_frames(lines, [(30, "f.png")])
        assert "## Heading" in out and "![[f.png]]" in out


class TestCaptureFrames:
    def _page(self):
        page = MagicMock()
        page.evaluate.return_value = {"ready": 4, "seeking": False}
        return page

    def test_no_timestamps_captures_nothing(self, tmp_path):
        page = self._page()
        assert capture_frames(page, [], tmp_path, 0) == []
        page.locator.assert_not_called()

    def test_one_capture_per_timestamp(self, tmp_path):
        page = self._page()

        def write(path):
            Path(path).write_bytes(b"\x89PNG fake")

        page.locator.return_value.first.screenshot.side_effect = lambda path: write(path)
        result = capture_frames(page, [10, 20], tmp_path, 0)
        assert result == [(10, "frame-0010.png"), (20, "frame-0020.png")]
        assert (tmp_path / "frame-0010.png").exists()

    def test_seeks_to_each_timestamp(self, tmp_path):
        page = self._page()
        page.locator.return_value.first.screenshot.side_effect = (
            lambda path: Path(path).write_bytes(b"x")
        )
        capture_frames(page, [90], tmp_path, 0)
        seeks = [c for c in page.evaluate.call_args_list if len(c.args) > 1 and c.args[1] == 90]
        assert seeks, "expected a seek to 90s"

    def test_empty_file_is_not_reported_as_captured(self, tmp_path):
        # A black or zero-byte capture must not look like a success.
        page = self._page()
        page.locator.return_value.first.screenshot.side_effect = (
            lambda path: Path(path).write_bytes(b"")
        )
        assert capture_frames(page, [10], tmp_path, 0) == []

    def test_a_failed_capture_does_not_abort_the_rest(self, tmp_path):
        page = self._page()
        calls = {"n": 0}

        def flaky(path):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("compositor said no")
            Path(path).write_bytes(b"x")

        page.locator.return_value.first.screenshot.side_effect = flaky
        assert capture_frames(page, [10, 20], tmp_path, 0) == [(20, "frame-0020.png")]


# ---------------------------------------------------------------------------
# --frames-every
# ---------------------------------------------------------------------------

from stream_transcript import dedupe_captured, interval_timestamps  # noqa: E402


class TestIntervalTimestamps:
    def test_every_n_stops_before_the_end(self):
        assert interval_timestamps(95, 30) == [0, 30, 60, 90]

    def test_long_recording_interval_is_raised(self):
        from pipeline.frames import MAX_FRAMES

        stamps = interval_timestamps(MAX_FRAMES * 20, 1)
        assert len(stamps) <= MAX_FRAMES
        assert stamps[1] == 20

    @pytest.mark.parametrize("duration", [0, None])
    def test_unknown_duration_is_an_error(self, duration):
        with pytest.raises(ValueError, match="frames-at"):
            interval_timestamps(duration, 10)


def test_dedupe_captured_deletes_dropped_files(tmp_path):
    from PIL import Image

    names = []
    for seconds, value in [(0, 20), (10, 20), (20, 220)]:
        name = f"frame-{seconds // 60:02d}{seconds % 60:02d}.png"
        Image.new("L", (64, 64), value).save(tmp_path / name)
        names.append((seconds, name))

    kept = dedupe_captured(names, tmp_path)

    assert kept == [(0, "frame-0000.png"), (20, "frame-0020.png")]
    assert not (tmp_path / "frame-0010.png").exists()


def test_frames_at_and_frames_every_are_mutually_exclusive(capsys):
    from stream_transcript import main

    code = main(["https://example.invalid/x", "--frames-at", "1:00", "--frames-every", "10"])
    captured = capsys.readouterr()
    assert code != 0
    assert "not both" in captured.err + captured.out
