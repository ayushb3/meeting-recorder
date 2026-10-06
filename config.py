# config.py
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import logging

import tomllib

from notes.vault import DEFAULT_SECTIONS, VaultConfig

log = logging.getLogger(__name__)

APP_SUPPORT_DIR = Path.home() / "Library" / "Application Support" / "MeetingRecorder"
USER_CONFIG_PATH = APP_SUPPORT_DIR / "config.toml"

# Paths that use this sentinel are from the template and haven't been edited yet
_TEMPLATE_WHISPER_BINARY = "/opt/homebrew/Cellar/whisper-cpp/1.8.4/share/whisper-cpp/ggml-large-v3.bin"


def _bundled_template() -> Path:
    """Find config.template.toml next to this file or in the .app bundle."""
    import os
    import sys
    candidates = [
        Path(__file__).parent / "config.template.toml",  # dev / repo
    ]
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        candidates.insert(0, Path(sys._MEIPASS) / "config.template.toml")
    resource_path = os.environ.get("RESOURCEPATH")  # py2app
    if resource_path:
        candidates.insert(0, Path(resource_path) / "config.template.toml")

    for p in candidates:
        if p.exists():
            return p
    raise FileNotFoundError("config.template.toml not found in bundle")


def ensure_user_config() -> tuple[Path, bool]:
    """
    Return (config_path, is_first_run).

    On first launch, copy the bundled template and open it in the default
    editor.  The caller should warn the user to quit-and-relaunch after saving.
    """
    if USER_CONFIG_PATH.exists():
        return USER_CONFIG_PATH, False

    APP_SUPPORT_DIR.mkdir(parents=True, exist_ok=True)
    template = _bundled_template()
    shutil.copy(template, USER_CONFIG_PATH)
    subprocess.run(["open", str(USER_CONFIG_PATH)])
    return USER_CONFIG_PATH, True


@dataclass(frozen=True)
class Config:
    output_dir: Path
    system_device: str
    mic_device: str
    whisper_model: Path
    whisper_binary: Path
    ollama_model: str
    ollama_host: str
    keep_audio: bool
    min_recording_seconds: int
    low_disk_threshold_mb: int
    mic_threshold: int = 300
    ollama_prompt: str | None = None
    capture_method: str = "auto"
    llm_provider: str = "ollama"
    llm_base_url: str = ""
    llm_model: str = ""
    llm_terms: str | None = None
    llm_fallback_to_ollama: bool = True
    vault: VaultConfig | None = None
    # Seconds between captured frames for video imports; 0 turns capture off.
    frames_every: int = 10


def _load_vault(raw: dict) -> VaultConfig | None:
    """The optional [vault] section; absent or without a root means disabled."""
    section = raw.get("vault") or {}
    root = str(section.get("root", "")).strip()
    if not root:
        return None
    sections = section.get("sections") or list(DEFAULT_SECTIONS)
    if not isinstance(sections, list) or not all(isinstance(s, str) for s in sections):
        raise ValueError("[vault] sections must be a list of heading names.")
    reads = int(section.get("daily_notes_to_read", 2))
    limit = int(section.get("context_max_chars", 3000))
    if reads < 0 or limit < 200:
        raise ValueError(
            "[vault] daily_notes_to_read must be >= 0 and context_max_chars >= 200."
        )
    command = str(section.get("orient_command", "")).strip() or None
    return VaultConfig(
        root=Path(root).expanduser(),
        orient_command=command,
        status_file=str(section.get("status_file", "NOW.md")),
        daily_notes_dir=str(section.get("daily_notes_dir", "Daily Notes")),
        daily_notes_to_read=reads,
        sections=tuple(sections),
        context_max_chars=limit,
        index_file=str(section.get("index_file", "vault-index.json")),
        write_daily_note=bool(section.get("write_daily_note", False)),
        notes_section=str(section.get("notes_section", "Notes Created")),
    )


def _load_prompt_file(raw: dict) -> str | None:
    """A prompt template kept outside the repo, e.g. ``[llm] prompt_file``.

    A missing or invalid file falls back to the built-in prompt rather than
    stopping the app, since a summary with the default prompt beats no summary.
    """
    value = str(raw.get("llm", {}).get("prompt_file", "")).strip()
    if not value:
        return None
    path = Path(value).expanduser()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        log.warning("[llm] prompt_file %s unreadable (%s); using the built-in prompt", path, exc)
        return None
    if "{transcript}" not in text:
        log.warning("[llm] prompt_file %s has no {transcript}; using the built-in prompt", path)
        return None
    return text


def load_config(path: Path) -> Config:
    if not path.exists():
        raise FileNotFoundError(f"Config not found: {path}")
    with open(path, "rb") as f:
        raw = tomllib.load(f)

    cfg = Config(
        output_dir=Path(raw["paths"]["output_dir"]).expanduser(),
        system_device=raw["audio"]["system_device"],
        mic_device=raw["audio"]["mic_device"],
        whisper_model=Path(raw["whisper"]["model"]),
        whisper_binary=Path(raw["whisper"]["binary"]),
        ollama_model=raw["ollama"]["model"],
        ollama_host=raw["ollama"]["host"],
        keep_audio=raw["processing"]["keep_audio"],
        min_recording_seconds=raw["processing"]["min_recording_seconds"],
        low_disk_threshold_mb=raw["processing"]["low_disk_threshold_mb"],
        mic_threshold=raw["processing"].get("mic_threshold", 300),
        ollama_prompt=_load_prompt_file(raw) or raw.get("ollama", {}).get("prompt", None),
        capture_method=raw["audio"].get("capture_method", "auto"),
        llm_provider=raw.get("llm", {}).get("provider", "ollama"),
        llm_base_url=raw.get("llm", {}).get("base_url", "").strip(),
        llm_model=raw.get("llm", {}).get("model", "").strip(),
        llm_terms=raw.get("llm", {}).get("terms") or None,
        llm_fallback_to_ollama=raw.get("llm", {}).get("fallback_to_ollama", True),
        vault=_load_vault(raw),
        frames_every=int(raw["processing"].get("frames_every", 10)),
    )

    if cfg.frames_every < 0:
        raise ValueError("[processing] frames_every must be 0 (off) or a number of seconds.")

    if cfg.llm_provider not in ("ollama", "openai", "anthropic"):
        raise ValueError(
            f'[llm] provider must be "ollama", "openai" or "anthropic", '
            f'but was {cfg.llm_provider!r}.'
        )
    if cfg.llm_provider != "ollama" and not (cfg.llm_base_url and cfg.llm_model):
        raise ValueError(
            f'[llm] provider = "{cfg.llm_provider}" needs both base_url and model.'
        )

    if cfg.capture_method not in ("auto", "tap", "blackhole"):
        raise ValueError(
            f'[audio] capture_method must be "auto", "tap" or "blackhole", '
            f'but was {cfg.capture_method!r}.'
        )

    if cfg.ollama_prompt is not None and "{transcript}" not in cfg.ollama_prompt:
        raise ValueError(
            "[ollama] prompt must contain {transcript} but it was not found.\n"
            "Add {transcript} where you want the meeting transcript inserted."
        )

    if not cfg.whisper_binary.exists():
        raise ValueError(
            f"whisper binary not found: {cfg.whisper_binary}\n"
            f"Install with: brew install whisper-cpp"
        )
    if not cfg.whisper_model.exists():
        raise ValueError(
            f"whisper model not found: {cfg.whisper_model}\n"
            f"Download a model and update [whisper] model in your config."
        )

    return cfg
