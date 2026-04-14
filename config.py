# config.py
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import tomllib

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
