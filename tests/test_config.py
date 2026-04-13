# tests/test_config.py
import pytest
import tomllib
from pathlib import Path
import tempfile, os

def write_toml(content: str) -> Path:
    f = tempfile.NamedTemporaryFile(suffix=".toml", delete=False, mode="w")
    f.write(content)
    f.close()
    return Path(f.name)

def test_config_loads_defaults():
    from config import load_config, Config
    path = write_toml("""
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
    cfg = load_config(path)
    assert cfg.output_dir == Path("/tmp/meetings")
    assert cfg.system_device == "BlackHole 2ch"
    assert cfg.whisper_model == "base"
    assert cfg.ollama_model == "llama3.2"
    assert cfg.keep_audio is True
    assert cfg.min_recording_seconds == 30
    os.unlink(path)

def test_config_missing_file_raises():
    from config import load_config
    with pytest.raises(FileNotFoundError):
        load_config(Path("/nonexistent/config.toml"))
