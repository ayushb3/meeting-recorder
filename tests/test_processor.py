import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from datetime import datetime
import tempfile


def test_pipeline_success_writes_note(tmp_path):
    from pipeline.processor import run_pipeline, PipelineResult

    with patch("pipeline.processor.mix_wavs") as mock_mix, \
         patch("pipeline.processor.transcribe", return_value=["[00:00] Hello."]) as mock_tr, \
         patch("pipeline.processor.summarize", return_value="## TL;DR\n- Done.") as mock_sum, \
         patch("pipeline.processor.write_note", return_value=tmp_path / "note.md") as mock_wn:

        result = run_pipeline(
            mic_path=tmp_path / "mic.wav",
            system_path=tmp_path / "sys.wav",
            session_dt=datetime(2026, 4, 13, 14, 30),
            duration_seconds=120,
            output_dir=tmp_path,
            whisper_binary=Path("/usr/local/bin/whisper-cpp"),
            whisper_model="base",
            ollama_model="llama3.2",
            ollama_host="http://localhost:11434",
            keep_audio=True,
        )

    assert result.success is True
    assert result.note_path == tmp_path / "note.md"
    mock_mix.assert_called_once()
    mock_tr.assert_called_once()
    mock_sum.assert_called_once()
    mock_wn.assert_called_once()


def test_pipeline_ollama_failure_saves_note_without_summary(tmp_path):
    from pipeline.processor import run_pipeline
    from summarizer.ollama import OllamaUnavailableError

    with patch("pipeline.processor.mix_wavs"), \
         patch("pipeline.processor.transcribe", return_value=["[00:00] Hello."]), \
         patch("pipeline.processor.summarize", side_effect=OllamaUnavailableError("down")), \
         patch("pipeline.processor.write_note", return_value=tmp_path / "note.md") as mock_wn:

        result = run_pipeline(
            mic_path=tmp_path / "mic.wav",
            system_path=tmp_path / "sys.wav",
            session_dt=datetime(2026, 4, 13, 14, 30),
            duration_seconds=120,
            output_dir=tmp_path,
            whisper_binary=Path("/usr/local/bin/whisper-cpp"),
            whisper_model="base",
            ollama_model="llama3.2",
            ollama_host="http://localhost:11434",
            keep_audio=True,
        )

    assert result.success is True
    # Verify write_note was called with a summary containing the unavailability warning
    call_args = mock_wn.call_args
    summary_arg = call_args[1].get("summary") or call_args[0][2]
    assert "Summary unavailable" in summary_arg


def test_pipeline_whisper_failure_writes_error_file(tmp_path):
    from pipeline.processor import run_pipeline
    from transcriber.whisper import TranscriptionError

    (tmp_path / "mic.wav").touch()
    (tmp_path / "sys.wav").touch()

    with patch("pipeline.processor.mix_wavs"), \
         patch("pipeline.processor.transcribe", side_effect=TranscriptionError("failed")):

        result = run_pipeline(
            mic_path=tmp_path / "mic.wav",
            system_path=tmp_path / "sys.wav",
            session_dt=datetime(2026, 4, 13, 14, 30),
            duration_seconds=120,
            output_dir=tmp_path,
            whisper_binary=Path("/usr/local/bin/whisper-cpp"),
            whisper_model="base",
            ollama_model="llama3.2",
            ollama_host="http://localhost:11434",
            keep_audio=True,
        )

    assert result.success is False
    error_files = list(tmp_path.rglob("*.error"))
    assert len(error_files) == 1
    assert "transcribe" in error_files[0].read_text()
