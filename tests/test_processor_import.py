# tests/test_processor_import.py — single_source import path tests
"""Tests for the single_source branch added to run_pipeline().

The central regression is that the user's original file must never be moved
or deleted, regardless of the keep_audio setting.  All whisper/ollama calls
are mocked so no real binaries are required.
"""
from datetime import datetime
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from transcriber.whisper import Segment


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(tmp_path: Path, single_source: Path, keep_audio: bool,
         meeting_name: str | None = None) -> "PipelineResult":  # noqa: F821
    from pipeline.processor import run_pipeline

    segs = [Segment(start_seconds=0.0, text="Hello.", source="system")]
    transcript = ["[00:00] Hello."]

    with patch("pipeline.processor.transcribe_raw", return_value=segs), \
         patch("pipeline.processor.summarize", return_value="## Summary\n- Done."), \
         patch("pipeline.processor.write_note",
               return_value=tmp_path / "meeting.md") as mock_wn:
        result = run_pipeline(
            mic_path=Path("/dev/null"),     # not used when single_source is set
            system_path=Path("/dev/null"),  # not used when single_source is set
            session_dt=datetime(2026, 3, 15, 10, 0),
            duration_seconds=300,
            output_dir=tmp_path,
            whisper_binary=Path("/usr/local/bin/whisper-cpp"),
            whisper_model=Path("base"),
            ollama_model="llama3",
            ollama_host="http://localhost:11434",
            keep_audio=keep_audio,
            meeting_name=meeting_name,
            single_source=single_source,
        )
    return result


# ---------------------------------------------------------------------------
# Central safety regression: original file is never moved or deleted
# ---------------------------------------------------------------------------

def test_original_file_untouched_when_keep_audio_true(tmp_path):
    """The user's original file must still exist after import with keep_audio=True."""
    source = tmp_path / "original-recording.m4a"
    source.write_bytes(b"fake audio data")
    original_size = source.stat().st_size

    result = _run(tmp_path, single_source=source, keep_audio=True)

    assert result.success is True
    assert source.exists(), "Original file was moved or deleted (keep_audio=True)"
    assert source.stat().st_size == original_size, "Original file was modified"


def test_original_file_untouched_when_keep_audio_false(tmp_path):
    """The user's original file must still exist after import with keep_audio=False."""
    source = tmp_path / "original-recording.mp4"
    source.write_bytes(b"fake video data")

    result = _run(tmp_path, single_source=source, keep_audio=False)

    assert result.success is True
    assert source.exists(), "Original file was moved or deleted (keep_audio=False)"


# ---------------------------------------------------------------------------
# mix_wavs is never called in the single-source path
# ---------------------------------------------------------------------------

def test_single_source_skips_mix_wavs(tmp_path):
    """merge_transcripts (which wraps mix_wavs logic) must not be called for a single source."""
    source = tmp_path / "talk.wav"
    source.write_bytes(b"data")

    segs = [Segment(start_seconds=0.0, text="Hi.", source="system")]

    with patch("pipeline.processor.transcribe_raw", return_value=segs), \
         patch("pipeline.processor.merge_transcripts") as mock_merge, \
         patch("pipeline.processor.summarize", return_value="## TL;DR"), \
         patch("pipeline.processor.write_note", return_value=tmp_path / "meeting.md"):
        _run(tmp_path, single_source=source, keep_audio=True)

    mock_merge.assert_not_called()


# ---------------------------------------------------------------------------
# Note lands in correct week folder
# ---------------------------------------------------------------------------

def test_note_lands_in_correct_week_folder(tmp_path):
    source = tmp_path / "conference-talk.wav"
    source.write_bytes(b"data")

    segs = [Segment(start_seconds=0.0, text="Welcome.", source="system")]

    with patch("pipeline.processor.transcribe_raw", return_value=segs), \
         patch("pipeline.processor.summarize", return_value="## Summary"), \
         patch("pipeline.processor.write_note") as mock_wn:
        mock_wn.return_value = tmp_path / "meeting.md"
        from pipeline.processor import run_pipeline
        result = run_pipeline(
            mic_path=Path("/dev/null"),
            system_path=Path("/dev/null"),
            session_dt=datetime(2026, 3, 15, 10, 0),
            duration_seconds=600,
            output_dir=tmp_path,
            whisper_binary=Path("/usr/local/bin/whisper-cpp"),
            whisper_model=Path("base"),
            ollama_model="llama3",
            ollama_host="http://localhost:11434",
            keep_audio=True,
            single_source=source,
        )

    assert result.success is True
    # session_dir should be inside the correct week folder
    assert result.session_dir is not None
    week_folder_name = result.session_dir.parent.name
    # 2026-03-15 is in ISO week 2026-W11
    assert week_folder_name == "2026-W11", f"Expected 2026-W11, got {week_folder_name}"


