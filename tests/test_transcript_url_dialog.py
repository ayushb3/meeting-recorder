import pytest

from ui.transcript_url_dialog import looks_like_stream_url, normalise_url, normalise_frames_at


class TestNormaliseUrl:
    def test_plain_url(self):
        assert normalise_url("https://example.com/x") == "https://example.com/x"

    def test_strips_surrounding_whitespace(self):
        assert normalise_url("  https://example.com/x \n") == "https://example.com/x"

    def test_strips_angle_brackets(self):
        # Mail and chat clients wrap pasted links like this.
        assert normalise_url("<https://example.com/x>") == "https://example.com/x"

    def test_strips_quotes(self):
        assert normalise_url('"https://example.com/x"') == "https://example.com/x"

    def test_keeps_query_string(self):
        url = "https://example.com/stream.aspx?id=abc&referrer=Web"
        assert normalise_url(url) == url

    def test_http_is_allowed(self):
        assert normalise_url("http://example.com") == "http://example.com"

    @pytest.mark.parametrize("raw", [
        None, "", "   ", "not a url", "example.com", "ftp://example.com",
        "javascript:alert(1)", "file:///etc/passwd",
    ])
    def test_rejects_non_http_urls(self, raw):
        assert normalise_url(raw) is None


class TestLooksLikeStreamUrl:
    def test_stream_aspx(self):
        assert looks_like_stream_url("https://x.sharepoint.com/_layouts/15/stream.aspx?id=1")

    def test_sharepoint_host(self):
        assert looks_like_stream_url("https://contoso-my.sharepoint.com/personal/a/x")

    def test_case_insensitive(self):
        assert looks_like_stream_url("https://X.SharePoint.com/Stream.aspx")

    def test_unrelated_url(self):
        # Advisory only — the caller warns but still proceeds.
        assert not looks_like_stream_url("https://example.com/video")


class TestNormaliseFramesAt:
    # ---- valid inputs ----

    def test_none_returns_none(self):
        assert normalise_frames_at(None) is None

    def test_empty_string_returns_none(self):
        assert normalise_frames_at("") is None

    def test_blank_string_returns_none(self):
        assert normalise_frames_at("   ") is None

    def test_single_seconds(self):
        assert normalise_frames_at("466") == "466"

    def test_single_m_ss(self):
        assert normalise_frames_at("7:46") == "7:46"

    def test_single_h_mm_ss(self):
        assert normalise_frames_at("1:07:46") == "1:07:46"

    def test_multiple_timestamps_m_ss(self):
        assert normalise_frames_at("7:46, 19:40") == "7:46,19:40"

    def test_strips_spaces_around_commas(self):
        assert normalise_frames_at("  7:46 , 19:40 ") == "7:46,19:40"

    def test_mixed_formats(self):
        assert normalise_frames_at("60, 7:46, 1:07:46") == "60,7:46,1:07:46"

    def test_trailing_comma_ignored(self):
        # A trailing comma produces an empty piece which is silently dropped.
        assert normalise_frames_at("7:46,") == "7:46"

    def test_only_commas_returns_none(self):
        assert normalise_frames_at(",,,") is None

    # ---- invalid inputs ----

    def test_raises_on_non_numeric(self):
        with pytest.raises(ValueError, match="Invalid timestamp"):
            normalise_frames_at("abc")

    def test_raises_on_m_ss_with_too_many_parts(self):
        # Four colon-separated parts is not a valid timestamp
        with pytest.raises(ValueError, match="Invalid timestamp"):
            normalise_frames_at("1:2:3:4")

    def test_raises_on_negative_looking_value(self):
        # Negative seconds are not valid timestamps
        with pytest.raises(ValueError):
            normalise_frames_at("-5")

    def test_error_message_names_the_bad_piece(self):
        with pytest.raises(ValueError) as exc_info:
            normalise_frames_at("7:46, badvalue, 19:40")
        assert "badvalue" in str(exc_info.value)


class TestFramesEvery:
    @pytest.mark.parametrize("raw,expected", [
        ("every 10", "every 10"), ("Every 10s", "every 10"), ("  every   30 ", "every 30"),
    ])
    def test_every_is_normalised(self, raw, expected):
        from ui.transcript_url_dialog import normalise_frames_at
        assert normalise_frames_at(raw) == expected

    def test_every_zero_is_rejected(self):
        from ui.transcript_url_dialog import normalise_frames_at
        with pytest.raises(ValueError):
            normalise_frames_at("every 0")

    def test_cli_flags(self):
        from ui.transcript_url_dialog import frames_cli_flag
        assert frames_cli_flag("every 10") == ["--frames-every", "10"]
        assert frames_cli_flag("7:46,19:40") == ["--frames-at", "7:46,19:40"]
        assert frames_cli_flag(None) == []


def test_dialog_window_is_not_released_when_closed():
    """Regression: the Stream dialog crashed the app on close (SIGSEGV in
    _NSWindowTransformAnimation dealloc) because the window was released on close
    while Python still held it. Every dialog window must opt out."""
    import pathlib
    import re

    src = pathlib.Path(__file__).parent.parent / "ui"
    for name in ("transcript_url_dialog", "import_recording_dialog", "stop_dialog", "settings_window"):
        text = (src / f"{name}.py").read_text()
        assert re.search(r"setReleasedWhenClosed_\(\s*False\s*\)", text), name
