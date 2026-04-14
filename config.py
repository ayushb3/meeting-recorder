# config.py
from dataclasses import dataclass
from pathlib import Path
import tomllib

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
        output_dir=Path(raw["paths"]["output_dir"]),
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
