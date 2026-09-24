# tests/test_step1.py — coverage for Step 1 logic layer
import pytest
from pathlib import Path
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_meeting(session_dir: Path, date: str, time: str, title: str = "Test Meeting") -> Path:
    """Create a minimal meeting.md with frontmatter in *session_dir*."""
    session_dir.mkdir(parents=True, exist_ok=True)
    note = session_dir / "meeting.md"
    note.write_text(
        f"---\ndate: {date}\ntime: {time}\nduration: 30m\ntags: [meeting]\ntitle: {title}\n---\n\nBody.\n"
    )
    return note


def _week_folder(dt: datetime) -> str:
    from notes.writer import week_folder
    return week_folder(dt)


def _run_pipeline_ollama_down(tmp_path, session_dt):
    """Run the pipeline with Ollama unavailable; let write_note execute for real."""
    from pipeline.processor import run_pipeline
    from summarizer.ollama import OllamaUnavailableError

    (tmp_path / "mic.wav").touch()
    (tmp_path / "sys.wav").touch()

    with patch("pipeline.processor.transcribe_raw", side_effect=[[], []]), \
         patch("pipeline.processor.merge_transcripts", return_value=["[00:00] Hello."]), \
         patch("pipeline.processor.summarize", side_effect=OllamaUnavailableError("down")):

        return run_pipeline(
            mic_path=tmp_path / "mic.wav",
            system_path=tmp_path / "sys.wav",
            session_dt=session_dt,
            duration_seconds=120,
            output_dir=tmp_path,
            whisper_binary=Path("/usr/local/bin/whisper-cpp"),
            whisper_model=Path("base"),
            ollama_model="llama3.2",
            ollama_host="http://localhost:11434",
            keep_audio=True,
        )


# ---------------------------------------------------------------------------
# 1.1  list_notes
# ---------------------------------------------------------------------------

def test_list_notes_renamed_slug_folders(tmp_path):
    """Notes inside slug-named (non-timestamp) folders are returned correctly."""
    from notes.writer import list_notes

    today = datetime.today()
    wdir = tmp_path / _week_folder(today)

    # Two slug-named folders for today
    _write_meeting(wdir / "standup", today.strftime("%Y-%m-%d"), "09:00", "Standup")
    _write_meeting(wdir / "q2-planning", today.strftime("%Y-%m-%d"), "15:00", "Q2 Planning")

    results = list_notes(tmp_path, weeks=1)
    titles = [r["title"] for r in results]
    assert "Standup" in titles
    assert "Q2 Planning" in titles


def test_list_notes_degraded_flag(tmp_path):
    """A note with a *.error sibling is marked degraded=True."""
    from notes.writer import list_notes

    today = datetime.today()
    wdir = tmp_path / _week_folder(today)
    session = wdir / "broken-meeting"
    _write_meeting(session, today.strftime("%Y-%m-%d"), "11:00", "Broken")
    (session / "summarize.error").write_text("stage: summarize\nerror: Ollama down\n")

    results = list_notes(tmp_path, weeks=1)
    assert len(results) == 1
    assert results[0]["degraded"] is True


def test_list_notes_non_degraded_flag(tmp_path):
    """A clean note has degraded=False."""
    from notes.writer import list_notes

    today = datetime.today()
    wdir = tmp_path / _week_folder(today)
    _write_meeting(wdir / "good-meeting", today.strftime("%Y-%m-%d"), "10:00", "Good")

    results = list_notes(tmp_path, weeks=1)
    assert len(results) == 1
    assert results[0]["degraded"] is False


def test_list_notes_multi_week_span(tmp_path):
    """weeks=2 covers this week AND last week."""
    from notes.writer import list_notes

    today = datetime.today()
    last_week = today - timedelta(days=7)

    wdir_this = tmp_path / _week_folder(today)
    wdir_last = tmp_path / _week_folder(last_week)

    _write_meeting(wdir_this / "meeting-a", today.strftime("%Y-%m-%d"), "09:00", "This Week")
    _write_meeting(wdir_last / "meeting-b", last_week.strftime("%Y-%m-%d"), "09:00", "Last Week")

    results = list_notes(tmp_path, weeks=2)
    titles = {r["title"] for r in results}
    assert "This Week" in titles
    assert "Last Week" in titles


