# tests/test_settings_window.py
"""Unit tests for pure helpers in ui/settings_window.py.

AppKit window construction is not tested here (requires a running event loop
and macOS display server). Only module-level pure functions are exercised.
"""
import tomllib
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# build_device_list
# ---------------------------------------------------------------------------

def test_build_device_list_configured_present():
    from ui.settings_window import build_device_list
    devices = ["BlackHole 2ch", "MacBook Pro Microphone", "ZoomAudioDevice"]
    result = build_device_list(devices, "BlackHole 2ch")
    assert result == devices  # no modification


def test_build_device_list_configured_absent():
    from ui.settings_window import build_device_list
    devices = ["MacBook Pro Microphone", "ZoomAudioDevice"]
    result = build_device_list(devices, "BlackHole 2ch")
    assert result[-1] == "BlackHole 2ch (not connected)"
    assert "MacBook Pro Microphone" in result
    assert len(result) == 3


def test_build_device_list_empty_available():
    from ui.settings_window import build_device_list
    result = build_device_list([], "Some Device")
    assert result == ["Some Device (not connected)"]


def test_build_device_list_preserves_order():
    from ui.settings_window import build_device_list
    devices = ["A", "B", "C"]
    result = build_device_list(devices, "A")
    assert result == ["A", "B", "C"]


# ---------------------------------------------------------------------------
# selected_device_value
# ---------------------------------------------------------------------------

def test_selected_device_value_strips_marker():
    from ui.settings_window import selected_device_value
    assert selected_device_value("BlackHole 2ch (not connected)") == "BlackHole 2ch"


def test_selected_device_value_no_marker():
    from ui.settings_window import selected_device_value
    assert selected_device_value("MacBook Pro Microphone") == "MacBook Pro Microphone"


def test_selected_device_value_partial_match():
    from ui.settings_window import selected_device_value
    # Only strips the exact suffix, not a partial one
    val = "Device (not connected) more"
    assert selected_device_value(val) == val


# ---------------------------------------------------------------------------
# validate_settings
# ---------------------------------------------------------------------------

def _valid_fields(tmp_path: Path) -> dict:
    """Return a set of fully-valid settings fields."""
    out = tmp_path / "meetings"
    out.mkdir()
    whisper_bin = tmp_path / "whisper-cli"
    whisper_bin.write_text("")
    whisper_model = tmp_path / "model.bin"
    whisper_model.write_text("")
    return {
        "output_dir": str(out),
        "system_device": "BlackHole 2ch",
        "mic_device": "MacBook Pro Microphone",
        "whisper_binary": str(whisper_bin),
        "whisper_model": str(whisper_model),
        "ollama_model": "llama3.1:8b",
        "ollama_host": "http://localhost:11434",
        "keep_audio": True,
        "min_recording_seconds": "30",
        "low_disk_threshold_mb": "500",
        "mic_threshold": "300",
        "ollama_prompt": "",
    }


def test_validate_settings_valid(tmp_path):
    from ui.settings_window import validate_settings
    errors = validate_settings(_valid_fields(tmp_path))
    assert errors == []


def test_validate_settings_missing_output_dir(tmp_path):
    from ui.settings_window import validate_settings
    fields = _valid_fields(tmp_path)
    fields["output_dir"] = ""
    errors = validate_settings(fields)
    assert any("Output folder" in e for e in errors)


def test_validate_settings_nonexistent_output_dir(tmp_path):
    from ui.settings_window import validate_settings
    fields = _valid_fields(tmp_path)
    fields["output_dir"] = str(tmp_path / "does_not_exist")
    errors = validate_settings(fields)
    assert any("does not exist" in e for e in errors)


def test_validate_settings_prompt_without_transcript(tmp_path):
    from ui.settings_window import validate_settings
    fields = _valid_fields(tmp_path)
    fields["ollama_prompt"] = "Summarize the meeting."  # missing {transcript}
    errors = validate_settings(fields)
    assert any("{transcript}" in e for e in errors)


def test_validate_settings_prompt_with_transcript_ok(tmp_path):
    from ui.settings_window import validate_settings
    fields = _valid_fields(tmp_path)
    fields["ollama_prompt"] = "Summarize:\n{transcript}"
    errors = validate_settings(fields)
    assert errors == []


def test_validate_settings_empty_prompt_ok(tmp_path):
    from ui.settings_window import validate_settings
    fields = _valid_fields(tmp_path)
    fields["ollama_prompt"] = ""
    errors = validate_settings(fields)
    assert errors == []


