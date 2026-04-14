# app.py
import logging
import sys
from pathlib import Path

from config import ensure_user_config, load_config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)

log = logging.getLogger(__name__)

if __name__ == "__main__":
    config_path = ensure_user_config()

    try:
        config = load_config(config_path)
    except Exception as e:
        log.error("Failed to load config: %s", e)
        import rumps
        rumps.alert(
            title="Meeting Recorder — Config Error",
            message=f"Could not load config:\n{e}\n\nEdit: {config_path}",
        )
        sys.exit(1)

    config.output_dir.mkdir(parents=True, exist_ok=True)

    from ui.menu import MeetingRecorderApp
    MeetingRecorderApp(config).run()
