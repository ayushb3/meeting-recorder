# tests/test_config.py
import pytest
from pathlib import Path

def test_config_loads_defaults(tmp_path):
    from config import load_config, Config
    # Create stub whisper files so load_config path validation passes
    whisper_bin = tmp_path / "whisper-cli"
    whisper_model = tmp_path / "model.bin"
    whisper_bin.touch()
    whisper_model.touch()

    p = tmp_path / "test.toml"
    p.write_text(f"""
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
[processing]
keep_audio = true
min_recording_seconds = 30
low_disk_threshold_mb = 500
""")
    cfg = load_config(p)
    assert cfg.output_dir == Path("/tmp/meetings")
    assert cfg.system_device == "BlackHole 2ch"
    assert cfg.whisper_model == whisper_model
    assert cfg.ollama_model == "llama3.2"
    assert cfg.keep_audio is True
    assert cfg.min_recording_seconds == 30

def test_config_missing_file_raises():
    from config import load_config
    with pytest.raises(FileNotFoundError):
        load_config(Path("/nonexistent/config.toml"))



def _write_config(tmp_path, llm_table: str = "") -> Path:
    whisper_bin = tmp_path / "whisper-cli"
    whisper_model = tmp_path / "model.bin"
    whisper_bin.touch()
    whisper_model.touch()
    p = tmp_path / "test.toml"
    p.write_text(f"""
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
[processing]
keep_audio = true
min_recording_seconds = 30
low_disk_threshold_mb = 500
{llm_table}
""")
    return p


def test_config_without_llm_table_uses_ollama(tmp_path):
    from config import load_config
    cfg = load_config(_write_config(tmp_path))
    assert cfg.llm_provider == "ollama"
    assert cfg.llm_terms is None
    assert cfg.llm_fallback_to_ollama is True


def test_config_reads_llm_table(tmp_path):
    from config import load_config
    cfg = load_config(_write_config(tmp_path, """
[llm]
provider = "openai"
base_url = "http://localhost:6655/openai/v1"
model = "gpt-5.6-luna"
terms = "Langfuse, SDD"
fallback_to_ollama = false
"""))
    assert (cfg.llm_provider, cfg.llm_model) == ("openai", "gpt-5.6-luna")
    assert cfg.llm_base_url == "http://localhost:6655/openai/v1"
    assert cfg.llm_terms == "Langfuse, SDD"
    assert cfg.llm_fallback_to_ollama is False


def test_config_rejects_unknown_provider(tmp_path):
    from config import load_config
    with pytest.raises(ValueError, match="provider"):
        load_config(_write_config(tmp_path, '[llm]\nprovider = "gemini"\n'))


def test_config_hosted_provider_needs_url_and_model(tmp_path):
    from config import load_config
    with pytest.raises(ValueError, match="base_url and model"):
        load_config(_write_config(tmp_path, '[llm]\nprovider = "openai"\nmodel = "gpt-5"\n'))
