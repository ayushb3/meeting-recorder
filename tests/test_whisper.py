import json
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

SAMPLE_WHISPER_JSON = {
    "transcription": [
        {"timestamps": {"from": "00:00:00,000", "to": "00:00:05,000"}, "text": " Hello everyone."},
        {"timestamps": {"from": "00:00:05,000", "to": "00:00:12,000"}, "text": " Let's get started."},
        {"timestamps": {"from": "00:01:30,000", "to": "00:01:35,000"}, "text": " Any questions?"},
    ]
}


def test_parse_whisper_json():
    from transcriber.whisper import parse_whisper_json
    lines = parse_whisper_json(SAMPLE_WHISPER_JSON)
    assert lines == [
        "[00:00] Hello everyone.",
        "[00:00] Let's get started.",
        "[01:30] Any questions?",
    ]


def test_transcribe_calls_subprocess(tmp_path):
    from transcriber.whisper import transcribe
    audio = tmp_path / "audio.wav"
    audio.touch()
    json_out = tmp_path / "audio.wav.json"
    json_out.write_text(json.dumps(SAMPLE_WHISPER_JSON))

    with patch("transcriber.whisper.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        result = transcribe(
            audio_path=audio,
            whisper_binary=Path("/usr/local/bin/whisper-cpp"),
            model="base",
        )

    mock_run.assert_called_once()
    call_args = mock_run.call_args[0][0]
    assert str(audio) in call_args
    assert "--output-json" in call_args
    assert len(result) == 3
    assert result[0].startswith("[00:00]")


def test_transcribe_raises_on_nonzero_exit(tmp_path):
    from transcriber.whisper import TranscriptionError, transcribe
    audio = tmp_path / "audio.wav"
    audio.touch()

    with patch("transcriber.whisper.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stderr="error")
        with pytest.raises(TranscriptionError):
            transcribe(audio, Path("/usr/local/bin/whisper-cpp"), "base")
