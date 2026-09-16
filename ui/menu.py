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

from config import Config
from pipeline.processor import run_pipeline
from recorder.audio import AudioRecorder
from summarizer.ollama import OllamaStatus, check_status
from notes.writer import list_notes

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


# ---------------------------------------------------------------------------
# Pure helper: group list_notes() output into day sections for the Meetings menu
# ---------------------------------------------------------------------------

def display_label(entry: dict) -> str:
    """Derive a human-readable display label for a meeting note entry.

    Fallback chain:
      a. frontmatter ``title`` if non-empty
      b. slug portion of folder name, de-slugified (title-case words)
         Handles both ``YYYY-MM-DD-HHhMM`` (bare timestamp) and
         ``YYYY-MM-DD-HHhMM-slug`` (timestamp + slug) folder names.
      c. H1 heading from the note body if cheaply available
      d. folder name as-is
    """
    title = (entry.get("title") or "").strip()
    if title:
        return title

    # Try to derive from folder name
    note_path: Path | None = entry.get("path")
    if note_path is not None:
        folder = note_path.parent.name
        # Strip leading YYYY-MM-DD-HHhMM (with optional trailing -) to expose the slug
        stripped = re.sub(r"^\d{4}-\d{2}-\d{2}-\d{2}h\d{2}-?", "", folder)
        if stripped:
            # De-slugify: hyphens → spaces, title-case
            return stripped.replace("-", " ").title()
        # Folder is a bare timestamp with no slug; try H1 from note body
        try:
            text = note_path.read_text(encoding="utf-8", errors="ignore")
            for line in text.splitlines():
                line = line.strip()
                if line.startswith("# "):
                    return line[2:].strip()
        except OSError:
            pass
        # Last resort: the folder name itself
        return folder

    return "Untitled"