# ---------------------------------------------------------------------------
# Correct frontmatter: write_note called with session_dt from file mtime
# ---------------------------------------------------------------------------

def test_write_note_called_with_correct_dt(tmp_path):
    source = tmp_path / "old-recording.m4a"
    source.write_bytes(b"data")

    segs = [Segment(start_seconds=0.0, text="Hello.", source="system")]
    expected_dt = datetime(2026, 1, 5, 9, 30)

    with patch("pipeline.processor.transcribe_raw", return_value=segs), \
         patch("pipeline.processor.summarize", return_value="## TL;DR"), \
         patch("pipeline.processor.write_note") as mock_wn:
        mock_wn.return_value = tmp_path / "meeting.md"
        from pipeline.processor import run_pipeline
        run_pipeline(
            mic_path=Path("/dev/null"),
            system_path=Path("/dev/null"),
            session_dt=expected_dt,
            duration_seconds=1800,
            output_dir=tmp_path,
            whisper_binary=Path("/usr/local/bin/whisper-cpp"),
            whisper_model=Path("base"),
            ollama_model="llama3",
            ollama_host="http://localhost:11434",
            keep_audio=False,
            single_source=source,
        )

    assert mock_wn.called
    call_kwargs = mock_wn.call_args.kwargs
    assert call_kwargs["dt"] == expected_dt
    assert call_kwargs["duration_seconds"] == 1800


# ---------------------------------------------------------------------------
# keep_audio=True copies file into session dir
# ---------------------------------------------------------------------------

def test_keep_audio_true_copies_into_session_dir(tmp_path):
    """When keep_audio is True, a copy of the audio should appear in the session dir."""
    source = tmp_path / "uploads" / "recording.m4a"
    source.parent.mkdir()
    source.write_bytes(b"audio-bytes")

    segs = [Segment(start_seconds=0.0, text="Hello.", source="system")]

    with patch("pipeline.processor.transcribe_raw", return_value=segs), \
         patch("pipeline.processor.summarize", return_value="## Summary"), \
         patch("pipeline.processor.write_note") as mock_wn:
        mock_wn.return_value = tmp_path / "meeting.md"
        from pipeline.processor import run_pipeline
        result = run_pipeline(
            mic_path=Path("/dev/null"),
            system_path=Path("/dev/null"),
            session_dt=datetime(2026, 3, 15, 10, 0),
            duration_seconds=300,
            output_dir=tmp_path,
            whisper_binary=Path("/usr/local/bin/whisper-cpp"),
            whisper_model=Path("base"),
            ollama_model="llama3",
            ollama_host="http://localhost:11434",
            keep_audio=True,
            single_source=source,
        )

    assert result.success is True
    # There should be an audio copy somewhere under the session dir
    assert result.session_dir is not None
    audio_copies = list(result.session_dir.glob("audio-import*"))
    assert len(audio_copies) == 1, f"Expected one audio copy, found: {audio_copies}"
    # And the original must still be there
    assert source.exists()


# ---------------------------------------------------------------------------
# keep_audio=False: no audio copy, original untouched
# ---------------------------------------------------------------------------

def test_keep_audio_false_does_not_copy(tmp_path):
    """When keep_audio is False, no audio copy is placed in the session dir."""
    source = tmp_path / "uploads" / "talk.m4a"
    source.parent.mkdir()
    source.write_bytes(b"data")

    segs = [Segment(start_seconds=0.0, text="Hello.", source="system")]

    with patch("pipeline.processor.transcribe_raw", return_value=segs), \
         patch("pipeline.processor.summarize", return_value="## Summary"), \
         patch("pipeline.processor.write_note") as mock_wn:
        mock_wn.return_value = tmp_path / "meeting.md"
        from pipeline.processor import run_pipeline
        result = run_pipeline(
            mic_path=Path("/dev/null"),
            system_path=Path("/dev/null"),
            session_dt=datetime(2026, 3, 15, 10, 0),
            duration_seconds=300,
            output_dir=tmp_path,
            whisper_binary=Path("/usr/local/bin/whisper-cpp"),
            whisper_model=Path("base"),
            ollama_model="llama3",
            ollama_host="http://localhost:11434",
            keep_audio=False,
            single_source=source,
        )

    assert result.success is True
    assert result.session_dir is not None
    audio_copies = list(result.session_dir.glob("audio-import*"))
    assert len(audio_copies) == 0, "Audio was copied despite keep_audio=False"
    assert source.exists(), "Original was deleted with keep_audio=False"


