# config.py
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import tomllib

APP_SUPPORT_DIR = Path.home() / "Library" / "Application Support" / "MeetingRecorder"
USER_CONFIG_PATH = APP_SUPPORT_DIR / "config.toml"


def _bundled_template() -> Path:
    """Find config.template.toml next to this file or in the .app bundle."""
    candidates = [
        Path(__file__).parent / "config.template.toml",  # dev / repo
    ]
    # PyInstaller bundle
    import sys
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        candidates.insert(0, Path(sys._MEIPASS) / "config.template.toml")
    # py2app bundle: RESOURCEPATH env var points to Contents/Resources
    import os
    resource_path = os.environ.get("RESOURCEPATH")
    if resource_path:
        candidates.insert(0, Path(resource_path) / "config.template.toml")

    for p in candidates:
        if p.exists():
            return p
    raise FileNotFoundError("config.template.toml not found in bundle")


def ensure_user_config() -> Path:
    """
    Return the path to the user's config.

    On first launch, copy the bundled template to
    ~/Library/Application Support/MeetingRecorder/config.toml
    and open it in the default editor so the user can fill in their paths.
    """
    if USER_CONFIG_PATH.exists():
        return USER_CONFIG_PATH

    APP_SUPPORT_DIR.mkdir(parents=True, exist_ok=True)
    template = _bundled_template()
    shutil.copy(template, USER_CONFIG_PATH)

    # Open in default editor so the user fills it in
    subprocess.run(["open", str(USER_CONFIG_PATH)])
    return USER_CONFIG_PATH


@dataclass(frozen=True)
class Config:
    output_dir: Path
    system_device: str
    mic_device: str
    whisper_model: str
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
    return Config(
        output_dir=Path(raw["paths"]["output_dir"]).expanduser(),
        system_device=raw["audio"]["system_device"],
        mic_device=raw["audio"]["mic_device"],
        whisper_model=raw["whisper"]["model"],
        whisper_binary=Path(raw["whisper"]["binary"]),
        ollama_model=raw["ollama"]["model"],
        ollama_host=raw["ollama"]["host"],
        keep_audio=raw["processing"]["keep_audio"],
        min_recording_seconds=raw["processing"]["min_recording_seconds"],
        low_disk_threshold_mb=raw["processing"]["low_disk_threshold_mb"],
    )