def test_validate_settings_bad_min_recording(tmp_path):
    from ui.settings_window import validate_settings
    fields = _valid_fields(tmp_path)
    fields["min_recording_seconds"] = "abc"
    errors = validate_settings(fields)
    assert any("integer" in e.lower() for e in errors)


def test_validate_settings_negative_min_recording(tmp_path):
    from ui.settings_window import validate_settings
    fields = _valid_fields(tmp_path)
    fields["min_recording_seconds"] = "-5"
    errors = validate_settings(fields)
    assert any("recording seconds" in e.lower() for e in errors)


def test_validate_settings_mic_threshold_out_of_range(tmp_path):
    from ui.settings_window import validate_settings
    fields = _valid_fields(tmp_path)
    fields["mic_threshold"] = "99999"
    errors = validate_settings(fields)
    assert any("32767" in e for e in errors)


def test_validate_settings_missing_whisper_binary(tmp_path):
    from ui.settings_window import validate_settings
    fields = _valid_fields(tmp_path)
    fields["whisper_binary"] = str(tmp_path / "nonexistent")
    errors = validate_settings(fields)
    assert any("Whisper binary" in e for e in errors)


def test_validate_settings_missing_whisper_model(tmp_path):
    from ui.settings_window import validate_settings
    fields = _valid_fields(tmp_path)
    fields["whisper_model"] = str(tmp_path / "nonexistent.bin")
    errors = validate_settings(fields)
    assert any("Whisper model" in e for e in errors)


# ---------------------------------------------------------------------------
# build_toml_text — round-trip via tomllib
# ---------------------------------------------------------------------------

def _base_fields(tmp_path):
    out = tmp_path / "meetings"
    out.mkdir(exist_ok=True)
    return {
        "output_dir": str(out),
        "system_device": "BlackHole 2ch",
        "mic_device": "MacBook Pro Microphone",
        "whisper_binary": "/opt/homebrew/bin/whisper-cli",
        "whisper_model": "/path/to/model.bin",
        "ollama_model": "llama3.1:8b",
        "ollama_host": "http://localhost:11434",
        "keep_audio": True,
        "min_recording_seconds": 30,
        "low_disk_threshold_mb": 500,
        "mic_threshold": 300,
        "ollama_prompt": "",
    }


def test_build_toml_roundtrip(tmp_path):
    from ui.settings_window import build_toml_text
    fields = _base_fields(tmp_path)
    toml_text = build_toml_text(fields)
    parsed = tomllib.loads(toml_text)
    assert parsed["paths"]["output_dir"] == fields["output_dir"]
    assert parsed["audio"]["system_device"] == "BlackHole 2ch"
    assert parsed["audio"]["mic_device"] == "MacBook Pro Microphone"
    assert parsed["whisper"]["binary"] == "/opt/homebrew/bin/whisper-cli"
    assert parsed["whisper"]["model"] == "/path/to/model.bin"
    assert parsed["ollama"]["model"] == "llama3.1:8b"
    assert parsed["ollama"]["host"] == "http://localhost:11434"
    assert parsed["processing"]["keep_audio"] is True
    assert parsed["processing"]["min_recording_seconds"] == 30
    assert parsed["processing"]["low_disk_threshold_mb"] == 500
    assert parsed["processing"]["mic_threshold"] == 300


def test_build_toml_with_prompt_roundtrip(tmp_path):
    from ui.settings_window import build_toml_text
    out = tmp_path / "meetings"
    out.mkdir()
    fields = {
        "output_dir": str(out),
        "system_device": "BlackHole 2ch",
        "mic_device": "Mic",
        "whisper_binary": "/bin/whisper",
        "whisper_model": "/path/model.bin",
        "ollama_model": "llama3.1:8b",
        "ollama_host": "http://localhost:11434",
        "keep_audio": False,
        "min_recording_seconds": 10,
        "low_disk_threshold_mb": 200,
        "mic_threshold": 150,
        "ollama_prompt": "Summarize this meeting.\n\n{transcript}\n",
    }
    toml_text = build_toml_text(fields)
    parsed = tomllib.loads(toml_text)
    assert "{transcript}" in parsed["ollama"]["prompt"]
    assert parsed["processing"]["keep_audio"] is False