# ---------------------------------------------------------------------------
# Missing source file → graceful error
# ---------------------------------------------------------------------------

def test_missing_source_returns_error(tmp_path):
    ghost = tmp_path / "ghost.mp4"
    # ghost does NOT exist
    from pipeline.processor import run_pipeline
    result = run_pipeline(
        mic_path=Path("/dev/null"),
        system_path=Path("/dev/null"),
        session_dt=datetime(2026, 3, 15, 10, 0),
        duration_seconds=0,
        output_dir=tmp_path,
        whisper_binary=Path("/usr/local/bin/whisper-cpp"),
        whisper_model=Path("base"),
        ollama_model="llama3",
        ollama_host="http://localhost:11434",
        keep_audio=True,
        single_source=ghost,
    )

    assert result.success is False
    assert result.error_stage == "setup"


# ---------------------------------------------------------------------------
# Degraded path: Ollama unavailable → note written with placeholder summary
# ---------------------------------------------------------------------------

def test_single_source_ollama_unavailable_writes_note(tmp_path):
    source = tmp_path / "talk.wav"
    source.write_bytes(b"data")

    segs = [Segment(start_seconds=0.0, text="Hello.", source="system")]
    from summarizer.ollama import OllamaUnavailableError

    with patch("pipeline.processor.transcribe_raw", return_value=segs), \
         patch("pipeline.processor.summarize",
               side_effect=OllamaUnavailableError("down")), \
         patch("pipeline.processor.write_note") as mock_wn:
        mock_wn.return_value = tmp_path / "meeting.md"
        from pipeline.processor import run_pipeline
        result = run_pipeline(
            mic_path=Path("/dev/null"),
            system_path=Path("/dev/null"),
            session_dt=datetime(2026, 3, 15, 10, 0),
            duration_seconds=120,
            output_dir=tmp_path,
            whisper_binary=Path("/usr/local/bin/whisper-cpp"),
            whisper_model=Path("base"),
            ollama_model="llama3",
            ollama_host="http://localhost:11434",
            keep_audio=True,
            single_source=source,
        )

    assert result.success is True
    assert result.summary_ok is False
    # write_note should still have been called
    assert mock_wn.called
    summary_arg = mock_wn.call_args.kwargs["summary"]
    assert "unavailable" in summary_arg.lower()


# ---------------------------------------------------------------------------
# Two-track path still works (regression: the single_source parameter must not
# break the existing pipeline when it is None / not supplied)
# ---------------------------------------------------------------------------

def test_two_track_pipeline_unaffected_by_single_source_param(tmp_path):
    """The normal two-track path is unchanged when single_source is not passed."""
    (tmp_path / "mic.wav").touch()
    (tmp_path / "sys.wav").touch()

    segs_sys = [Segment(start_seconds=0.0, text="Hello.", source="system")]
    segs_mic = [Segment(start_seconds=0.5, text="Hi.", source="mic")]

    with patch("pipeline.processor.transcribe_raw",
               side_effect=[segs_sys, segs_mic]) as mock_tr, \
         patch("pipeline.processor.merge_transcripts",
               return_value=["[00:00] Hello.", "[00:00] (you) Hi."]) as mock_merge, \
         patch("pipeline.processor.summarize", return_value="## Done"), \
         patch("pipeline.processor.write_note",
               return_value=tmp_path / "meeting.md"):
        from pipeline.processor import run_pipeline
        result = run_pipeline(
            mic_path=tmp_path / "mic.wav",
            system_path=tmp_path / "sys.wav",
            session_dt=datetime(2026, 3, 15, 10, 0),
            duration_seconds=120,
            output_dir=tmp_path,
            whisper_binary=Path("/usr/local/bin/whisper-cpp"),
            whisper_model=Path("base"),
            ollama_model="llama3",
            ollama_host="http://localhost:11434",
            keep_audio=True,
        )

    assert result.success is True
    assert mock_tr.call_count == 2
    mock_merge.assert_called_once()
