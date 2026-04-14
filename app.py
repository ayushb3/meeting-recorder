# app.py
from pathlib import Path
from config import load_config
from ui.menu import MeetingRecorderApp

if __name__ == "__main__":
    config = load_config(Path(__file__).parent / "config.toml")
    config.output_dir.mkdir(parents=True, exist_ok=True)
    MeetingRecorderApp(config).run()
