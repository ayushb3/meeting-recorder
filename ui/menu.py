# ui/menu.py
import logging
import re
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
from summarizer.ollama import OllamaStatus, check_status
from notes.writer import list_notes

log = logging.getLogger(__name__)

# Validated characters for an Ollama model name — safe to embed in AppleScript.
_SAFE_MODEL_RE = re.compile(r'^[A-Za-z0-9._:/@-]+$')


def _bundle_resource(rel_path: str) -> str:
    """Resolve a resource path that works in dev mode and inside the .app bundle."""
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        return str(Path(sys._MEIPASS) / rel_path)
    return str(Path(__file__).parent.parent / rel_path)


ICON_IDLE = _bundle_resource("assets/icon.png")
ICON_RECORDING = _bundle_resource("assets/icon-recording.png")


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

        # Cached Ollama status — probed on a background thread, never on draw
        self._ollama_status: OllamaStatus | None = None
        self._ollama_status_lock = threading.Lock()

        # Hero action item
        self._record_item = rumps.MenuItem("● Start Recording", callback=self.toggle_recording)

        # ---- Meetings submenu (rebuilt on timer + pipeline completion) ----
        self._meetings_menu = rumps.MenuItem("Meetings")

        # ---- Ollama submenu ----
        self._ollama_status_item = rumps.MenuItem("Checking…", callback=None)
        self._ollama_detail_item = rumps.MenuItem("", callback=None)
        self._ollama_start_item = rumps.MenuItem("Start Ollama in Terminal", callback=self.start_ollama)
        self._ollama_pull_item = rumps.MenuItem("Pull Model…", callback=self.pull_model)
        self._ollama_recheck_item = rumps.MenuItem("Re-check Now", callback=self.recheck_ollama)
        self._ollama_menu = rumps.MenuItem("Ollama")

        # ---- Location items (shown at bottom of Meetings submenu) ----
        self._location_caption = rumps.MenuItem("", callback=None)

        # ---- Top-level Settings / Quit ----
        self._settings_item = rumps.MenuItem("Settings…", callback=self.open_prefs)
        self._quit_item = rumps.MenuItem("Quit", callback=rumps.quit_application)

        self._build_static_submenus()
        self._build_top_menu()
        self._set_idle()

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
        self.title = "Processing..."
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
        )
        self._recorder.start()
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

        # Window 1: meeting name
        name_win = rumps.Window(
            message="Meeting name (used for folder and note title):",
            title="Meeting Saved",
            default_text="",
            ok="Next",
            cancel="Skip",
            dimensions=(320, 24),
        )
        name_resp = name_win.run()
        meeting_name: str | None = name_resp.text.strip() if name_resp.clicked else None

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

        self._set_processing()
        log.info("Dispatching pipeline: duration=%ds name=%s context=%s", duration, meeting_name, llm_context)
        threading.Thread(
            target=self._run_pipeline,
            args=(mic_path, sys_path, session_dt, duration, meeting_name, llm_context, None, tmp_dir),
            daemon=True,
        ).start()

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
                            result.warning or "Ollama was not reachable. Open Meetings menu to retry.",
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
        """Run a blocking Ollama probe on a background thread, then marshal UI update to main."""
        status = check_status(self.config.ollama_model, self.config.ollama_host)
        with self._ollama_status_lock:
            self._ollama_status = status
        # Marshal UI mutation to main thread (issue #2)
        self._call_on_main(self._update_ollama_ui, status)

    def _on_poll_timer(self, timer):
        """rumps.Timer callback (main thread): kick off Ollama probe + menu refresh."""
        self._rebuild_meetings_menu()
        threading.Thread(target=self._probe_ollama_and_refresh_ui, daemon=True).start()

    def _update_ollama_ui(self, status: OllamaStatus):
        """Update Ollama submenu. MUST be called on the main thread."""
        if status.ready:
            self._ollama_status_item.title = "🟢 Running"
            self._ollama_detail_item.title = f"{status.model} · {status.host}"
        elif status.reachable:
            self._ollama_status_item.title = "🟡 Running — model not pulled"
            self._ollama_detail_item.title = f"{status.model} not found at {status.host}"
        else:
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
        items.append(rumps.MenuItem("Open Meetings Folder", callback=self.open_output_dir))
        items.append(rumps.MenuItem("Change Location…", callback=self.open_prefs))

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
                "Open Note",
                callback=lambda _, p=note_path: self._open_note_checked(p),
            )
            retry_item = rumps.MenuItem(
                "Retry Summary",
                callback=lambda _, p=note_path: self._reprocess_note(p),
            )
            reveal_item = rumps.MenuItem(
                "Reveal in Finder",
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

    def open_prefs(self, _):
        from config import USER_CONFIG_PATH, ensure_user_config
        ensure_user_config()
        subprocess.Popen(["open", str(USER_CONFIG_PATH)])

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
