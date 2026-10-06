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


# ---------------------------------------------------------------------------
# [vault] and [llm] prompt_file
# ---------------------------------------------------------------------------

def _write_cfg(tmp_path, extra=""):
    whisper_bin = tmp_path / "whisper-cli"
    whisper_bin.write_text("")
    model = tmp_path / "model.bin"
    model.write_text("")
    path = tmp_path / "config.toml"
    path.write_text(f"""
[paths]
output_dir = "{tmp_path}"
[audio]
system_device = "x"
mic_device = "y"
[whisper]
model = "{model}"
binary = "{whisper_bin}"
[ollama]
model = "m"
host = "http://localhost:11434"
[processing]
keep_audio = true
min_recording_seconds = 30
low_disk_threshold_mb = 500
{extra}
""")
    return path


def test_vault_absent_means_disabled(tmp_path):
    from config import load_config
    assert load_config(_write_cfg(tmp_path)).vault is None


def test_vault_section_is_loaded_with_defaults(tmp_path):
    from config import load_config
    cfg = load_config(_write_cfg(tmp_path, f'[vault]\nroot = "{tmp_path}/v"\nwrite_daily_note = true\n'))
    assert cfg.vault.root == tmp_path / "v"
    assert cfg.vault.write_daily_note is True
    assert cfg.vault.daily_notes_to_read == 2
    assert cfg.vault.orient_command is None


def test_vault_without_root_is_disabled(tmp_path):
    from config import load_config
    assert load_config(_write_cfg(tmp_path, "[vault]\nwrite_daily_note = true\n")).vault is None


def test_vault_rejects_tiny_context_budget(tmp_path):
    import pytest
    from config import load_config
    with pytest.raises(ValueError, match="context_max_chars"):
        load_config(_write_cfg(tmp_path, f'[vault]\nroot = "{tmp_path}"\ncontext_max_chars = 10\n'))


def test_prompt_file_overrides_inline_prompt(tmp_path):
    from config import load_config
    prompt = tmp_path / "p.txt"
    prompt.write_text("Summarise:\n{transcript}\n{context}")
    cfg = load_config(_write_cfg(tmp_path, f'[llm]\nprompt_file = "{prompt}"\n'))
    assert cfg.ollama_prompt == "Summarise:\n{transcript}\n{context}"


def test_bad_prompt_file_falls_back_to_default(tmp_path):
    from config import load_config
    missing = load_config(_write_cfg(tmp_path, f'[llm]\nprompt_file = "{tmp_path}/nope.txt"\n'))
    assert missing.ollama_prompt is None
    no_placeholder = tmp_path / "bad.txt"
    no_placeholder.write_text("no placeholder here")
    bad = load_config(_write_cfg(tmp_path, f'[llm]\nprompt_file = "{no_placeholder}"\n'))
    assert bad.ollama_prompt is None


def test_frames_every_defaults_to_ten_and_zero_turns_it_off(tmp_path):
    from config import load_config
    assert load_config(_write_cfg(tmp_path)).frames_every == 10
    off = _write_cfg(tmp_path).read_text().replace(
        "low_disk_threshold_mb = 500", "low_disk_threshold_mb = 500\nframes_every = 0")
    path = tmp_path / "off.toml"
    path.write_text(off)
    assert load_config(path).frames_every == 0


def test_negative_frames_every_is_rejected(tmp_path):
    import pytest
    from config import load_config
    text = _write_cfg(tmp_path).read_text().replace(
        "low_disk_threshold_mb = 500", "low_disk_threshold_mb = 500\nframes_every = -5")
    path = tmp_path / "neg.toml"
    path.write_text(text)
    with pytest.raises(ValueError, match="frames_every"):
        load_config(path)