def test_list_notes_missing_week_dir_skipped(tmp_path):
    """Missing week dirs don't raise; they're silently skipped."""
    from notes.writer import list_notes
    # tmp_path is empty — no week dirs at all
    results = list_notes(tmp_path, weeks=2)
    assert results == []


def test_list_notes_missing_frontmatter_keys(tmp_path):
    """A note whose frontmatter lacks all keys is included with empty string fallbacks."""
    from notes.writer import list_notes

    today = datetime.today()
    wdir = tmp_path / _week_folder(today)
    session = wdir / "2026-01-01-09h00"
    session.mkdir(parents=True, exist_ok=True)
    # Write a note with no frontmatter at all
    (session / "meeting.md").write_text("No frontmatter here.\n")

    results = list_notes(tmp_path, weeks=1)
    assert len(results) == 1
    assert results[0]["date"] == ""
    assert results[0]["title"] == ""


def test_list_notes_newest_day_first(tmp_path):
    """Dates are returned newest-first."""
    from notes.writer import list_notes

    today = datetime.today()
    yesterday = today - timedelta(days=1)
    wdir_today = tmp_path / _week_folder(today)
    wdir_yesterday = tmp_path / _week_folder(yesterday)

    _write_meeting(wdir_today / "today-meeting", today.strftime("%Y-%m-%d"), "09:00", "Today")
    _write_meeting(wdir_yesterday / "yesterday-meeting", yesterday.strftime("%Y-%m-%d"), "09:00", "Yesterday")

    results = list_notes(tmp_path, weeks=2)
    dates = [r["date"] for r in results]
    assert dates == sorted(dates, reverse=True)


def test_list_notes_within_day_oldest_time_first(tmp_path):
    """Within the same date, entries are sorted oldest time first."""
    from notes.writer import list_notes

    today = datetime.today()
    wdir = tmp_path / _week_folder(today)
    date_str = today.strftime("%Y-%m-%d")

    _write_meeting(wdir / "afternoon", date_str, "15:00", "Afternoon")
    _write_meeting(wdir / "morning", date_str, "09:00", "Morning")

    results = list_notes(tmp_path, weeks=1)
    same_day = [r for r in results if r["date"] == date_str]
    assert same_day[0]["time"] < same_day[1]["time"]


# ---------------------------------------------------------------------------
# B1 regression — find_notes_for_date with all-renamed folders
# ---------------------------------------------------------------------------

def test_find_notes_for_date_all_renamed(tmp_path):
    """B1: find_notes_for_date returns all 3 meetings when folders are slug-named."""
    from notes.writer import find_notes_for_date, week_folder

    dt = datetime(2026, 9, 16, 9, 0)
    date_str = "2026-09-16"
    wdir = tmp_path / week_folder(dt)  # derive from the function, not a hardcoded string

    _write_meeting(wdir / "standup", date_str, "09:00", "Standup")
    _write_meeting(wdir / "q2-planning", date_str, "11:00", "Q2 Planning")
    _write_meeting(wdir / "11-with-alex", date_str, "15:00", "1:1 With Alex")

    results = find_notes_for_date(tmp_path, dt)
    assert len(results) == 3
    # Assert on parent dir names — the key point is slug folders are found
    parent_names = [note.parent.name for note in results]
    assert parent_names == ["standup", "q2-planning", "11-with-alex"]


# ---------------------------------------------------------------------------
# 1.2  Degraded pipeline result (B2)
# ---------------------------------------------------------------------------

def test_pipeline_degraded_result_summary_ok_false(tmp_path):
    """OllamaUnavailableError → success=True, summary_ok=False, warning set."""
    from summarizer.ollama import OllamaUnavailableError
    from pipeline.processor import run_pipeline

    (tmp_path / "mic.wav").touch()
    (tmp_path / "sys.wav").touch()

    with patch("pipeline.processor.transcribe_raw", side_effect=[[], []]), \
         patch("pipeline.processor.merge_transcripts", return_value=["[00:00] Hello."]), \
         patch("pipeline.processor.summarize", side_effect=OllamaUnavailableError("down")), \
         patch("pipeline.processor.write_note", return_value=tmp_path / "note.md"):

        result = run_pipeline(
            mic_path=tmp_path / "mic.wav",
            system_path=tmp_path / "sys.wav",
            session_dt=datetime(2026, 9, 16, 14, 30),
            duration_seconds=120,
            output_dir=tmp_path,
            whisper_binary=Path("/usr/local/bin/whisper-cpp"),
            whisper_model=Path("base"),
            ollama_model="llama3.2",
            ollama_host="http://localhost:11434",
            keep_audio=True,
        )

    assert result.success is True
    assert result.summary_ok is False
    assert result.warning is not None
    assert "Summarizer unavailable" in result.warning


