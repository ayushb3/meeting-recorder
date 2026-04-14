# ui/menu.py
import logging
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


class MeetingRecorderApp(rumps.App):
    def __init__(self, config: Config):
        super().__init__("●", quit_button=None)
        self.config = config
        self._recorder: AudioRecorder | None = None
        self._recording = False
        self._recording_lock = threading.Lock()
        self._timer_thread: threading.Thread | None = None
        self._session_dt: datetime | None = None

        self._record_item = rumps.MenuItem("Start Recording", callback=self.toggle_recording)
        self._reprocess_item = rumps.MenuItem("Reprocess Last Meeting", callback=self.reprocess)
        self.menu = [
            self._record_item,
            self._reprocess_item,
            rumps.MenuItem("Open Today's Note", callback=self.open_note),
            rumps.separator,
            rumps.MenuItem("Preferences", callback=self.open_prefs),
            rumps.MenuItem("Quit", callback=rumps.quit_application),
        ]
        self._set_idle()

    def _set_idle(self):
        self.title = "●"
        self._reprocess_item.set_callback(
            self.reprocess if self._has_error_files() else None
        )

    def _has_error_files(self) -> bool:
        if not self.config.output_dir.exists():
            return False
        return bool(list(self.config.output_dir.rglob("*.error")))

    def toggle_recording(self, sender):
        if not self._recording:
            self._start_recording()
        else:
            self._stop_recording()

    def _start_recording(self):
        self._recording = True
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
        log.info("Recording stopped: duration=%ds, mic=%s, sys=%s", duration, self._recorder.mic_path, self._recorder.system_path)

        mic_path = self._recorder.mic_path
        sys_path = self._recorder.system_path
        session_dt = self._session_dt

        if duration < self.config.min_recording_seconds:
            log.warning("Recording too short (%ds < %ds) — discarded.", duration, self.config.min_recording_seconds)
            rumps.notification("Meeting Recorder", "", "Recording too short — discarded.")
            shutil.rmtree(self._tmp_dir, ignore_errors=True)
            self._set_idle()
            return

        self.title = "● Processing..."
        log.info("Dispatching pipeline: mic=%s sys=%s duration=%ds", mic_path, sys_path, duration)
        threading.Thread(
            target=self._run_pipeline,
            args=(mic_path, sys_path, session_dt, duration),
            daemon=True,
        ).start()

    def _run_pipeline(self, mic_path: Path, sys_path: Path, session_dt: datetime, duration: int, error_file: Path | None = None):
        try:
            result = run_pipeline(
                mic_path=mic_path,
                system_path=sys_path,
                session_dt=session_dt,
                duration_seconds=duration,
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
                self.title = "● Ready"
                rumps.notification("Meeting Recorder", "", f"Note saved: {result.note_path.name}")
                time.sleep(3)
                self._set_idle()
            else:
                self.title = "⚠ Error — click to view"
                rumps.notification(
                    "Meeting Recorder", "Processing failed",
                    f"Stage: {result.error_stage}. Click menu to reprocess."
                )
        except Exception as e:
            self.title = "⚠ Error — click to view"
            rumps.notification("Meeting Recorder", "Unexpected error", str(e))
            self._set_idle()

    def _update_timer(self):
        while self._recording:
            elapsed = int(self._recorder.elapsed_seconds())
            m, s = divmod(elapsed, 60)
            self.title = f"● Recording... ({m:02d}:{s:02d})"

            disk_check_path = self.config.output_dir if self.config.output_dir.exists() else Path.home()
            stat = shutil.disk_usage(disk_check_path)
            free_mb = stat.free // (1024 * 1024)
            if free_mb < self.config.low_disk_threshold_mb:
                self._stop_recording()
                rumps.notification("Meeting Recorder", "Disk space low", "Recording stopped.")
                break
            time.sleep(1)

    def reprocess(self, _):
        error_files = list(self.config.output_dir.rglob("*.error"))
        if not error_files:
            return
        error_file = sorted(error_files)[-1]
        session_dir = error_file.parent
        session_name = session_dir.name
        mic_path = session_dir / "audio-mic.wav"
        sys_path = session_dir / "audio-system.wav"
        dt = datetime.strptime(session_name, "%Y-%m-%d-%Hh%M")
        self.title = "● Processing..."
        threading.Thread(
            target=self._run_pipeline,
            args=(mic_path, sys_path, dt, 0),
            kwargs={"error_file": error_file},
            daemon=True,
        ).start()

    def open_note(self, _):
        from notes.writer import week_folder
        today = datetime.now()
        today_prefix = today.strftime("%Y-%m-%d")
        week_dir = self.config.output_dir / week_folder(today)
        matches = list(week_dir.glob(f"{today_prefix}-*-meeting.md")) if week_dir.exists() else []
        if matches:
            subprocess.run(["open", str(sorted(matches)[-1])])  # open most recent
        else:
            rumps.notification("Meeting Recorder", "", "No note found for today.")

    def open_prefs(self, _):
        subprocess.run(["open", str(Path(__file__).parent.parent / "config.toml")])
