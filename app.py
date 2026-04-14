# app.py
import logging
import sys

from config import ensure_user_config, load_config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)

log = logging.getLogger(__name__)

if __name__ == "__main__":
    import rumps  # imported here so rumps.alert works before full init

    config_path, is_first_run = ensure_user_config()

    if is_first_run:
        rumps.alert(
            title="Welcome to Meeting Recorder",
            message=(
                "Your config file has been opened in a text editor.\n\n"
                "Fill in your paths, save the file, then relaunch the app."
            ),
            ok="Quit",
        )
        sys.exit(0)

    try:
        config = load_config(config_path)
    except Exception as e:
        log.error("Failed to load config: %s", e)
        rumps.alert(
            title="Meeting Recorder — Config Error",
            message=f"Could not load config:\n\n{e}\n\nEdit: {config_path}",
        )
        sys.exit(1)

    config.output_dir.mkdir(parents=True, exist_ok=True)

    from ui.menu import MeetingRecorderApp
    MeetingRecorderApp(config).run()
