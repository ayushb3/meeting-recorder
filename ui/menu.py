# ui/menu.py
import logging
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, date
from pathlib import Path

import rumps

# PyObjCTools.AppHelper.callAfter is the correct primitive for marshalling a
# callable onto the main thread's runloop from a background thread.
# Guard the import so tests (which run outside the .app bundle on any OS) can
# still import this module without exploding.
try:
    from PyObjCTools import AppHelper as _AppHelper
except ImportError:  # non-macOS or stripped test environment
    _AppHelper = None

from config import Config
from pipeline.processor import run_pipeline
from recorder.audio import AudioRecorder
from summarizer.llm import LLMSettings, LLMStatus
from summarizer.llm import check_status as llm_check_status
from summarizer.ollama import OllamaStatus, check_status
from notes.writer import list_notes

log = logging.getLogger(__name__)

# Validated characters for an Ollama model name — safe to embed in AppleScript.
_SAFE_MODEL_RE = re.compile(r'^[A-Za-z0-9._:/@-]+$')

# Written by the spec at build time so a frozen app can find the checkout
# that produced it — scripts/ and .venv/ are never bundled.
try:
    from _build_info import REPO_ROOT as _BUILT_FROM_REPO  # noqa: PLC0415
except ImportError:
    _BUILT_FROM_REPO = None


def _bundle_resource(rel_path: str) -> str:
    """Resolve a resource path that works in dev mode and inside the .app bundle."""
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        return str(Path(sys._MEIPASS) / rel_path)
    return str(Path(__file__).parent.parent / rel_path)


def _repo_path(rel_path: str) -> str:
    """Resolve a path in the source checkout, never inside the .app bundle.

    For things deliberately not bundled — scripts/ and .venv/. Running from
    source, __file__ is already in the checkout. Frozen, it is under _MEIPASS,
    where scripts/ does not exist and never will, so the checkout has to be
    found another way: an explicit override, then the usual locations.

    Returns the first candidate that exists, else the source-relative path so
    the caller reports a sensible missing-file error.
    """
    candidates: list[Path] = []

    override = os.environ.get("MEETING_RECORDER_REPO")
    if override:
        candidates.append(Path(override).expanduser())

    if getattr(sys, "frozen", False):
        # A frozen app cannot see the checkout from __file__, so try the
        # recorded build location and the conventional spot.
        candidates.extend([
            Path(_BUILT_FROM_REPO) if _BUILT_FROM_REPO else None,
            Path.home() / "Documents" / "Personal" / "meeting-recorder",
        ])
    else:
        candidates.append(Path(__file__).resolve().parent.parent)

    for base in candidates:
        if base and (base / rel_path).exists():
            return str(base / rel_path)

    return str(Path(__file__).resolve().parent.parent / rel_path)


ICON_IDLE = _bundle_resource("assets/icon.png")
ICON_RECORDING = _bundle_resource("assets/icon-recording.png")

# Below this RMS a system track is treated as silent rather than quiet. Real
# room audio sits orders of magnitude above it; a failed capture is exactly 0.
_SILENT_TRACK_RMS = 1e-5


# ---------------------------------------------------------------------------
# Pure helpers — no rumps, fully unit-testable
# ---------------------------------------------------------------------------

def display_label(entry: dict) -> str:
    """Derive a human-readable display label for a meeting note entry.

    Fallback chain:
      a. frontmatter ``title`` if non-empty
      b. slug portion of folder name, de-slugified (title-case words).
         Handles both ``YYYY-MM-DD-HHhMM`` (bare timestamp) and
         ``YYYY-MM-DD-HHhMM-slug`` (timestamp + slug) folder names.
      c. H1 heading from the note body if cheaply available
      d. folder name as-is
    """
    title = (entry.get("title") or "").strip()
    if title:
        return title

    note_path: Path | None = entry.get("path")
    if note_path is not None:
        folder = note_path.parent.name
        # Strip leading YYYY-MM-DD-HHhMM (with optional trailing -) to expose the slug
        stripped = re.sub(r"^\d{4}-\d{2}-\d{2}-\d{2}h\d{2}-?", "", folder)
        if stripped:
            return stripped.replace("-", " ").title()
        # Bare timestamp folder — try H1 from note body
        try:
            text = note_path.read_text(encoding="utf-8", errors="ignore")
            for line in text.splitlines():
                line = line.strip()
                if line.startswith("# "):
                    return line[2:].strip()
        except OSError:
            pass
        return folder

    return "Untitled"


def group_notes_for_menu(notes: list[dict], today: date | None = None) -> list[dict]:
    """Group note entries into sections for the Meetings submenu.

    Returns a list of section dicts::

        {"label": str, "entries": [...]}

    Always emits a Today section (may be empty). Emits an "Earlier this week"
    section only when there are older entries.
    """
    if today is None:
        today = date.today()
    today_str = today.strftime("%Y-%m-%d")

    today_entries: list[dict] = []
    earlier_entries: list[dict] = []
    for entry in notes:
        if entry.get("date") == today_str:
            today_entries.append(entry)
        else:
            earlier_entries.append(entry)

    today_label = "Today — " + today.strftime("%a %-d %b")
    sections: list[dict] = [{"label": today_label, "entries": today_entries}]
    if earlier_entries:
        sections.append({"label": "Earlier this week", "entries": earlier_entries})
    return sections


