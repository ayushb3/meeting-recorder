# ui/menu.py
import logging
import re
import shutil
import subprocess
import sys
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


def _bundle_resource(rel_path: str) -> str:
    """Resolve a resource path that works in dev mode and inside the .app bundle."""
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        # PyInstaller bundle: resources are next to the executable in _MEIPASS
        return str(Path(sys._MEIPASS) / rel_path)
    # Dev mode: relative to repo root (parent of ui/)
    return str(Path(__file__).parent.parent / rel_path)


ICON_IDLE = _bundle_resource("assets/icon.png")
ICON_RECORDING = _bundle_resource("assets/icon-recording.png")


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
        self._pending_stop: tuple | None = None

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

        # Show name + context modal on main thread before dispatching
        self._pending_stop = (mic_path, sys_path, session_dt, duration, meeting_name)
        rumps.Timer(self._show_stop_modal, 0.1).start()

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

    def _show_stop_modal(self, timer):
        """Main-thread modal shown after Stop Recording — collect name + context."""
        timer.stop()
        if not self._pending_stop:
            return
        mic_path, sys_path, session_dt, duration, pre_name = self._pending_stop
        self._pending_stop = None

        # Window 1: meeting name
        name_win = rumps.Window(
            message="Meeting name (used for folder and note title):",
            title="Meeting Saved",
            default_text=pre_name or "",
            ok="Next",
            cancel="Skip",
            dimensions=(320, 24),
        )
        name_resp = name_win.run()
        if name_resp.clicked:
            typed = name_resp.text.strip()
            meeting_name = typed if typed else pre_name
        else:
            meeting_name = pre_name

        # Window 2: optional context for the LLM
        ctx_win = rumps.Window(
            message="Add context for the AI summary (optional):\ne.g. 'Q2 planning with design team, focused on redesign timeline'",
            title="Meeting Context",
            default_text="",
            ok="Process",
            cancel="Skip",
            dimensions=(320, 60),
        )
        ctx_resp = ctx_win.run()
        llm_context = ctx_resp.text.strip() if ctx_resp.clicked and ctx_resp.text.strip() else None

        self.title = "Processing..."
        log.info("Dispatching pipeline: mic=%s sys=%s duration=%ds name=%s context=%s",
                 mic_path, sys_path, duration, meeting_name, llm_context)
        threading.Thread(
            target=self._run_pipeline,
            args=(mic_path, sys_path, session_dt, duration, meeting_name, llm_context),
            daemon=True,
        ).start()

    # ------------------------------------------------------------ pipeline

    def _run_pipeline(
        self,
        mic_path: Path,
        sys_path: Path,
        session_dt: datetime,
        duration: int,
        meeting_name: str | None,
        llm_context: str | None = None,
        error_file: Path | None = None,
    ):
        try:
            result = run_pipeline(
                mic_path=mic_path,
                system_path=sys_path,
                session_dt=session_dt,
                duration_seconds=duration,
                meeting_name=meeting_name,
                llm_context=llm_context,
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
                rumps.notification(
                    "Meeting Recorder",
                    "Note saved",
                    result.meeting_name or (result.session_dir.name if result.session_dir else "Done"),
                )
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
        finally:
            # Clean up temp dir if it still exists (pipeline moves files out on success)
            if hasattr(self, '_tmp_dir') and self._tmp_dir and self._tmp_dir.exists():
                shutil.rmtree(self._tmp_dir, ignore_errors=True)

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
            args=(mic_path, sys_path, dt, 0, None, None),
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
        from config import USER_CONFIG_PATH, ensure_user_config
        ensure_user_config()
        subprocess.run(["open", str(USER_CONFIG_PATH)])