def test_build_toml_special_chars_in_device_name(tmp_path):
    from ui.settings_window import build_toml_text
    out = tmp_path / "meetings"
    out.mkdir()
    fields = {
        "output_dir": str(out),
        "system_device": 'Device "with" quotes',
        "mic_device": "Normal Device",
        "whisper_binary": "/bin/whisper",
        "whisper_model": "/path/model.bin",
        "ollama_model": "llama3.1:8b",
        "ollama_host": "http://localhost:11434",
        "keep_audio": True,
        "min_recording_seconds": 30,
        "low_disk_threshold_mb": 500,
        "mic_threshold": 300,
        "ollama_prompt": "",
    }
    toml_text = build_toml_text(fields)
    parsed = tomllib.loads(toml_text)
    assert parsed["audio"]["system_device"] == 'Device "with" quotes'


# ---------------------------------------------------------------------------
# build_toml_text — comments are present and file still parses
# ---------------------------------------------------------------------------

def test_build_toml_contains_audio_device_comment(tmp_path):
    """The output must contain the comment that tells users how to list devices."""
    from ui.settings_window import build_toml_text
    toml_text = build_toml_text(_base_fields(tmp_path))
    assert "sounddevice" in toml_text
    # Must still parse after comments are added
    tomllib.loads(toml_text)


def test_build_toml_contains_mic_threshold_comment(tmp_path):
    """The mic_threshold comment explaining RMS / backwash must be present."""
    from ui.settings_window import build_toml_text
    toml_text = build_toml_text(_base_fields(tmp_path))
    assert "RMS" in toml_text or "backwash" in toml_text
    tomllib.loads(toml_text)


def test_build_toml_contains_whisper_comment(tmp_path):
    from ui.settings_window import build_toml_text
    toml_text = build_toml_text(_base_fields(tmp_path))
    assert "whisper-cpp" in toml_text
    tomllib.loads(toml_text)


def test_build_toml_contains_ollama_comment(tmp_path):
    from ui.settings_window import build_toml_text
    toml_text = build_toml_text(_base_fields(tmp_path))
    assert "ollama" in toml_text.lower()
    tomllib.loads(toml_text)


def test_build_toml_contains_output_dir_comment(tmp_path):
    from ui.settings_window import build_toml_text
    toml_text = build_toml_text(_base_fields(tmp_path))
    assert "Obsidian" in toml_text or "meeting notes" in toml_text.lower()
    tomllib.loads(toml_text)


# ---------------------------------------------------------------------------
# atomic_save_config — backup and atomic write
# ---------------------------------------------------------------------------

def test_atomic_save_creates_file(tmp_path):
    from ui.settings_window import atomic_save_config
    dest = tmp_path / "config.toml"
    atomic_save_config(dest, "[paths]\noutput_dir = \"/tmp\"\n")
    assert dest.exists()
    assert dest.read_text() == "[paths]\noutput_dir = \"/tmp\"\n"


def test_atomic_save_backs_up_existing(tmp_path):
    from ui.settings_window import atomic_save_config
    dest = tmp_path / "config.toml"
    dest.write_text("original content")
    atomic_save_config(dest, "new content")
    bak = tmp_path / "config.toml.bak"
    assert bak.exists(), "Backup file should be created"
    assert bak.read_text() == "original content"
    assert dest.read_text() == "new content"


def test_atomic_save_no_backup_when_no_existing_file(tmp_path):
    from ui.settings_window import atomic_save_config
    dest = tmp_path / "config.toml"
    atomic_save_config(dest, "content")
    bak = tmp_path / "config.toml.bak"
    # No original file existed, so no backup should be created
    assert not bak.exists()


def test_atomic_save_overwrites_previous_backup(tmp_path):
    from ui.settings_window import atomic_save_config
    dest = tmp_path / "config.toml"
    dest.write_text("first")
    atomic_save_config(dest, "second")
    atomic_save_config(dest, "third")
    bak = tmp_path / "config.toml.bak"
    # After two saves the bak holds the content written by the first save
    assert bak.read_text() == "second"
    assert dest.read_text() == "third"


def test_atomic_save_no_temp_file_left_on_success(tmp_path):
    from ui.settings_window import atomic_save_config
    dest = tmp_path / "config.toml"
    atomic_save_config(dest, "content")
    tmp_files = list(tmp_path.glob("*.tmp"))
    assert tmp_files == [], f"Temp files should be cleaned up: {tmp_files}"


# ---------------------------------------------------------------------------
# Round-trip on a config shaped like the user's real config
# ---------------------------------------------------------------------------