def resolve_session_dt(session_dir: Path) -> datetime:
    """Derive the session datetime for a (possibly slug-named) session directory.

    Tries (in order):
      1. ``strptime`` on the first 16 chars of the folder name (``YYYY-MM-DD-HHhMM``).
      2. ``date:`` / ``time:`` fields in ``meeting.md`` frontmatter.
      3. Directory mtime.

    Never raises — always returns a datetime.
    """
    session_name = session_dir.name

    # 1. Timestamp prefix (works for bare-timestamp AND timestamp+slug folders)
    try:
        return datetime.strptime(session_name[:16], "%Y-%m-%d-%Hh%M")
    except ValueError:
        pass

    # 2. Note frontmatter
    note_path = session_dir / "meeting.md"
    if note_path.exists():
        try:
            from notes.writer import _frontmatter
            fields = _frontmatter(note_path)
            date_str = fields.get("date", "")
            time_str = fields.get("time", "")
            if date_str and time_str:
                return datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
        except Exception:
            log.warning("Could not parse frontmatter date from %s", note_path)

    # 3. mtime
    try:
        return datetime.fromtimestamp(session_dir.stat().st_mtime)
    except OSError:
        pass

    return datetime.now()


# ---------------------------------------------------------------------------
# Notification click handler (module-level, picked up by rumps)
# ---------------------------------------------------------------------------

@rumps.notifications
def notification_handler(info):
    """Handle notification clicks — open the note path stored in ``data``.

    ``info`` is a rumps.Notification (a Mapping wrapping the data dict), not a
    plain dict, so isinstance(info, dict) must NOT be used as a guard.
    """
    try:
        # info is a rumps.Notification which implements Mapping; .get() works.
        path = info.get("note_path") if info is not None else None
        if path:
            subprocess.Popen(["open", str(path)])
    except Exception:
        log.exception("notification_handler error")


# ---------------------------------------------------------------------------
# App class
# ---------------------------------------------------------------------------

