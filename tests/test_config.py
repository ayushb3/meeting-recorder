# tests/test_config.py
import pytest
from pathlib import Path

def test_config_loads_defaults(tmp_path):
    from config import load_config, Config
    p = tmp_path / "test.toml"
    p.write_text("""
[paths]
output_dir = "/tmp/meetings"
[audio]
system_device = "BlackHole 2ch"
mic_device = "default"
[whisper]
model = "base"
binary = "/usr/local/bin/whisper-cpp"
[ollama]
model = "llama3.2"
host = "http://localhost:11434"
[processing]
keep_audio = true
min_recording_seconds = 30
low_disk_threshold_mb = 500
""")
    cfg = load_config(p)
    assert cfg.output_dir == Path("/tmp/meetings")
    assert cfg.system_device == "BlackHole 2ch"
    assert cfg.whisper_model == "base"
    assert cfg.ollama_model == "llama3.2"
    assert cfg.keep_audio is True
    assert cfg.min_recording_seconds == 30

def test_config_missing_file_raises():
    from config import load_config
    with pytest.raises(FileNotFoundError):
        load_config(Path("/nonexistent/config.toml"))
