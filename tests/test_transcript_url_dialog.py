import pytest

from ui.transcript_url_dialog import looks_like_stream_url, normalise_url


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
