# app.py
import logging
from pathlib import Path
from config import load_config
from ui.menu import MeetingRecorderApp

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)

if __name__ == "__main__":
    config = load_config(Path(__file__).parent / "config.toml")
    config.output_dir.mkdir(parents=True, exist_ok=True)
    MeetingRecorderApp(config).run()
