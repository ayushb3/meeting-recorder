"""_repo_path finds scripts/ and .venv/, which are deliberately never bundled.

A frozen app's __file__ lives under _MEIPASS, where those paths do not exist,
so resolving from __file__ made the shell-out menu items always report the
script as missing.
"""
import os
from pathlib import Path
from unittest.mock import patch

from ui.menu import _repo_path


def test_resolves_from_source_when_not_frozen():
    # Running from the checkout, __file__ is already in the right place.
    assert _repo_path("scripts/stream_transcript.py").endswith(
        "meeting-recorder/scripts/stream_transcript.py"
    )


def test_env_override_wins_when_it_exists(tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "stream_transcript.py").write_text("")
    with patch.dict(os.environ, {"MEETING_RECORDER_REPO": str(tmp_path)}):
        assert _repo_path("scripts/stream_transcript.py") == str(
            tmp_path / "scripts" / "stream_transcript.py"
        )


def test_env_override_ignored_when_path_missing(tmp_path):
    # A stale override must not shadow a working checkout.
    with patch.dict(os.environ, {"MEETING_RECORDER_REPO": str(tmp_path / "nope")}):
        assert Path(_repo_path("scripts/stream_transcript.py")).exists()


def test_frozen_uses_recorded_build_root(tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "stream_transcript.py").write_text("")
    with patch("ui.menu.sys.frozen", True, create=True), \
         patch("ui.menu._BUILT_FROM_REPO", str(tmp_path)):
        assert _repo_path("scripts/stream_transcript.py") == str(
            tmp_path / "scripts" / "stream_transcript.py"
        )


def test_returns_a_path_even_when_nothing_exists():
    # The caller reports a missing file; the resolver never raises.
    result = _repo_path("scripts/does-not-exist.py")
    assert result.endswith("scripts/does-not-exist.py")