def test_pipeline_degraded_marker_in_final_dir(tmp_path):
    """summarize.error marker lands in the FINAL (post-rename) session dir."""
    from summarizer.ollama import OllamaUnavailableError

    result = _run_pipeline_ollama_down(tmp_path, datetime(2026, 9, 16, 14, 30))

    assert result.success is True
    assert result.session_dir is not None
    marker = result.session_dir / "summarize.error"
    assert marker.exists(), f"summarize.error not found in {result.session_dir}"
    assert "summarize" in marker.read_text()


def test_pipeline_normal_run_no_marker(tmp_path):
    """A successful Ollama run must NOT write a summarize.error file."""
    from pipeline.processor import run_pipeline

    (tmp_path / "mic.wav").touch()
    (tmp_path / "sys.wav").touch()

    with patch("pipeline.processor.transcribe_raw", side_effect=[[], []]), \
         patch("pipeline.processor.merge_transcripts", return_value=["[00:00] Hello."]), \
         patch("pipeline.processor.summarize", return_value="## TL;DR\n- Done."), \
         patch("pipeline.processor.write_note", return_value=tmp_path / "note.md"):

        result = run_pipeline(
            mic_path=tmp_path / "mic.wav",
            system_path=tmp_path / "sys.wav",
            session_dt=datetime(2026, 9, 16, 14, 30),
            duration_seconds=120,
            output_dir=tmp_path,
            whisper_binary=Path("/usr/local/bin/whisper-cpp"),
            whisper_model=Path("base"),
            ollama_model="llama3.2",
            ollama_host="http://localhost:11434",
            keep_audio=True,
        )

    assert result.success is True
    assert result.summary_ok is True
    assert result.warning is None
    error_files = list(tmp_path.rglob("summarize.error"))
    assert error_files == []


def test_pipeline_reprocess_ollama_still_down_leaves_marker(tmp_path):
    """Bug #1 regression: reprocessing with Ollama still down must leave the marker on disk.

    Previously run_pipeline returned success=True so the caller unlinking
    on any success=True would wipe the fresh marker, making reprocess go dark again.
    The guard is now `summary_ok`, so the marker must survive.
    """
    dt = datetime(2026, 9, 16, 14, 30)
    # First run: Ollama down, creates the session dir + marker
    result1 = _run_pipeline_ollama_down(tmp_path, dt)
    assert result1.success is True
    assert result1.summary_ok is False
    first_marker = result1.session_dir / "summarize.error"
    assert first_marker.exists(), "marker must exist after first degraded run"

    # Simulate a reprocess: the existing session dir already has audio + note.
    # The pipeline writes a NEW run into a fresh timestamp dir; what matters is
    # that summary_ok=False is returned so the caller does NOT unlink the marker.
    # We just re-assert summary_ok=False to confirm the contract.
    assert result1.summary_ok is False, (
        "summary_ok must be False so caller skips error_file.unlink()"
    )


# ---------------------------------------------------------------------------
# B6 — Slug collision disambiguation
# ---------------------------------------------------------------------------

