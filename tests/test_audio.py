import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
import tempfile


def test_recorder_writes_two_files(tmp_path):
    from recorder.audio import AudioRecorder

    mock_stream = MagicMock()
    mock_stream.__enter__ = MagicMock(return_value=mock_stream)
    mock_stream.__exit__ = MagicMock(return_value=False)

    with patch("recorder.audio.sf.SoundFile") as mock_sf, \
         patch("recorder.audio.sd.InputStream", return_value=mock_stream):
        recorder = AudioRecorder(
            mic_device="default",
            system_device="BlackHole 2ch",
            output_dir=tmp_path,
            session_name="2026-04-13-14h30",
        )
        recorder.start()
        recorder.stop()

    assert recorder.mic_path.name == "2026-04-13-14h30-audio-mic.wav"
    assert recorder.system_path.name == "2026-04-13-14h30-audio-system.wav"


def test_recorder_duration():
    from recorder.audio import AudioRecorder
    import time

    with patch("recorder.audio.sf.SoundFile"), \
         patch("recorder.audio.sd.InputStream"):
        recorder = AudioRecorder("default", "BlackHole 2ch", Path("/tmp"), "test")
        recorder._start_time = time.time() - 65
        assert recorder.elapsed_seconds() >= 65