def test_build_toml_real_config_shape_roundtrip(tmp_path):
    """Round-trip a config shaped like the user's live config.toml — values preserved."""
    from ui.settings_window import build_toml_text
    out = tmp_path / "recordings"
    out.mkdir()
    fields = {
        "output_dir": str(out),
        "system_device": "BlackHole 2ch",
        "mic_device": "MacBook Pro Microphone",
        "whisper_binary": "/opt/homebrew/bin/whisper-cli",
        "whisper_model": "/opt/homebrew/Cellar/whisper-cpp/1.8.4/share/whisper-cpp/ggml-large-v3.bin",
        "ollama_model": "gemma:latest",
        "ollama_host": "http://localhost:11434",
        "keep_audio": True,
        "min_recording_seconds": 30,
        "low_disk_threshold_mb": 500,
        "mic_threshold": 300,
        "ollama_prompt": "",
    }
    toml_text = build_toml_text(fields)
    parsed = tomllib.loads(toml_text)
    assert parsed["paths"]["output_dir"] == str(out)
    assert parsed["audio"]["system_device"] == "BlackHole 2ch"
    assert parsed["ollama"]["model"] == "gemma:latest"
    assert parsed["processing"]["mic_threshold"] == 300
    # All standard comments present
    assert "sounddevice" in toml_text
    assert "RMS" in toml_text or "backwash" in toml_text


# ---------------------------------------------------------------------------
# build_toml_text — [llm] section round-trips
# ---------------------------------------------------------------------------

def _base_fields_with_llm(tmp_path, **overrides):
    """Base fields including a minimal [llm] section."""
    fields = _base_fields(tmp_path)
    fields.update({
        "llm_provider": "ollama",
        "llm_base_url": "",
        "llm_model": "",
        "llm_terms": "",
        "llm_fallback_to_ollama": True,
    })
    fields.update(overrides)
    return fields


def test_build_toml_llm_ollama_roundtrip(tmp_path):
    """[llm] provider=ollama round-trips; no base_url/model required."""
    from ui.settings_window import build_toml_text
    fields = _base_fields_with_llm(tmp_path, llm_provider="ollama")
    toml_text = build_toml_text(fields)
    parsed = tomllib.loads(toml_text)
    assert parsed["llm"]["provider"] == "ollama"
    assert parsed["llm"]["fallback_to_ollama"] is True


def test_build_toml_llm_openai_roundtrip(tmp_path):
    """[llm] provider=openai with base_url and model round-trips."""
    from ui.settings_window import build_toml_text
    fields = _base_fields_with_llm(
        tmp_path,
        llm_provider="openai",
        llm_base_url="https://api.openai.com/v1",
        llm_model="gpt-4o",
        llm_fallback_to_ollama=False,
    )
    toml_text = build_toml_text(fields)
    parsed = tomllib.loads(toml_text)
    assert parsed["llm"]["provider"] == "openai"
    assert parsed["llm"]["base_url"] == "https://api.openai.com/v1"
    assert parsed["llm"]["model"] == "gpt-4o"
    assert parsed["llm"]["fallback_to_ollama"] is False


def test_build_toml_llm_anthropic_roundtrip(tmp_path):
    """[llm] provider=anthropic with a proxy base_url round-trips."""
    from ui.settings_window import build_toml_text
    fields = _base_fields_with_llm(
        tmp_path,
        llm_provider="anthropic",
        llm_base_url="http://localhost:6655/anthropic",
        llm_model="claude-sonnet-latest",
        llm_fallback_to_ollama=True,
    )
    toml_text = build_toml_text(fields)
    parsed = tomllib.loads(toml_text)
    assert parsed["llm"]["provider"] == "anthropic"
    assert parsed["llm"]["base_url"] == "http://localhost:6655/anthropic"
    assert parsed["llm"]["model"] == "claude-sonnet-latest"
    assert parsed["llm"]["fallback_to_ollama"] is True


def test_build_toml_llm_terms_roundtrip(tmp_path):
    """[llm] terms field (multi-line) serialises and parses back intact."""
    from ui.settings_window import build_toml_text
    terms = "Alice Smith\nBob Jones\nProject Hydra"
    fields = _base_fields_with_llm(tmp_path, llm_provider="openai",
                                    llm_base_url="https://api.openai.com/v1",
                                    llm_model="gpt-4o", llm_terms=terms)
    toml_text = build_toml_text(fields)
    parsed = tomllib.loads(toml_text)
    # Value may have leading/trailing newline from triple-quote block — strip for comparison
    assert "Alice Smith" in parsed["llm"]["terms"]
    assert "Project Hydra" in parsed["llm"]["terms"]