def group_notes_for_menu(notes: list[dict], today: date | None = None) -> list[dict]:
    """Group note entries into sections suitable for building the Meetings submenu.

    Returns a list of section dicts:
        {
          "label": str,          # caption text, e.g. "Today — Tue 16 Sep" or "Earlier this week"
          "entries": [...]       # the note dicts that belong to this section
        }

    Sections:
      - "today": one section labelled "Today — <weekday> <day> <month>"
      - "earlier": one section labelled "Earlier this week" for the remaining entries

    Only entries with a valid `date` field are included. Entries with no date
    fall into the "earlier" bucket regardless.
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

    sections: list[dict] = []
    today_label = "Today — " + today.strftime("%a %-d %b")
    sections.append({"label": today_label, "entries": today_entries})
    if earlier_entries:
        sections.append({"label": "Earlier this week", "entries": earlier_entries})
    return sections


@rumps.notifications
def notification_handler(info):
    """Handle notification clicks — open the note path stored in `data`."""
    try:
        path = info.get("note_path") if isinstance(info, dict) else None
        if path:
            subprocess.run(["open", str(path)])
    except Exception:
        log.exception("notification_handler error")


class MeetingRecorderApp(rumps.App):
    def __init__(self, config: Config):
        super().__init__("", icon=ICON_IDLE, template=True, quit_button=None)
        self.config = config
        self._recorder: AudioRecorder | None = None
        self._recording = False
        self._recording_lock = threading.Lock()
        self._timer_thread: threading.Thread | None = None
        self._session_dt: datetime | None = None
        self._meeting_name: str | None = None
        self._pending_stop: tuple | None = None

        # Cached Ollama status — never probe on every draw
        self._ollama_status: OllamaStatus | None = None
        self._ollama_status_lock = threading.Lock()

        # Hero action item
        self._record_item = rumps.MenuItem("● Start Recording", callback=self.toggle_recording)

        # ---- Meetings submenu (rebuilt on open) ----
        self._meetings_menu = rumps.MenuItem("Meetings")

        # ---- Ollama submenu ----
        self._ollama_status_item = rumps.MenuItem("Checking…", callback=None)
        self._ollama_detail_item = rumps.MenuItem("", callback=None)
        self._ollama_start_item = rumps.MenuItem("Start Ollama in Terminal", callback=self.start_ollama)
        self._ollama_pull_item = rumps.MenuItem("Pull Model…", callback=self.pull_model)
        self._ollama_recheck_item = rumps.MenuItem("Re-check Now", callback=self.recheck_ollama)
        self._ollama_menu = rumps.MenuItem("Ollama")

        # ---- Save Location submenu ----
        self._location_caption = rumps.MenuItem("", callback=None)
        self._save_location_menu = rumps.MenuItem("Save Location")

        # ---- Top-level Settings / Quit ----
        self._settings_item = rumps.MenuItem("Settings…", callback=self.open_prefs)
        self._quit_item = rumps.MenuItem("Quit", callback=rumps.quit_application)

        self._build_static_submenus()
        self._build_top_menu()
        self._set_idle()

        # Start Ollama status poll (every 45s)
        self._ollama_poll_timer = rumps.Timer(self._poll_ollama_status, 45)
        self._ollama_poll_timer.start()
        # Do an initial async probe so we don't stall startup
        threading.Thread(target=self._probe_ollama, daemon=True).start()

    # ---------------------------------------------------------------- menu building

    def _build_static_submenus(self):
        """Build the Ollama and Save Location submenus (static structure)."""
        self._ollama_menu.update([
            self._ollama_status_item,
            self._ollama_detail_item,
            None,
            self._ollama_start_item,
            self._ollama_pull_item,
            self._ollama_recheck_item,
        ])

        self._location_caption.title = self._short_location()
        self._save_location_menu.update([
            self._location_caption,
            None,
            rumps.MenuItem("Open Meetings Folder", callback=self.open_output_dir),
            rumps.MenuItem("Change Location…", callback=self.open_prefs),
        ])

    def _build_top_menu(self):
        """Set the root menu (fixed size — never grows with meeting count)."""
        self.menu = [
            self._record_item,
            None,
            self._meetings_menu,
            self._ollama_menu,
            self._save_location_menu,
            None,
            self._settings_item,
            self._quit_item,
        ]

    def _short_location(self) -> str:
        """Return a ~/ abbreviated path for the save location caption."""
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

    def _set_idle(self):
        self.title = ""
        self.icon = ICON_IDLE
        self._record_item.title = "● Start Recording"
        self._record_item.set_callback(self.toggle_recording)

    def _set_recording(self):
        self.icon = ICON_RECORDING
        # Hero item updated by timer loop

    def _has_error_files(self) -> bool:
        if not self.config.output_dir.exists():
            return False
        return bool(list(self.config.output_dir.rglob("*.error")))

    # ---------------------------------------------------------------- notification helper (B4)

    def _notify(self, title: str, subtitle: str, message: str,
                data: dict | None = None, action_button: str | None = None):
        """Wrap rumps.notification so it never raises when running unbundled.

        rumps.notification raises RuntimeError when there is no CFBundleIdentifier
        (i.e. when running `python app.py` without a built .app). Catch it and log
        instead so a successful run does not get reported as a failure.
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

    # ---------------------------------------------------------------- recording toggle

    def toggle_recording(self, sender):
        if not self._recording:
            self._start_recording()
        else:
            self._stop_recording()

    def _start_recording(self):
        # Warn if Ollama is not ready (never block — recording always works)
        with self._ollama_status_lock:
            status = self._ollama_status
        if status is not None and not status.ready:
            self._notify(
                "Meeting Recorder",
                "Ollama not available",
                "Recording will proceed but the summary will be unavailable. "
                "Start Ollama from the menu to enable summaries.",
            )

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
            mic_threshold=self.config.mic_threshold,
        )
        self._recorder.start()
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
        self._record_item.title = "● Start Recording"
        log.info("Recording stopped: duration=%ds", duration)

        mic_path = self._recorder.mic_path
        sys_path = self._recorder.system_path
        session_dt = self._session_dt
        meeting_name = self._meeting_name

        if duration < self.config.min_recording_seconds:
            log.warning("Recording too short (%ds < %ds) — discarded.", duration, self.config.min_recording_seconds)
            self._notify("Meeting Recorder", "", "Recording too short — discarded.")
            shutil.rmtree(self._tmp_dir, ignore_errors=True)
            self._set_idle()
            return

        # Show name + context modal on main thread before dispatching
        self._pending_stop = (mic_path, sys_path, session_dt, duration, meeting_name)
        rumps.Timer(self._show_stop_modal, 0.1).start()

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
                ollama_prompt=self.config.ollama_prompt,
            )
            if result.success:
                # Only remove the error marker when the summary actually succeeded;
                # a degraded reprocess must leave the marker so reprocess stays enabled.
                if error_file and result.summary_ok:
                    error_file.unlink(missing_ok=True)
                self.title = "" if result.summary_ok else "⚠"
                self._set_idle()
                log.info("Pipeline complete: %s", result.note_path)
                display_name = (
                    result.meeting_name
                    or (result.session_dir.name if result.session_dir else "Done")
                )
                if result.summary_ok:
                    note_path = str(result.note_path) if result.note_path else None
                    self._notify(
                        "Meeting Recorder",
                        "Note saved",
                        display_name,
                        data={"note_path": note_path} if note_path else None,
                        action_button="Open",
                    )
                else:
                    self._notify(
                        "Meeting Recorder",
                        "Note saved (summary unavailable)",
                        result.warning or "Ollama was not reachable. Use Meetings menu to retry.",
                    )
            else:
                self.title = "⚠ Error"
                self._set_idle()  # B3: always re-enable reprocess on failure
                self._notify(
                    "Meeting Recorder", "Processing failed",
                    f"Stage: {result.error_stage}. Click menu to reprocess."
                )
        except Exception as e:
            log.exception("Unexpected pipeline error")
            self.title = "⚠ Error"
            self._notify("Meeting Recorder", "Unexpected error", str(e))
            self._set_idle()  # B3: always re-enable reprocess on failure
        finally:
            # Clean up temp dir if it still exists (pipeline moves files out on success)
            if hasattr(self, '_tmp_dir') and self._tmp_dir and self._tmp_dir.exists():
                shutil.rmtree(self._tmp_dir, ignore_errors=True)

    # ---------------------------------------------------------------- timer

    def _update_timer(self):
        while self._recording:
            elapsed = int(self._recorder.elapsed_seconds())
            m, s = divmod(elapsed, 60)
            elapsed_str = f"{m:02d}:{s:02d}"
            self.title = elapsed_str
            self._record_item.title = f"■ Stop Recording — {elapsed_str}"

            disk_check_path = self.config.output_dir if self.config.output_dir.exists() else Path.home()
            stat = shutil.disk_usage(disk_check_path)
            free_mb = stat.free // (1024 * 1024)
            if free_mb < self.config.low_disk_threshold_mb:
                self._stop_recording()
                self._notify("Meeting Recorder", "Disk space low", "Recording stopped.")
                break
            time.sleep(1)

    # ---------------------------------------------------------------- Ollama status

    def _probe_ollama(self):
        """Run a blocking Ollama probe and cache the result. Safe to call from a thread."""
        status = check_status(self.config.ollama_model, self.config.ollama_host)
        with self._ollama_status_lock:
            self._ollama_status = status
        self._update_ollama_ui(status)

    def _poll_ollama_status(self, timer):
        """rumps.Timer callback: re-probe in background thread."""
        threading.Thread(target=self._probe_ollama, daemon=True).start()

    def _update_ollama_ui(self, status: OllamaStatus):
        """Update the Ollama submenu items from a fresh status (may be called from any thread)."""
        if status.ready:
            self._ollama_status_item.title = "● Running"
            self._ollama_detail_item.title = f"{status.model} · {status.host}"
        elif status.reachable:
            self._ollama_status_item.title = "⚠ Running — model not pulled"
            self._ollama_detail_item.title = f"{status.model} not found at {status.host}"
        else:
            self._ollama_status_item.title = "○ Not running"
            self._ollama_detail_item.title = f"No server at {status.host}"

    # ---------------------------------------------------------------- menu open hook

    def menuOpened_(self, menu):
        """Called by rumps/AppKit when the user clicks the menu bar icon.

        Rebuild the Meetings submenu with fresh data from list_notes().
        Re-probe Ollama using the cache (no blocking).
        """
        self._rebuild_meetings_menu()
        # Re-probe Ollama in background so status is fresh on next open
        threading.Thread(target=self._probe_ollama, daemon=True).start()

    # ---------------------------------------------------------------- meetings submenu

    def _rebuild_meetings_menu(self):
        """Rebuild the Meetings submenu from list_notes() output."""
        try:
            notes = list_notes(self.config.output_dir, weeks=2)
        except Exception:
            log.exception("list_notes failed")
            notes = []

        sections = group_notes_for_menu(notes)
        items = []
        for i, section in enumerate(sections):
            if section["entries"] or i == 0:
                # Always show the Today section even if empty
                caption = rumps.MenuItem(section["label"], callback=None)
                items.append(caption)
                for entry in section["entries"]:
                    items.append(self._make_note_item(entry))
            if i < len(sections) - 1 and section["entries"]:
                items.append(None)

        items.append(None)
        browse_item = rumps.MenuItem("Browse All Meetings…", callback=self.open_output_dir)
        items.append(browse_item)

        self._meetings_menu.clear()
        self._meetings_menu.update(items)

    def _make_note_item(self, entry: dict) -> "rumps.MenuItem | tuple":
        """Build a single note menu item (possibly with a Retry submenu)."""
        time_str = entry.get("time", "")
        degraded = entry.get("degraded", False)
        note_path = entry.get("path")

        readable = display_label(entry)
        label = f"⚠ {readable}" if degraded else (
            f"{time_str}  {readable}" if time_str else readable
        )

        if degraded:
            # Build a sub-item for retry
            main_item = rumps.MenuItem(label, callback=lambda _, p=note_path: self._open_note(p))
            retry_item = rumps.MenuItem(
                "Retry summary",
                callback=lambda _, p=note_path: self._reprocess_note(p),
            )
            reveal_item = rumps.MenuItem(
                "Reveal in Finder",
                callback=lambda _, p=note_path: self._reveal_in_finder(p),
            )
            main_item.update([retry_item, reveal_item])
            return main_item
        else:
            item = rumps.MenuItem(label, callback=lambda _, p=note_path: self._open_note(p))
            return item

    def _open_note(self, note_path: Path | None):
        if note_path is None:
            return
        try:
            subprocess.run(["open", str(note_path)])
        except Exception:
            log.exception("Failed to open note: %s", note_path)

    def _reveal_in_finder(self, note_path: Path | None):
        if note_path is None:
            return
        try:
            subprocess.run(["open", "-R", str(note_path)])
        except Exception:
            log.exception("Failed to reveal note: %s", note_path)

    def _reprocess_note(self, note_path: Path | None):
        """Reprocess the session containing note_path."""
        if note_path is None:
            return
        session_dir = note_path.parent
        error_files = list(session_dir.glob("*.error"))
        if not error_files:
            log.warning("No error file found in %s — nothing to reprocess", session_dir)
            return
        error_file = sorted(error_files)[0]
        self._reprocess_session(session_dir, error_file)

    def reprocess(self, _):
        """Reprocess the most recent errored session (legacy global action)."""
        error_files = list(self.config.output_dir.rglob("*.error"))
        if not error_files:
            return
        error_file = sorted(error_files)[-1]
        session_dir = error_file.parent
        self._reprocess_session(session_dir, error_file)

    def _reprocess_session(self, session_dir: Path, error_file: Path):
        """Start a reprocess pipeline for session_dir, tolerating slug-named folders (B5)."""
        session_name = session_dir.name
        mic_path = session_dir / "audio-mic.wav"
        sys_path = session_dir / "audio-system.wav"

        # Parse timestamp from start of session name (YYYY-MM-DD-HHhMM).
        # Fall back to note frontmatter, then dir mtime when it's a slug folder.
        dt: datetime | None = None
        try:
            dt = datetime.strptime(session_name[:16], "%Y-%m-%d-%Hh%M")
        except ValueError:
            pass

        if dt is None:
            # Try the note's frontmatter date/time
            note_path = session_dir / "meeting.md"
            if note_path.exists():
                try:
                    from notes.writer import _frontmatter
                    fields = _frontmatter(note_path)
                    date_str = fields.get("date", "")
                    time_str = fields.get("time", "")
                    if date_str and time_str:
                        dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
                except Exception:
                    log.warning("Could not parse frontmatter date from %s", note_path)

        if dt is None:
            # Last resort: directory mtime
            dt = datetime.fromtimestamp(session_dir.stat().st_mtime)
            log.info("Falling back to mtime for reprocess dt: %s → %s", session_dir.name, dt)

        self.title = "Processing..."
        threading.Thread(
            target=self._run_pipeline,
            args=(mic_path, sys_path, dt, 0, None, None),
            kwargs={"error_file": error_file},
            daemon=True,
        ).start()

    # ---------------------------------------------------------------- menu actions

    def open_output_dir(self, _=None):
        subprocess.run(["open", str(self.config.output_dir)])

    def open_prefs(self, _):
        from config import USER_CONFIG_PATH, ensure_user_config
        ensure_user_config()
        subprocess.run(["open", str(USER_CONFIG_PATH)])

    def start_ollama(self, _):
        """Open Terminal running 'ollama serve'."""
        script = "tell application \"Terminal\" to do script \"ollama serve\""
        subprocess.run(["osascript", "-e", script])

    def pull_model(self, _):
        """Open Terminal running 'ollama pull <model>'."""
        model = self.config.ollama_model
        script = f"tell application \"Terminal\" to do script \"ollama pull {model}\""
        subprocess.run(["osascript", "-e", script])

    def recheck_ollama(self, _):
        """Re-probe Ollama immediately (async)."""
        threading.Thread(target=self._probe_ollama, daemon=True).start()