class MeetingRecorderApp(rumps.App):
    def __init__(self, config: Config):
        super().__init__("", icon=ICON_IDLE, template=True, quit_button=None)
        self.config = config
        self._recorder: AudioRecorder | None = None
        self._recording = False
        self._recording_lock = threading.Lock()
        self._timer_thread: threading.Thread | None = None
        self._session_dt: datetime | None = None
        self._pending_stop: tuple | None = None

        # Cached summarizer status — probed on a background thread, never on draw.
        # Typed as OllamaStatus | LLMStatus | None to cover both probe paths.
        self._ollama_status: OllamaStatus | LLMStatus | None = None
        self._ollama_status_lock = threading.Lock()

        # Hero action item
        self._record_item = rumps.MenuItem("● Start Recording", callback=self.toggle_recording)

        # ---- Meetings submenu (rebuilt on timer + pipeline completion) ----
        self._meetings_menu = rumps.MenuItem("Meetings")

        # ---- Ollama submenu ----
        self._ollama_status_item = rumps.MenuItem("Checking…", callback=None)
        self._ollama_detail_item = rumps.MenuItem("", callback=None)
        self._ollama_start_item = rumps.MenuItem("Start Ollama in Terminal ↗", callback=self.start_ollama)
        self._ollama_pull_item = rumps.MenuItem("Pull Model… ↗", callback=self.pull_model)
        self._ollama_recheck_item = rumps.MenuItem("Refresh Ollama Status", callback=self.recheck_ollama)
        self._ollama_menu = rumps.MenuItem("⚪ Ollama")
        self._ollama_root_item = self._ollama_menu  # alias for clarity in _update_ollama_ui

        # ---- Import a local audio/video file (in-process) ----
        self._import_recording_item = rumps.MenuItem(
            "Import Recording…", callback=self.import_recording
        )

        # ---- Import from Stream (shells out — see import_stream_transcript) ----
        self._import_transcript_item = rumps.MenuItem(
            "Import Transcript from Stream… ↗", callback=self.import_stream_transcript
        )

        # ---- Location items (shown at bottom of Meetings submenu) ----
        self._location_caption = rumps.MenuItem("", callback=None)

        # ---- Top-level Settings / Quit ----
        self._settings_item = rumps.MenuItem("Settings…", callback=self.open_prefs)
        self._quit_item = rumps.MenuItem("Quit", callback=rumps.quit_application)

        self._build_static_submenus()
        self._build_top_menu()
        self._set_idle()

        # An accessory app has no menu bar, so ⌘X/⌘C/⌘V would reach nothing and
        # no text field in this app could paste. Give them somewhere to dispatch.
        try:
            from ui.edit_menu import install_edit_menu  # noqa: PLC0415
            install_edit_menu()
        except Exception:
            log.warning("Edit menu unavailable; clipboard shortcuts will not work")

        # Initial Meetings submenu population (sync — output_dir may not exist yet, that's fine)
        self._rebuild_meetings_menu()

        # Ollama poll: every 45s also refreshes the Meetings submenu
        self._ollama_poll_timer = rumps.Timer(self._on_poll_timer, 45)
        self._ollama_poll_timer.start()

        # Initial async Ollama probe
        threading.Thread(target=self._probe_ollama_and_refresh_ui, daemon=True).start()

    # ---------------------------------------------------------------- menu building

    def _build_static_submenus(self):
        self._ollama_menu.update([
            self._ollama_status_item,
            self._ollama_detail_item,
            None,
            self._ollama_start_item,
            self._ollama_pull_item,
            self._ollama_recheck_item,
        ])

    def _build_top_menu(self):
        """Set the root menu — fixed size, never grows with meeting count."""
        self.menu = [
            self._record_item,
            None,
            self._meetings_menu,
            self._import_recording_item,
            self._import_transcript_item,
            self._ollama_menu,
            None,
            self._settings_item,
            self._quit_item,
        ]

    def _short_location(self) -> str:
        try:
            home = Path.home()
            p = self.config.output_dir
            try:
                rel = p.relative_to(home)
                return f"~/{rel}"
            except ValueError:
                return str(p)
        except Exception:
            return str(self.config.output_dir)

    # ---------------------------------------------------------------- state
    # ALL of these must be called on the main thread only.

    def _set_idle(self):
        self.title = ""
        self.icon = ICON_IDLE
        self._record_item.title = "● Start Recording"
        self._record_item.set_callback(self.toggle_recording)

    def _set_processing(self):
        """Disable the hero item while the pipeline runs (main thread only)."""
        self.title = "Processing…"
        self._record_item.title = "Processing…"
        self._record_item.set_callback(None)

    def _set_recording(self):
        self.icon = ICON_RECORDING
        # Set the label synchronously (we are already on the main thread — this
        # is called directly from _start_recording which is a menu callback).
        # _update_timer will keep it updated every second thereafter.
        self._record_item.title = "■ Stop Recording — 00:00"

    # ---------------------------------------------------------------- notification helper (B4)

    def _notify(self, title: str, subtitle: str, message: str,
                data: dict | None = None, action_button: str | None = None):
        """Wrap rumps.notification to never raise when running unbundled.

        rumps.notification raises RuntimeError when there is no CFBundleIdentifier
        (``python app.py`` without a built .app). Catch it and log instead so a
        successful run is not reported as a failure.
        """
        try:
            kwargs: dict = {}
            if data is not None:
                kwargs["data"] = data
            if action_button is not None:
                kwargs["action_button"] = action_button
            rumps.notification(title, subtitle, message, **kwargs)
        except RuntimeError:
            log.info("Notification suppressed (no bundle): %s — %s: %s", title, subtitle, message)
        except Exception:
            log.exception("rumps.notification failed unexpectedly")

    # ---------------------------------------------------------------- marshal helpers

    def _call_on_main(self, fn, *args, **kwargs):
        """Schedule *fn* to run on the main thread's runloop.

        Uses PyObjCTools.AppHelper.callAfter when available (macOS .app bundle),
        which reliably dispatches to the main runloop from any background thread.
        Falls back to a direct call (used in test / non-macOS environments).
        """
        if _AppHelper is not None:
            if kwargs:
                _AppHelper.callAfter(lambda: fn(*args, **kwargs))
            else:
                _AppHelper.callAfter(fn, *args)
        else:
            fn(*args, **kwargs)

    # ---------------------------------------------------------------- recording toggle

    def toggle_recording(self, sender):
        if not self._recording:
            self._start_recording()
        else:
            self._stop_recording()

    def _start_recording(self):
        # Always called on main thread (menu callback).
        with self._recording_lock:
            if self._recording:
                return  # guard against double-click (issue #5)
            self._recording = True

        # Warn if Ollama is not ready (never block — recording always works)
        with self._ollama_status_lock:
            status = self._ollama_status
        if status is not None and not status.ready:
            self._notify(
                "Meeting Recorder",
                "Ollama not available",
                "Recording will proceed but the summary will be unavailable. "
                "Start Ollama from the Ollama menu to enable summaries.",
            )

        self._session_dt = datetime.now()
        session_name = self._session_dt.strftime("%Y-%m-%d-%Hh%M")
        log.info("Recording started: %s", session_name)

        tmp_dir = Path(tempfile.mkdtemp())
        self._tmp_dir = tmp_dir  # kept for the short-recording cleanup path only
        self._recorder = AudioRecorder(
            mic_device=self.config.mic_device,
            system_device=self.config.system_device,
            output_dir=tmp_dir,
            session_name=session_name,
            mic_threshold=self.config.mic_threshold,
            capture_method=self.config.capture_method,
        )
        self._recorder.start()
        if self._recorder.system_capture_method == "blackhole" and self._recorder.tap_error:
            # Falling back is fine, but silently recording via the legacy path
            # would leave the user guessing why setup still matters.
            log.warning("System audio tap unavailable: %s", self._recorder.tap_error)
            self._notify(
                "Meeting Recorder",
                "Using loopback device",
                f"System audio tap unavailable ({self._recorder.tap_error}). "
                f"Recording via {self.config.system_device}.",
            )
        self._set_recording()
        self._timer_thread = threading.Thread(
            target=self._update_timer, args=(tmp_dir,), daemon=True
        )
        self._timer_thread.start()

    def _stop_recording(self):
        """Stop the recorder. Safe to call from both the main thread and the timer thread."""
        with self._recording_lock:
            if not self._recording:
                return
            self._recording = False

        self._recorder.stop()
        duration = int(self._recorder.elapsed_seconds())
        log.info("Recording stopped: duration=%ds", duration)

        mic_path = self._recorder.mic_path
        sys_path = self._recorder.system_path
        session_dt = self._session_dt
        tmp_dir = self._tmp_dir  # captured locally (issue #10)

        if duration < self.config.min_recording_seconds:
            log.warning("Recording too short (%ds < %ds) — discarded.", duration, self.config.min_recording_seconds)
            # Marshal UI update to main thread
            def _discard():
                self._notify("Meeting Recorder", "", "Recording too short — discarded.")
                shutil.rmtree(tmp_dir, ignore_errors=True)
                self._set_idle()
            self._call_on_main(_discard)
            return

        # A silent system track means capture failed — the tap and the loopback
        # both fail by producing zeroes rather than raising. Say so now, while
        # the user can still act on it, rather than leaving it to be discovered
        # in the note.
        sys_rms = self._recorder.system_rms()
        if sys_rms < _SILENT_TRACK_RMS:
            method = self._recorder.system_capture_method
            log.warning("System audio track is silent (RMS=%.6f, method=%s)", sys_rms, method)
            hint = (
                f"Check that {self.config.system_device} is in your output path."
                if method == "blackhole"
                else "Grant Meeting Recorder audio capture access in System Settings."
            )
            self._call_on_main(
                self._notify, "Meeting Recorder", "No system audio captured",
                f"Only your microphone was recorded. {hint}",
            )

        # Show name + context modal on main thread before dispatching pipeline
        self._pending_stop = (mic_path, sys_path, session_dt, duration, tmp_dir)
        self._call_on_main(self._show_stop_modal_main)

    # ------------------------------------------------------------ pipeline

    def _show_stop_modal_main(self):
        """Main-thread modal — collect meeting name + context, then launch pipeline."""
        if not self._pending_stop:
            return
        mic_path, sys_path, session_dt, duration, tmp_dir = self._pending_stop
        self._pending_stop = None

        from ui.stop_dialog import open_stop_dialog

        def _on_process(meeting_name, llm_context):
            self._set_processing()
            log.info(
                "Dispatching pipeline: duration=%ds name=%s context=%s",
                duration, meeting_name,
                f"{len(llm_context)} chars" if llm_context else "none",
            )
            threading.Thread(
                target=self._run_pipeline,
                args=(mic_path, sys_path, session_dt, duration, meeting_name, llm_context, None, tmp_dir),
                daemon=True,
            ).start()

        def _on_skip():
            # Skip both fields: name=None (LLM picks), context=None
            self._set_processing()
            log.info("Stop dialog skipped — dispatching pipeline with no name/context")
            threading.Thread(
                target=self._run_pipeline,
                args=(mic_path, sys_path, session_dt, duration, None, None, None, tmp_dir),
                daemon=True,
            ).start()

        open_stop_dialog(on_process=_on_process, on_skip=_on_skip)

    def _run_pipeline(
        self,
        mic_path: Path,
        sys_path: Path,
        session_dt: datetime,
        duration: int,
        meeting_name: str | None,
        llm_context: str | None = None,
        error_file: Path | None = None,
        tmp_dir: Path | None = None,  # issue #10: passed explicitly, not read from self
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
                ollama_prompt=self.config.ollama_prompt,
                llm=LLMSettings.from_config(self.config),
            )
            if result.success:
                if error_file and result.summary_ok:
                    error_file.unlink(missing_ok=True)
                display_name = (
                    result.meeting_name
                    or (result.session_dir.name if result.session_dir else "Done")
                )
                note_path_str = str(result.note_path) if result.note_path else None

                def _ui_success():
                    self._set_idle()
                    if not result.summary_ok:
                        self.title = "⚠"
                    self._rebuild_meetings_menu()
                    if result.summary_ok:
                        self._notify(
                            "Meeting Recorder", "Note saved", display_name,
                            data={"note_path": note_path_str} if note_path_str else None,
                            action_button="Open",
                        )
                    else:
                        self._notify(
                            "Meeting Recorder", "Note saved (summary unavailable)",
                            result.warning or "No LLM was reachable. Open Meetings menu to retry.",
                        )
                self._call_on_main(_ui_success)
                log.info("Pipeline complete: %s", result.note_path)
            else:
                def _ui_failure():
                    self._set_idle()  # B3: re-enable reprocess immediately
                    self.title = "⚠ Error"
                    self._rebuild_meetings_menu()
                    self._notify(
                        "Meeting Recorder", "Processing failed",
                        f"Stage: {result.error_stage}. Open Meetings menu to reprocess.",
                    )
                self._call_on_main(_ui_failure)
        except Exception as e:
            log.exception("Unexpected pipeline error")
            def _ui_error():
                self._set_idle()  # B3
                self.title = "⚠ Error"
                self._notify("Meeting Recorder", "Unexpected error", str(e))
                self._rebuild_meetings_menu()
            self._call_on_main(_ui_error)
        finally:
            if tmp_dir and tmp_dir.exists():
                shutil.rmtree(tmp_dir, ignore_errors=True)

    # ---------------------------------------------------------------- timer

    def _update_timer(self, tmp_dir: Path):
        """Background timer thread — only writes self.title and _record_item.title.

        Both are marshalled to the main thread via _call_on_main to avoid
        off-thread AppKit mutations.
        """
        while self._recording:
            elapsed = int(self._recorder.elapsed_seconds())
            m, s = divmod(elapsed, 60)
            elapsed_str = f"{m:02d}:{s:02d}"

            def _ui(t=elapsed_str):
                self.title = t
                self._record_item.title = f"■ Stop Recording — {t}"
            self._call_on_main(_ui)

            disk_check_path = self.config.output_dir if self.config.output_dir.exists() else Path.home()
            stat = shutil.disk_usage(disk_check_path)
            free_mb = stat.free // (1024 * 1024)
            if free_mb < self.config.low_disk_threshold_mb:
                # Stop recording from timer thread; marshal modal + UI to main (issue #4)
                self._stop_recording()
                break
            time.sleep(1)

    # ---------------------------------------------------------------- Ollama status

    def _probe_ollama_and_refresh_ui(self):
        """Run a blocking summarizer probe on a background thread, then marshal UI update to main.

        When the configured provider is ollama (the default) the existing
        OllamaStatus path is used so Ollama-specific detail (host, model) is
        preserved in the submenu.  For hosted providers (openai / anthropic) we
        use the provider-neutral llm_check_status which returns LLMStatus.
        """
        if self.config.llm_provider == "ollama":
            status = check_status(self.config.ollama_model, self.config.ollama_host)
        else:
            status = llm_check_status(LLMSettings.from_config(self.config))
        with self._ollama_status_lock:
            self._ollama_status = status
        # Marshal UI mutation to main thread (issue #2)
        self._call_on_main(self._update_ollama_ui, status)

    def _on_poll_timer(self, timer):
        """rumps.Timer callback (main thread): kick off Ollama probe + menu refresh."""
        self._rebuild_meetings_menu()
        threading.Thread(target=self._probe_ollama_and_refresh_ui, daemon=True).start()

    def _update_ollama_ui(self, status):
        """Update summarizer submenu and root item title. MUST be called on the main thread.

        Accepts either OllamaStatus (when provider=ollama) or LLMStatus (hosted
        providers). The two types share .ready and differ in their detail fields.
        """
        if isinstance(status, LLMStatus):
            # Hosted provider (openai / anthropic)
            if status.ready:
                self._ollama_root_item.title = "🟢 Summarizer"
                self._ollama_status_item.title = "🟢 Ready"
                self._ollama_detail_item.title = status.detail
            else:
                self._ollama_root_item.title = "🔴 Summarizer"
                self._ollama_status_item.title = f"🔴 {status.label}"
                self._ollama_detail_item.title = status.detail
        else:
            # OllamaStatus — preserve existing Ollama-specific detail
            if status.ready:
                self._ollama_root_item.title = "🟢 Ollama"
                self._ollama_status_item.title = "🟢 Running"
                self._ollama_detail_item.title = f"{status.model} · {status.host}"
            elif status.reachable:
                self._ollama_root_item.title = "🟡 Ollama"
                self._ollama_status_item.title = "🟡 Running — model not pulled"
                self._ollama_detail_item.title = f"{status.model} not found at {status.host}"
            else:
                self._ollama_root_item.title = "🔴 Ollama"
                self._ollama_status_item.title = "🔴 Not running"
                self._ollama_detail_item.title = f"No server at {status.host}"

    # ---------------------------------------------------------------- meetings submenu

    def _rebuild_meetings_menu(self):
        """Rebuild the Meetings submenu. MUST be called on the main thread."""
        try:
            notes = list_notes(self.config.output_dir, weeks=2)
        except Exception:
            log.exception("list_notes failed")
            notes = []

        # Also surface error-only sessions (no meeting.md — issue #3)
        error_only_dirs = _find_error_only_dirs(self.config.output_dir)

        sections = group_notes_for_menu(notes)
        items: list = []

        for i, section in enumerate(sections):
            caption = rumps.MenuItem(section["label"], callback=None)
            items.append(caption)
            for entry in section["entries"]:
                items.append(self._make_note_item(entry))
            if i == 0 and not section["entries"] and len(sections) > 1:
                # Separator after empty Today section so next caption isn't adjacent (issue #12)
                items.append(None)
            elif i < len(sections) - 1 and section["entries"]:
                items.append(None)

        # Error-only sessions (no note file) appended under their own caption
        if error_only_dirs:
            items.append(None)
            items.append(rumps.MenuItem("Failed (no note)", callback=None))
            for d in error_only_dirs:
                items.append(self._make_error_dir_item(d))

        # Location footer — always at the bottom of the Meetings submenu
        self._location_caption.title = self._short_location()
        items.append(None)
        items.append(self._location_caption)
        items.append(rumps.MenuItem("Open Meetings Folder ↗", callback=self.open_output_dir))
        items.append(rumps.MenuItem("Change Location…", callback=lambda _: self.open_prefs(_, focus_output_dir=True)))

        # clear() calls NSMenu.removeAllItems() which requires _menu to be non-None.
        # A MenuItem with no prior children has _menu=None, so guard the clear.
        # update() allocates _menu as needed — safe to call unconditionally.
        if getattr(self._meetings_menu, '_menu', None) is not None:
            self._meetings_menu.clear()
        self._meetings_menu.update(items)

    def _make_note_item(self, entry: dict) -> rumps.MenuItem:
        """Build a single note menu item (with Retry submenu when degraded)."""
        time_str = entry.get("time", "")
        degraded = entry.get("degraded", False)
        note_path: Path | None = entry.get("path")

        readable = display_label(entry)
        if degraded:
            label = f"⚠ {time_str}  {readable}" if time_str else f"⚠ {readable}"
        else:
            label = f"{time_str}  {readable}" if time_str else readable

        if degraded:
            main_item = rumps.MenuItem(label, callback=None)  # has submenu; callback never fires (#13)
            open_item = rumps.MenuItem(
                "Open Note ↗",
                callback=lambda _, p=note_path: self._open_note_checked(p),
            )
            retry_item = rumps.MenuItem(
                "Retry Summary",
                callback=lambda _, p=note_path: self._reprocess_note(p),
            )
            reveal_item = rumps.MenuItem(
                "Reveal in Finder ↗",
                callback=lambda _, p=note_path: self._reveal_in_finder(p),
            )
            main_item.update([open_item, retry_item, reveal_item])
            return main_item
        else:
            return rumps.MenuItem(
                label, callback=lambda _, p=note_path: self._open_note_checked(p)
            )

    def _make_error_dir_item(self, session_dir: Path) -> rumps.MenuItem:
        """Build a menu item for a session that failed before writing a note (issue #3)."""
        label = f"⚠ {session_dir.name}"
        main_item = rumps.MenuItem(label, callback=None)
        error_files = sorted(session_dir.glob("*.error"))
        if error_files:
            retry_item = rumps.MenuItem(
                "Retry",
                callback=lambda _, d=session_dir, e=error_files[0]: self._reprocess_session(d, e),
            )
            main_item.update([retry_item])
        return main_item

    # ---------------------------------------------------------------- note actions

    def _open_note_checked(self, note_path: Path | None):
        """Open a note, showing an error if the path is stale/gone (issue #6)."""
        if note_path is None:
            return
        # Re-stat to detect stale paths from pipeline renames
        if not note_path.exists():
            self._notify("Meeting Recorder", "Note not found",
                         f"The file may have been moved. Open the Meetings folder to browse.")
            log.warning("Stale note path: %s", note_path)
            return
        try:
            subprocess.Popen(["open", str(note_path)])
        except Exception:
            log.exception("Failed to open note: %s", note_path)

    def _reveal_in_finder(self, note_path: Path | None):
        if note_path is None:
            return
        if not note_path.exists():
            note_path = note_path.parent  # reveal the session dir at least
        try:
            subprocess.Popen(["open", "-R", str(note_path)])
        except Exception:
            log.exception("Failed to reveal note: %s", note_path)

    def _reprocess_note(self, note_path: Path | None):
        """Reprocess the session whose note is at note_path."""
        if note_path is None:
            return
        session_dir = note_path.parent
        error_files = list(session_dir.glob("*.error"))
        if not error_files:
            log.warning("No error file found in %s — nothing to reprocess", session_dir)
            self._notify("Meeting Recorder", "Nothing to reprocess",
                         "No error marker found for this session.")
            return
        self._reprocess_session(session_dir, sorted(error_files)[0])

    def _reprocess_session(self, session_dir: Path, error_file: Path):
        """Launch a reprocess pipeline for session_dir (B5: tolerates slug/any folder name)."""
        mic_path = session_dir / "audio-mic.wav"
        sys_path = session_dir / "audio-system.wav"
        dt = resolve_session_dt(session_dir)

        self._call_on_main(self._set_processing)
        threading.Thread(
            target=self._run_pipeline,
            args=(mic_path, sys_path, dt, 0, None, None, error_file, None),
            daemon=True,
        ).start()

    # ---------------------------------------------------------------- menu actions

    def open_output_dir(self, _=None):
        subprocess.Popen(["open", str(self.config.output_dir)])

    def open_prefs(self, _, focus_output_dir: bool = False):
        from ui.settings_window import open_settings_window
        open_settings_window(self, focus_output_dir=focus_output_dir)

    def start_ollama(self, _):
        """Open Terminal running 'ollama serve' (issue #9: Popen, not run)."""
        script = 'tell application "Terminal" to do script "ollama serve"'
        subprocess.Popen(["osascript", "-e", script])
        self._schedule_ollama_probes([2, 5, 10, 20])

    def pull_model(self, _):
        """Open Terminal running 'ollama pull <model>' (issues #8, #9)."""
        model = self.config.ollama_model
        if not _SAFE_MODEL_RE.match(model):
            log.error("Unsafe model name, refusing to interpolate into AppleScript: %r", model)
            self._notify("Meeting Recorder", "Invalid model name",
                         f"Model name {model!r} contains unexpected characters.")
            return
        script = f'tell application "Terminal" to do script "ollama pull {model}"'
        subprocess.Popen(["osascript", "-e", script])
        self._schedule_ollama_probes([5, 15, 30, 60])

    def import_stream_transcript(self, _):
        """Ask for a recording URL, then run the scraper in Terminal.

        Deliberately shells out rather than scraping in-process: the scrape needs
        playwright (which the .app bundle does not ship), a visible browser, a
        manual sign-in and a manually opened transcript panel. Terminal is also
        where the script's diagnostics are readable when a selector breaks.
        """
        from ui.transcript_url_dialog import (  # noqa: PLC0415
            looks_like_stream_url,
            open_transcript_url_dialog,
        )

        def _on_submit(url: str, name: str | None = None,
                       frames_at: str | None = None, context: str | None = None):
            if not looks_like_stream_url(url):
                log.info("URL does not look like a Stream recording; running anyway")
            self._launch_transcript_scrape(url, name=name, frames_at=frames_at, context=context)

        open_transcript_url_dialog(on_submit=_on_submit)

    def import_recording(self, _):
        """Import a local audio or video file and produce a summarised note.

        Runs entirely in-process: no browser, no sign-in, no Terminal window.
        The pipeline runs on a daemon thread so the menu stays responsive during
        long transcriptions.  Progress is shown via _set_processing() and
        reported through _notify() on completion.
        """
        from ui.settings_window import _pick_file  # noqa: PLC0415

        file_types = [
            ["Audio files", ["mp3", "m4a", "aac", "ogg", "flac", "wav", "mp4", "mov", "m4v"]],
        ]
        chosen = _pick_file("Select a recording to import", file_types)
        if not chosen:
            return

        source_path = Path(chosen)

        # Default datetime from the file's mtime
        try:
            mtime = source_path.stat().st_mtime
            default_dt = datetime.fromtimestamp(mtime)
        except OSError:
            default_dt = datetime.now()

        from ui.import_recording_dialog import open_import_recording_dialog  # noqa: PLC0415

        def _on_import(confirmed_dt: datetime, meeting_name: str | None,
                       llm_context: str | None = None):
            self._set_processing()
            log.info(
                "Dispatching import pipeline: file=%s dt=%s name=%s context=%s",
                source_path.name, confirmed_dt, meeting_name,
                f"{len(llm_context)} chars" if llm_context else "none",
            )
            threading.Thread(
                target=self._run_import_pipeline,
                args=(source_path, confirmed_dt, meeting_name, llm_context),
                daemon=True,
            ).start()

        def _on_cancel():
            log.info("Import recording cancelled by user")

        open_import_recording_dialog(
            default_dt=default_dt,
            filename=source_path.name,
            on_import=_on_import,
            on_cancel=_on_cancel,
        )

    def _run_import_pipeline(
        self,
        source_path: Path,
        session_dt: datetime,
        meeting_name: str | None,
        llm_context: str | None = None,
    ):
        """Background thread: prepare audio, then call run_pipeline with single_source."""
        from pipeline.importer import prepare_audio, get_duration_seconds  # noqa: PLC0415
        from pipeline.importer import AudioImportError  # noqa: PLC0415

        try:
            # Step 1: get duration
            try:
                duration_seconds = get_duration_seconds(source_path)
                log.info("Import duration: %ds", duration_seconds)
            except AudioImportError as e:
                log.error("Could not read duration: %s", e)
                duration_seconds = 0  # non-fatal; note will show 0m

            # Step 2: convert to 16 kHz mono WAV if needed
            # Use a temp subdir of the output dir so the converted file is
            # close to the session folder (same volume) for cheap rename later.
            import tempfile
            with tempfile.TemporaryDirectory(
                prefix="mr-import-", dir=self.config.output_dir.parent
            ) as tmp_str:
                tmp_dir = Path(tmp_str)
                try:
                    audio_for_pipeline = prepare_audio(source_path, tmp_dir)
                    log.info("Audio ready for pipeline: %s", audio_for_pipeline)
                except AudioImportError as e:
                    log.error("Audio preparation failed: %s", e)
                    def _ui_prep_fail(msg=str(e)):
                        self._set_idle()
                        self._notify("Meeting Recorder", "Import failed", msg)
                    self._call_on_main(_ui_prep_fail)
                    return

                # Step 3: run the pipeline with single_source
                result = run_pipeline(
                    mic_path=audio_for_pipeline,   # unused when single_source is set
                    system_path=audio_for_pipeline, # unused when single_source is set
                    session_dt=session_dt,
                    duration_seconds=duration_seconds,
                    meeting_name=meeting_name,
                    llm_context=llm_context,
                    output_dir=self.config.output_dir,
                    whisper_binary=self.config.whisper_binary,
                    whisper_model=self.config.whisper_model,
                    ollama_model=self.config.ollama_model,
                    ollama_host=self.config.ollama_host,
                    keep_audio=self.config.keep_audio,
                    ollama_prompt=self.config.ollama_prompt,
                    single_source=audio_for_pipeline,
                    llm=LLMSettings.from_config(self.config),
                )

            # tmp_dir is cleaned up by TemporaryDirectory context manager at this point

            if result.success:
                display_name = (
                    result.meeting_name
                    or (result.session_dir.name if result.session_dir else source_path.stem)
                )
                note_path_str = str(result.note_path) if result.note_path else None

                def _ui_success():
                    self._set_idle()
                    if not result.summary_ok:
                        self.title = "⚠"
                    self._rebuild_meetings_menu()
                    if result.summary_ok:
                        self._notify(
                            "Meeting Recorder", "Import complete", display_name,
                            data={"note_path": note_path_str} if note_path_str else None,
                            action_button="Open",
                        )
                    else:
                        self._notify(
                            "Meeting Recorder", "Import complete (summary unavailable)",
                            result.warning or "No LLM was reachable. Open Meetings menu to retry.",
                        )
                self._call_on_main(_ui_success)
                log.info("Import pipeline complete: %s", result.note_path)
            else:
                def _ui_failure():
                    self._set_idle()
                    self.title = "⚠ Error"
                    self._rebuild_meetings_menu()
                    self._notify(
                        "Meeting Recorder", "Import failed",
                        f"Stage: {result.error_stage}. Open Meetings menu to retry.",
                    )
                self._call_on_main(_ui_failure)

        except Exception as e:
            log.exception("Unexpected import error")
            def _ui_error():
                self._set_idle()
                self.title = "⚠ Error"
                self._notify("Meeting Recorder", "Unexpected import error", str(e))
                self._rebuild_meetings_menu()
            self._call_on_main(_ui_error)

    def _launch_transcript_scrape(
        self,
        url: str,
        name: str | None = None,
        frames_at: str | None = None,
        context: str | None = None,
    ):
        """Open Terminal running the scraper against *url*.

        The URL and optional arguments are written into a temporary shell script
        rather than interpolated into the AppleScript `do script` string: they
        are arbitrary user input, and `do script` would otherwise hand them to
        the shell as code.  Each argument is passed through shlex.quote before
        being embedded in the script body.

        Optional kwargs map to the CLI flags:
            name      → --name
            frames_at → --frames-at
            context   → --context
        """
        script_path = _repo_path("scripts/stream_transcript.py")
        if not Path(script_path).exists():
            self._notify(
                "Meeting Recorder",
                "Scraper not found",
                "scripts/stream_transcript.py is missing from this install.",
            )
            return

        python = _repo_path(".venv/bin/python")
        if not Path(python).exists():
            python = sys.executable

        # Build the optional flag fragments; each value is independently quoted.
        extra_flags = ""
        if name:
            extra_flags += f" --name {shlex.quote(name)}"
        if frames_at:
            extra_flags += f" --frames-at {shlex.quote(frames_at)}"
        if context:
            extra_flags += f" --context {shlex.quote(context)}"

        runner = Path(tempfile.mkdtemp(prefix="mr-scrape-")) / "run.sh"
        runner.write_text(
            "#!/bin/sh\n"
            f"cd {shlex.quote(str(Path(script_path).parent.parent))}\n"
            f"{shlex.quote(python)} {shlex.quote(script_path)} {shlex.quote(url)} --note{extra_flags}\n"
            'status=$?\n'
            'echo\n'
            'if [ $status -ne 0 ]; then echo "Scrape failed — see the error above."; fi\n'
            'echo "Press Return to close."; read _\n'
        )
        runner.chmod(0o700)

        script = f'tell application "Terminal" to do script "{runner}"'
        subprocess.Popen(["osascript", "-e", script])
        self._notify(
            "Meeting Recorder",
            "Scraping transcript",
            "Sign in and open the transcript panel in the Chrome window.",
        )

    def recheck_ollama(self, _):
        """Re-probe Ollama immediately (async)."""
        threading.Thread(target=self._probe_ollama_and_refresh_ui, daemon=True).start()

    def _schedule_ollama_probes(self, delays_seconds: list[int]):
        """Schedule background Ollama probes at the given delays (seconds after now).

        Uses threading.Timer so the probes run off the main thread and UI updates
        are marshalled back through _call_on_main / AppHelper.callAfter.
        """
        for delay in delays_seconds:
            threading.Timer(
                delay,
                self._probe_ollama_and_refresh_ui,
            ).start()


# ---------------------------------------------------------------------------
# Pure helper: find session dirs that have *.error but no meeting.md (issue #3)
# ---------------------------------------------------------------------------

def _find_error_only_dirs(output_dir: Path) -> list[Path]:
    """Return session dirs that have at least one *.error file but no meeting.md.

    These are sessions that failed before a note was written (transcribe/setup
    stage failures). They won't appear in list_notes() output.
    """
    if not output_dir.exists():
        return []
    result = []
    for error_file in output_dir.rglob("*.error"):
        d = error_file.parent
        if not (d / "meeting.md").exists():
            if d not in result:
                result.append(d)
    return sorted(result)
