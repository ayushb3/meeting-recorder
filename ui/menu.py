# ui/menu.py
import logging
import re
import shutil
import subprocess
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

import rumps

from config import Config
from pipeline.processor import run_pipeline
from recorder.audio import AudioRecorder

log = logging.getLogger(__name__)

ICON_IDLE = str(Path(__file__).parent.parent / "assets" / "icon.png")
ICON_RECORDING = str(Path(__file__).parent.parent / "assets" / "icon-recording.png")


def slugify(name: str) -> str:
    """Convert a meeting name to a safe folder-name component."""
    name = name.strip().lower()
    name = re.sub(r"[^\w\s-]", "", name)
    name = re.sub(r"[\s_]+", "-", name)
    return name[:60]  # cap length


class MeetingRecorderApp(rumps.App):
    def __init__(self, config: Config):
        super().__init__("", icon=ICON_IDLE, template=True, quit_button=None)
        self.config = config
        self._recorder: AudioRecorder | None = None
        self._recording = False
        self._recording_lock = threading.Lock()
        self._timer_thread: threading.Thread | None = None
        self._session_dt: datetime | None = None
        self._meeting_name: str | None = None  # set via "Set Meeting Name..."
        self._pending_rename: tuple | None = None

        self._record_item = rumps.MenuItem("Start Recording", callback=self.toggle_recording)
        self._name_item = rumps.MenuItem("Set Meeting Name...", callback=self.set_meeting_name)
        self._reprocess_item = rumps.MenuItem("Reprocess Last Meeting", callback=self.reprocess)

        self.menu = [
            self._record_item,
            self._name_item,
            self._reprocess_item,
            rumps.MenuItem("Open Today's Note", callback=self.open_note),
            rumps.separator,
            rumps.MenuItem("Preferences", callback=self.open_prefs),
            rumps.MenuItem("Quit", callback=rumps.quit_application),
        ]
        self._set_idle()

    # ------------------------------------------------------------------ state

    def _set_idle(self):
        self.title = ""
        self.icon = ICON_IDLE
        self._name_item.set_callback(None)  # greyed out when not recording
        self._reprocess_item.set_callback(
            self.reprocess if self._has_error_files() else None
        )

    def _set_recording(self):
        self.icon = ICON_RECORDING
        self._name_item.set_callback(self.set_meeting_name)

    def _has_error_files(self) -> bool:
        if not self.config.output_dir.exists():
            return False
        return bool(list(self.config.output_dir.rglob("*.error")))

    # ------------------------------------------------------- recording toggle

    def toggle_recording(self, sender):
        if not self._recording:
            self._start_recording()
        else:
            self._stop_recording()

    def _start_recording(self):
        self._recording = True
        self._meeting_name = None
        self._session_dt = datetime.now()
        session_name = self._session_dt.strftime("%Y-%m-%d-%Hh%M")
        log.info("Recording started: %s", session_name)

        self._tmp_dir = Path(tempfile.mkdtemp())
        self._recorder = AudioRecorder(
            mic_device=self.config.mic_device,
            system_device=self.config.system_device,
            output_dir=self._tmp_dir,
            session_name=session_name,
        )
        self._recorder.start()
        self._record_item.title = "Stop Recording"
        self._set_recording()
        self._timer_thread = threading.Thread(target=self._update_timer, daemon=True)
        self._timer_thread.start()

    def _stop_recording(self):
        with self._recording_lock:
            if not self._recording:
                return
            self._recording = False

        self._recorder.stop()
        duration = int(self._recorder.elapsed_seconds())
        self._record_item.title = "Start Recording"
        log.info("Recording stopped: duration=%ds", duration)

        mic_path = self._recorder.mic_path
        sys_path = self._recorder.system_path
        session_dt = self._session_dt
        meeting_name = self._meeting_name

        if duration < self.config.min_recording_seconds:
            log.warning("Recording too short (%ds < %ds) — discarded.", duration, self.config.min_recording_seconds)
            rumps.notification("Meeting Recorder", "", "Recording too short — discarded.")
            shutil.rmtree(self._tmp_dir, ignore_errors=True)
            self._set_idle()
            return

        self.title = "Processing..."
        log.info("Dispatching pipeline: mic=%s sys=%s duration=%ds name=%s", mic_path, sys_path, duration, meeting_name)
        threading.Thread(
            target=self._run_pipeline,
            args=(mic_path, sys_path, session_dt, duration, meeting_name),
            daemon=True,
        ).start()

    # --------------------------------------------------------- meeting naming

    def set_meeting_name(self, _):
        current = self._meeting_name or ""
        window = rumps.Window(
            message="Name this meeting (used for the folder and note title):",
            title="Set Meeting Name",
            default_text=current,
            ok="Set",
            cancel="Cancel",
            dimensions=(300, 24),
        )
        response = window.run()
        if response.clicked and response.text.strip():
            self._meeting_name = response.text.strip()
            log.info("Meeting name set: %r", self._meeting_name)

    # ------------------------------------------------------------ pipeline

    def _run_pipeline(
        self,
        mic_path: Path,
        sys_path: Path,
        session_dt: datetime,
        duration: int,
        meeting_name: str | None,
        error_file: Path | None = None,
    ):
        try:
            result = run_pipeline(
                mic_path=mic_path,
                system_path=sys_path,
                session_dt=session_dt,
                duration_seconds=duration,
                meeting_name=meeting_name,
                output_dir=self.config.output_dir,
                whisper_binary=self.config.whisper_binary,
                whisper_model=self.config.whisper_model,
                ollama_model=self.config.ollama_model,
                ollama_host=self.config.ollama_host,
                keep_audio=self.config.keep_audio,
            )
            if result.success:
                if error_file:
                    error_file.unlink(missing_ok=True)
                self.title = ""
                self._set_idle()
                log.info("Pipeline complete: %s", result.note_path)
                # Must show window on main thread
                self._pending_rename = (result.note_path, result.session_dir)
                rumps.Timer(self._show_rename_popup, 0.1).start()
            else:
                self.title = "⚠ Error"
                rumps.notification(
                    "Meeting Recorder", "Processing failed",
                    f"Stage: {result.error_stage}. Click menu to reprocess."
                )
        except Exception as e:
            log.exception("Unexpected pipeline error")
            self.title = "⚠ Error"
            rumps.notification("Meeting Recorder", "Unexpected error", str(e))
            self._set_idle()

    def _show_rename_popup(self, timer):
        timer.stop()
        if self._pending_rename:
            note_path, session_dir = self._pending_rename
            self._pending_rename = None
            self._prompt_rename(note_path, session_dir)

    def _prompt_rename(self, note_path: Path, session_dir: Path):
        """Show a popup after save — lets user rename the meeting."""
        display_name = session_dir.name
        window = rumps.Window(
            message=f"Note saved to:\n{note_path}\n\nGive this meeting a name (or leave blank to keep the timestamp):",
            title="Meeting Saved",
            default_text=self._meeting_name or "",
            ok="Rename",
            cancel="Keep Timestamp",
            dimensions=(300, 24),
        )
        response = window.run()
        if response.clicked and response.text.strip():
            new_name = response.text.strip()
            self._rename_session(session_dir, new_name)

    def _rename_session(self, session_dir: Path, new_name: str):
        """Rename the session folder and note title."""
        slug = slugify(new_name)
        timestamp = session_dir.name  # e.g. 2026-04-14-14h30
        new_dir_name = f"{timestamp}-{slug}"
        new_dir = session_dir.parent / new_dir_name

        try:
            session_dir.rename(new_dir)
            log.info("Renamed session dir: %s -> %s", session_dir.name, new_dir_name)

            # Update the note's H1 title line
            note_path = new_dir / "meeting.md"
            if note_path.exists():
                text = note_path.read_text(encoding="utf-8")
                # Replace the first H1 line (whatever it is) with the new name
                text = re.sub(r"^# .+$", f"# {new_name}", text, count=1, flags=re.MULTILINE)
                note_path.write_text(text, encoding="utf-8")

            rumps.notification("Meeting Recorder", "Renamed", f"Saved as: {new_dir_name}")
        except Exception as e:
            log.error("Rename failed: %s", e)
            rumps.notification("Meeting Recorder", "Rename failed", str(e))

    # ------------------------------------------------------------ timer

    def _update_timer(self):
        while self._recording:
            elapsed = int(self._recorder.elapsed_seconds())
            m, s = divmod(elapsed, 60)
            self.title = f"{m:02d}:{s:02d}"

            disk_check_path = self.config.output_dir if self.config.output_dir.exists() else Path.home()
            stat = shutil.disk_usage(disk_check_path)
            free_mb = stat.free // (1024 * 1024)
            if free_mb < self.config.low_disk_threshold_mb:
                self._stop_recording()
                rumps.notification("Meeting Recorder", "Disk space low", "Recording stopped.")
                break
            time.sleep(1)

    # ------------------------------------------------------------ menu actions

    def reprocess(self, _):
        error_files = list(self.config.output_dir.rglob("*.error"))
        if not error_files:
            return
        error_file = sorted(error_files)[-1]
        session_dir = error_file.parent
        session_name = session_dir.name
        mic_path = session_dir / "audio-mic.wav"
        sys_path = session_dir / "audio-system.wav"
        # Parse timestamp from start of session name (YYYY-MM-DD-HHhMM)
        dt = datetime.strptime(session_name[:16], "%Y-%m-%d-%Hh%M")
        self.title = "Processing..."
        threading.Thread(
            target=self._run_pipeline,
            args=(mic_path, sys_path, dt, 0, None),
            kwargs={"error_file": error_file},
            daemon=True,
        ).start()

    def open_note(self, _):
        from notes.writer import week_folder
        today = datetime.now()
        today_prefix = today.strftime("%Y-%m-%d")
        week_dir = self.config.output_dir / week_folder(today)
        matches = list(week_dir.glob(f"{today_prefix}-*/meeting.md")) if week_dir.exists() else []
        if matches:
            subprocess.run(["open", str(sorted(matches)[-1])])
        else:
            rumps.notification("Meeting Recorder", "", "No note found for today.")

    def open_prefs(self, _):
        subprocess.run(["open", str(Path(__file__).parent.parent / "config.toml")])
