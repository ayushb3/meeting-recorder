# tests/test_importer.py — tests for pipeline/importer.py
"""Tests for audio preparation helpers.

All ffprobe/ffmpeg calls are mocked so no real binaries are required.
"""
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pipeline.importer import (
    FFMPEG_DEFAULT,
    FFPROBE_DEFAULT,
    AudioImportError,
    get_duration_seconds,
    prepare_audio,
)


# ---------------------------------------------------------------------------
# get_duration_seconds
# ---------------------------------------------------------------------------

def test_get_duration_returns_int(tmp_path):
    source = tmp_path / "talk.mp4"
    source.touch()

    with patch("pipeline.importer.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="3661.5\n", stderr="")
        result = get_duration_seconds(source)

    assert result == 3661
    mock_run.assert_called_once()
    args = mock_run.call_args[0][0]
    assert str(FFPROBE_DEFAULT) in args
    assert str(source) in args


def test_get_duration_strips_whitespace(tmp_path):
    source = tmp_path / "clip.m4a"
    source.touch()

    with patch("pipeline.importer.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="  120.0  \n", stderr="")
        result = get_duration_seconds(source)

    assert result == 120


def test_get_duration_raises_on_nonzero_exit(tmp_path):
    source = tmp_path / "bad.mp3"
    source.touch()

    with patch("pipeline.importer.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="moov atom not found")
        with pytest.raises(AudioImportError, match="ffprobe returned 1"):
            get_duration_seconds(source)


def test_get_duration_raises_on_unparseable_output(tmp_path):
    source = tmp_path / "weird.wav"
    source.touch()

    with patch("pipeline.importer.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="N/A\n", stderr="")
        with pytest.raises(AudioImportError, match="unparseable duration"):
            get_duration_seconds(source)


def test_get_duration_raises_when_ffprobe_missing(tmp_path):
    source = tmp_path / "file.wav"
    source.touch()

    with patch("pipeline.importer.subprocess.run", side_effect=FileNotFoundError("no ffprobe")):
        with pytest.raises(AudioImportError, match="not found or failed to start"):
            get_duration_seconds(source)


# ---------------------------------------------------------------------------
# prepare_audio — video / non-WAV input routes through ffmpeg
# ---------------------------------------------------------------------------

def _ffmpeg_creates_output(out_path: Path):
    """Return a subprocess.run side-effect function that creates out_path."""
    def _side(cmd, **kwargs):
        out = Path(cmd[-1])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.touch()
        return MagicMock(returncode=0, stdout="", stderr="")
    return _side


def test_prepare_audio_mp4_converts_to_wav(tmp_path):
    """An .mp4 file (non-WAV) goes directly to ffmpeg — no ffprobe stream check."""
    source = tmp_path / "recording.mp4"
    source.touch()
    dest_dir = tmp_path / "session"

    with patch("pipeline.importer.subprocess.run") as mock_run:
        def _side(cmd, **kwargs):
            # The only call should be the ffmpeg conversion
            out = Path(cmd[-1])
            out.parent.mkdir(parents=True, exist_ok=True)
            out.touch()
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = _side
        result = prepare_audio(source, dest_dir)

    assert result.suffix == ".wav"
    assert result.parent == dest_dir
    assert result.exists()
    # Only one subprocess call: ffmpeg (no _is_16k_mono_wav check for non-WAV)
    assert mock_run.call_count == 1
    ffmpeg_cmd = mock_run.call_args[0][0]
    assert str(FFMPEG_DEFAULT) in ffmpeg_cmd


def test_prepare_audio_wav_already_16k_mono_skips_conversion(tmp_path):
    """A 16 kHz mono PCM WAV is returned as-is without calling ffmpeg."""
    source = tmp_path / "audio-mic.wav"
    source.touch()
    dest_dir = tmp_path / "session"

    with patch("pipeline.importer.subprocess.run") as mock_run:
        # _is_16k_mono_wav ffprobe call returns pcm_s16le 16000 mono
        mock_run.return_value = MagicMock(
            returncode=0, stdout="pcm_s16le,16000,1\n", stderr=""
        )
        result = prepare_audio(source, dest_dir)

    # Should return the original path unchanged
    assert result == source
    # Only one call: the ffprobe stream check (no ffmpeg)
    assert mock_run.call_count == 1


def test_prepare_audio_wav_wrong_rate_converts(tmp_path):
    """A WAV at 44100 Hz (not 16 kHz) is converted."""
    source = tmp_path / "stereo-44k.wav"
    source.touch()
    dest_dir = tmp_path / "session"

    call_count = [0]

    def _side(cmd, **kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            # _is_16k_mono_wav: wrong rate
            return MagicMock(returncode=0, stdout="pcm_s16le,44100,2\n", stderr="")
        # ffmpeg conversion
        out = Path(cmd[-1])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.touch()
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("pipeline.importer.subprocess.run", side_effect=_side):
        result = prepare_audio(source, dest_dir)

    assert result.suffix == ".wav"
    assert result.parent == dest_dir
    assert call_count[0] == 2  # ffprobe + ffmpeg


def test_prepare_audio_m4a_routes_through_ffmpeg(tmp_path):
    """An .m4a (non-WAV) goes directly to ffmpeg, no ffprobe stream check."""
    source = tmp_path / "voice-memo.m4a"
    source.touch()
    dest_dir = tmp_path / "conv"

    with patch("pipeline.importer.subprocess.run") as mock_run:
        def _side(cmd, **kwargs):
            out = Path(cmd[-1])
            out.parent.mkdir(parents=True, exist_ok=True)
            out.touch()
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = _side
        result = prepare_audio(source, dest_dir)

    assert result.suffix == ".wav"
    assert result.parent == dest_dir
    assert mock_run.call_count == 1  # only ffmpeg


def test_prepare_audio_raises_when_source_missing(tmp_path):
    source = tmp_path / "ghost.mp4"  # does NOT exist
    dest_dir = tmp_path / "session"

    with pytest.raises(AudioImportError, match="Source file not found"):
        prepare_audio(source, dest_dir)


def test_prepare_audio_raises_when_ffmpeg_returns_nonzero(tmp_path):
    """ffmpeg exit code != 0 raises AudioImportError."""
    source = tmp_path / "broken.mp4"
    source.touch()
    dest_dir = tmp_path / "session"

    with patch("pipeline.importer.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="Error: invalid data")
        with pytest.raises(AudioImportError, match="ffmpeg returned 1"):
            prepare_audio(source, dest_dir)


def test_prepare_audio_raises_when_ffmpeg_missing(tmp_path):
    source = tmp_path / "file.mp4"
    source.touch()
    dest_dir = tmp_path / "session"

    with patch("pipeline.importer.subprocess.run", side_effect=FileNotFoundError("no ffmpeg")):
        with pytest.raises(AudioImportError, match="not found or failed to start"):
            prepare_audio(source, dest_dir)