def test_build_toml_llm_terms_blank_omitted(tmp_path):
    """When terms is blank the key should be absent from the [llm] table."""
    from ui.settings_window import build_toml_text
    fields = _base_fields_with_llm(tmp_path, llm_provider="openai",
                                    llm_base_url="https://api.openai.com/v1",
                                    llm_model="gpt-4o", llm_terms="")
    toml_text = build_toml_text(fields)
    parsed = tomllib.loads(toml_text)
    assert "terms" not in parsed["llm"]


def test_build_toml_llm_special_chars_in_url(tmp_path):
    """base_url containing characters that need TOML escaping round-trips."""
    from ui.settings_window import build_toml_text
    fields = _base_fields_with_llm(
        tmp_path,
        llm_provider="openai",
        llm_base_url='http://host/path?a="quoted"',
        llm_model="m",
    )
    toml_text = build_toml_text(fields)
    parsed = tomllib.loads(toml_text)
    assert parsed["llm"]["base_url"] == 'http://host/path?a="quoted"'


def test_build_toml_no_llm_section_when_provider_empty(tmp_path):
    """When llm_provider is absent/empty the [llm] table is not emitted."""
    from ui.settings_window import build_toml_text
    fields = _base_fields(tmp_path)  # no llm_* keys
    toml_text = build_toml_text(fields)
    parsed = tomllib.loads(toml_text)
    assert "llm" not in parsed


def test_build_toml_llm_keychain_hint_in_output(tmp_path):
    """The Keychain security command hint must appear as a comment."""
    from ui.settings_window import build_toml_text
    fields = _base_fields_with_llm(tmp_path, llm_provider="openai",
                                    llm_base_url="https://api.openai.com/v1",
                                    llm_model="gpt-4o")
    toml_text = build_toml_text(fields)
    assert "MeetingRecorder" in toml_text
    assert "llm-api-key" in toml_text
    # Still parses cleanly
    tomllib.loads(toml_text)


@pytest.mark.parametrize("key,field", [("prompt", "ollama_prompt"), ("terms", "llm_terms")])
def test_multiline_fields_survive_backslashes_and_triple_quotes(key, field):
    """A backslash or \"\"\" in a multi-line field must not produce an unloadable config."""
    import tomllib
    from ui.settings_window import build_toml_text

    value = 'C:\\path {transcript} say """hi""" \\n not a newline\nline two'
    fields = {
        "output_dir": "/tmp/x", "system_device": "d", "mic_device": "m",
        "whisper_model": "/m", "whisper_binary": "/b", "ollama_model": "gemma",
        "ollama_host": "http://localhost:11434", "keep_audio": True,
        "min_recording_seconds": 30, "low_disk_threshold_mb": 500, "mic_threshold": 300,
        "llm_provider": "openai", "llm_base_url": "http://p/v1", "llm_model": "gpt-5",
        "llm_fallback_to_ollama": True, field: value,
    }
    loaded = tomllib.loads(build_toml_text(fields))
    table = loaded["ollama"] if key == "prompt" else loaded["llm"]
    assert table[key].strip("\n") == value


def test_validate_rejects_hosted_provider_without_url_or_model(tmp_path):
    """Saving these would write a config load_config rejects, and the app would not launch."""
    from ui.settings_window import validate_settings

    errors = validate_settings({"output_dir": str(tmp_path), "llm_provider": "openai"})
    assert any("Base URL" in e for e in errors)
    assert any("Model" in e for e in errors)
    assert validate_settings({"output_dir": str(tmp_path), "llm_provider": "ollama"}) == []


def test_single_line_field_with_pasted_newline_still_loads():
    import tomllib
    from ui.settings_window import build_toml_text

    fields = {
        "output_dir": "/tmp/x", "system_device": "d", "mic_device": "m",
        "whisper_model": "/m", "whisper_binary": "/b", "ollama_model": "gemma",
        "ollama_host": "http://localhost:11434", "keep_audio": True,
        "min_recording_seconds": 30, "low_disk_threshold_mb": 500, "mic_threshold": 300,
        "llm_provider": "openai", "llm_base_url": "http://p/v1\n", "llm_model": "gpt-5\r",
        "llm_fallback_to_ollama": True, "llm_terms": "a\r\nb\x07",
    }
    loaded = tomllib.loads(build_toml_text(fields))
    assert loaded["llm"]["base_url"] == "http://p/v1\n"
    assert loaded["llm"]["terms"].strip("\n") == "a\nb\x07"