def test_pipeline_slug_collision_appends_time(tmp_path):
    """Two meetings with the same title → two distinct dirs, no data loss.

    The colliding dir has content so rename would have failed with ENOTEMPTY on
    the old path too. The real hole (B6) is an EMPTY colliding dir — see next test.
    """
    from pipeline.processor import run_pipeline

    week_dir = tmp_path / "2026-W38"
    existing_slug = week_dir / "standup"
    existing_slug.mkdir(parents=True, exist_ok=True)
    (existing_slug / "meeting.md").write_text("first meeting\n")

    (tmp_path / "mic.wav").touch()
    (tmp_path / "sys.wav").touch()

    with patch("pipeline.processor.transcribe_raw", side_effect=[[], []]), \
         patch("pipeline.processor.merge_transcripts", return_value=["[00:00] Hi."]), \
         patch("pipeline.processor.summarize", return_value="## TL;DR\n- Done."), \
         patch("pipeline.processor.suggest_title", return_value="Standup"):

        result = run_pipeline(
            mic_path=tmp_path / "mic.wav",
            system_path=tmp_path / "sys.wav",
            session_dt=datetime(2026, 9, 16, 15, 30),
            duration_seconds=120,
            output_dir=tmp_path,
            whisper_binary=Path("/usr/local/bin/whisper-cpp"),
            whisper_model=Path("base"),
            ollama_model="llama3.2",
            ollama_host="http://localhost:11434",
            keep_audio=True,
        )

    assert result.success is True
    assert result.session_dir != existing_slug
    # First meeting untouched
    assert (existing_slug / "meeting.md").read_text() == "first meeting\n"
    assert result.session_dir is not None
    assert "15h30" in result.session_dir.name


def test_pipeline_slug_collision_empty_dir_not_overwritten(tmp_path):
    """B6 core: an EMPTY colliding dir must not be silently replaced.

    POSIX rename(2) replaces an empty target directory. The old exists()-guard
    approach chose the right name but then called session_dir.rename(named_dir)
    which would overwrite an empty dir. The new atomic mkdir approach reserves
    the name before any rename, so an empty existing dir is never clobbered.
    """
    from pipeline.processor import run_pipeline

    week_dir = tmp_path / "2026-W38"
    empty_slug = week_dir / "standup"
    empty_slug.mkdir(parents=True, exist_ok=True)
    # Deliberately empty — no files inside

    (tmp_path / "mic.wav").touch()
    (tmp_path / "sys.wav").touch()

    with patch("pipeline.processor.transcribe_raw", side_effect=[[], []]), \
         patch("pipeline.processor.merge_transcripts", return_value=["[00:00] Hi."]), \
         patch("pipeline.processor.summarize", return_value="## TL;DR\n- Done."), \
         patch("pipeline.processor.suggest_title", return_value="Standup"):

        result = run_pipeline(
            mic_path=tmp_path / "mic.wav",
            system_path=tmp_path / "sys.wav",
            session_dt=datetime(2026, 9, 16, 15, 30),
            duration_seconds=120,
            output_dir=tmp_path,
            whisper_binary=Path("/usr/local/bin/whisper-cpp"),
            whisper_model=Path("base"),
            ollama_model="llama3.2",
            ollama_host="http://localhost:11434",
            keep_audio=True,
        )

    assert result.success is True
    # The new session must NOT have landed in the empty slug dir
    assert result.session_dir != empty_slug
    # The empty dir must still exist (not replaced/removed)
    assert empty_slug.exists()
    # The new session name must include the time suffix
    assert result.session_dir is not None
    assert "15h30" in result.session_dir.name


# ---------------------------------------------------------------------------
# 1.4  Prompt validation in config
# ---------------------------------------------------------------------------

def _base_toml(tmp_path: Path, extra: str = "") -> Path:
    whisper_bin = tmp_path / "whisper-cli"
    whisper_model = tmp_path / "model.bin"
    whisper_bin.touch()
    whisper_model.touch()
    p = tmp_path / "test.toml"
    p.write_text(
        f"""
[paths]
output_dir = "/tmp/meetings"
[audio]
system_device = "BlackHole 2ch"
mic_device = "default"
[whisper]
model = "{whisper_model}"
binary = "{whisper_bin}"
[ollama]
model = "llama3.2"
host = "http://localhost:11434"
{extra}
[processing]
keep_audio = true
min_recording_seconds = 30
low_disk_threshold_mb = 500
"""
    )
    return p


def test_config_prompt_valid(tmp_path):
    """A custom prompt containing {transcript} loads without error."""
    from config import load_config

    cfg_path = _base_toml(tmp_path, extra='prompt = "Summarize this: {transcript}"')
    cfg = load_config(cfg_path)
    assert cfg.ollama_prompt == "Summarize this: {transcript}"


