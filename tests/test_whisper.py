import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from transcriber.whisper import Segment, parse_whisper_json, merge_transcripts

SAMPLE_WHISPER_JSON = {
    "transcription": [
        {"timestamps": {"from": "00:00:00,000", "to": "00:00:05,000"}, "text": " Hello everyone."},
        {"timestamps": {"from": "00:00:05,000", "to": "00:00:12,000"}, "text": " Let's get started."},
        {"timestamps": {"from": "00:01:30,000", "to": "00:01:35,000"}, "text": " Any questions?"},
    ]
}


def test_parse_whisper_json():
    segments = parse_whisper_json(SAMPLE_WHISPER_JSON, source="system")
    assert len(segments) == 3
    assert segments[0].text == "Hello everyone."
    assert segments[0].start_seconds == 0.0
    assert segments[0].source == "system"
    assert segments[2].start_seconds == 90.0


def test_merge_deduplicates_bleed():
    # Mic segment closely matches system segment within time window — dropped as bleed
    sys_segs = [Segment(start_seconds=0.0, text="Hello everyone", source="system")]
    mic_segs = [Segment(start_seconds=0.5, text="Hello everyone", source="mic")]
    lines = merge_transcripts(sys_segs, mic_segs, similarity_threshold=0.85, time_window_seconds=2.0)
    assert len(lines) == 1
    assert "(you)" not in lines[0]


def test_merge_keeps_unique_mic():
    # Mic segment has no match in system — survives with (you) label
    sys_segs = [Segment(start_seconds=0.0, text="Let's get started", source="system")]
    mic_segs = [Segment(start_seconds=5.0, text="Can you hear me?", source="mic")]
    lines = merge_transcripts(sys_segs, mic_segs, similarity_threshold=0.85, time_window_seconds=2.0)
    assert len(lines) == 2
    you_lines = [l for l in lines if "(you)" in l]
    assert len(you_lines) == 1
    assert "Can you hear me?" in you_lines[0]


def test_merge_sorts_by_timestamp():
    sys_segs = [Segment(start_seconds=10.0, text="Second", source="system")]
    mic_segs = [Segment(start_seconds=2.0, text="First", source="mic")]
    lines = merge_transcripts(sys_segs, mic_segs, similarity_threshold=0.85, time_window_seconds=2.0)
    assert "First" in lines[0]
    assert "Second" in lines[1]


def test_transcribe_raw_calls_subprocess(tmp_path):
    from transcriber.whisper import transcribe_raw
    audio = tmp_path / "audio.wav"
    audio.touch()
    json_out = tmp_path / "audio.wav.json"
    json_out.write_text(json.dumps(SAMPLE_WHISPER_JSON))

    with patch("transcriber.whisper.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        result = transcribe_raw(audio, Path("/usr/local/bin/whisper-cpp"), "base", source="system")

    mock_run.assert_called_once()
    assert len(result) == 3
    assert all(s.source == "system" for s in result)


def test_transcribe_raw_raises_on_nonzero_exit(tmp_path):
    from transcriber.whisper import transcribe_raw, TranscriptionError
    audio = tmp_path / "audio.wav"
    audio.touch()

    with patch("transcriber.whisper.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stderr="error")
        with pytest.raises(TranscriptionError):
            transcribe_raw(audio, Path("/usr/local/bin/whisper-cpp"), "base", source="system")


def test_transcribe_backward_compat(tmp_path):
    # transcribe() still works for single-file use
    from transcriber.whisper import transcribe
    audio = tmp_path / "audio.wav"
    audio.touch()
    json_out = tmp_path / "audio.wav.json"
    json_out.write_text(json.dumps(SAMPLE_WHISPER_JSON))

    with patch("transcriber.whisper.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        result = transcribe(audio, Path("/usr/local/bin/whisper-cpp"), "base")

    assert len(result) == 3
    assert result[0].startswith("[00:00]")
