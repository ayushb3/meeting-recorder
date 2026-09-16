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