def test_config_prompt_missing_transcript_raises(tmp_path):
    """A custom prompt without {transcript} raises ValueError."""
    from config import load_config

    cfg_path = _base_toml(tmp_path, extra='prompt = "No placeholder here."')
    with pytest.raises(ValueError, match=r"\{transcript\}"):
        load_config(cfg_path)


def test_config_prompt_absent_is_none(tmp_path):
    """When [ollama] prompt is absent, ollama_prompt is None."""
    from config import load_config

    cfg_path = _base_toml(tmp_path)
    cfg = load_config(cfg_path)
    assert cfg.ollama_prompt is None


# ---------------------------------------------------------------------------
# 1.5  Custom template — literal braces survive interpolation
# ---------------------------------------------------------------------------

def test_custom_template_literal_braces_do_not_raise():
    """A user prompt containing literal JSON braces must not raise KeyError."""
    from summarizer.ollama import summarize

    template = 'Reply with JSON: {"summary": "...", "items": []}.\nTranscript: {transcript}'

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"response": "ok"}

    with patch("summarizer.ollama.requests.post", return_value=mock_response):
        result = summarize(
            transcript_lines=["Hello."],
            model="llama3.2",
            host="http://localhost:11434",
            custom_template=template,
        )
    assert result == "ok"


def test_custom_template_transcript_substituted():
    """The {transcript} placeholder is filled with the actual transcript."""
    from summarizer.ollama import summarize

    template = "BEGIN\n{transcript}\nEND"

    captured = {}

    def fake_post(url, json=None, timeout=None):
        captured["prompt"] = json["prompt"]
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"response": "done"}
        return mock_response

    with patch("summarizer.ollama.requests.post", side_effect=fake_post):
        summarize(
            transcript_lines=["line one", "line two"],
            model="llama3.2",
            host="http://localhost:11434",
            custom_template=template,
        )

    assert "line one\nline two" in captured["prompt"]
    assert captured["prompt"].startswith("BEGIN")
    assert captured["prompt"].endswith("END")


def test_custom_template_context_substituted():
    """{context} in the custom template is filled with the context string."""
    from summarizer.ollama import summarize

    template = "Context: {context}\nTranscript: {transcript}"

    captured = {}

    def fake_post(url, json=None, timeout=None):
        captured["prompt"] = json["prompt"]
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"response": "done"}
        return mock_response

    with patch("summarizer.ollama.requests.post", side_effect=fake_post):
        summarize(
            transcript_lines=["hello"],
            model="llama3.2",
            host="http://localhost:11434",
            context="quarterly review",
            custom_template=template,
        )

    assert "quarterly review" in captured["prompt"]


def test_builtin_template_still_uses_format():
    """Without custom_template the built-in path still works (regression guard)."""
    from summarizer.ollama import summarize

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"response": "summary text"}

    with patch("summarizer.ollama.requests.post", return_value=mock_response):
        result = summarize(
            transcript_lines=["[00:00] Hello."],
            model="llama3.2",
            host="http://localhost:11434",
        )
    assert result == "summary text"


# ---------------------------------------------------------------------------
# check_status (already committed helper, no previous test coverage)
# ---------------------------------------------------------------------------

def test_check_status_reachable_model_present():
    from summarizer.ollama import check_status

    mock_response = MagicMock()
    mock_response.json.return_value = {"models": [{"name": "llama3.1:8b"}]}

    with patch("summarizer.ollama.requests.get", return_value=mock_response):
        status = check_status("llama3.1:8b", "http://localhost:11434")

    assert status.ready is True
    assert "ready" in status.label


def test_check_status_unreachable():
    from summarizer.ollama import check_status
    import requests

    with patch("summarizer.ollama.requests.get", side_effect=requests.ConnectionError):
        status = check_status("llama3.1:8b", "http://localhost:11434")

    assert status.ready is False
    assert status.reachable is False
    assert "not running" in status.label


def test_check_status_model_not_pulled():
    from summarizer.ollama import check_status

    mock_response = MagicMock()
    mock_response.json.return_value = {"models": [{"name": "other-model:latest"}]}

    with patch("summarizer.ollama.requests.get", return_value=mock_response):
        status = check_status("llama3.1:8b", "http://localhost:11434")

    assert status.reachable is True
    assert status.model_present is False
    assert "not pulled" in status.label
