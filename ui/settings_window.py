# ui/settings_window.py
"""
Native AppKit Settings window for Meeting Recorder.

All AppKit code runs on the main thread (enforced by callers via _call_on_main /
callAfter).  Pure helper functions at module level are fully unit-testable without
an AppKit event loop.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

# Module-level strong reference to the settings window and delegate so they
# survive GC (NSWindow doesn't allow arbitrary Python attribute assignment).
_active_window = None
_active_delegate = None

# ---------------------------------------------------------------------------
# Pure helpers — no AppKit dependency, fully unit-testable
# ---------------------------------------------------------------------------

def build_device_list(available_names: list[str], configured_value: str) -> list[str]:
    """Return an ordered list of device names for a popup button.

    Rules:
    - Only input devices (caller is responsible for pre-filtering).
    - The configured value is always included; if it is absent from
      *available_names* it is appended with a "(not connected)" suffix.
    - Preserves the order of *available_names*.

    Returns a plain list of display strings.
    """
    if configured_value in available_names:
        return list(available_names)

    # configured device not currently connected — append it with a marker
    marked = f"{configured_value} (not connected)"
    return list(available_names) + [marked]


def selected_device_value(display_string: str) -> str:
    """Strip the '(not connected)' marker from a display string to get the raw value."""
    suffix = " (not connected)"
    if display_string.endswith(suffix):
        return display_string[: -len(suffix)]
    return display_string


def validate_settings(fields: dict) -> list[str]:
    """Validate a dict of field values before saving.

    Returns a list of human-readable error strings (empty = valid).

    *fields* keys correspond to Config fields:
        output_dir, system_device, mic_device, whisper_model, whisper_binary,
        ollama_model, ollama_host, keep_audio, min_recording_seconds,
        low_disk_threshold_mb, mic_threshold, ollama_prompt
    """
    errors: list[str] = []

    output_dir_raw = str(fields.get("output_dir", "")).strip()
    if not output_dir_raw:
        errors.append("Output folder must not be empty.")
    else:
        output_dir = Path(output_dir_raw).expanduser()
        if not output_dir.exists():
            errors.append(f"Output folder does not exist: {output_dir}")

    whisper_binary = Path(fields.get("whisper_binary", ""))
    if str(whisper_binary).strip() and not whisper_binary.exists():
        errors.append(f"Whisper binary not found: {whisper_binary}")

    whisper_model = Path(fields.get("whisper_model", ""))
    if str(whisper_model).strip() and not whisper_model.exists():
        errors.append(f"Whisper model not found: {whisper_model}")

    prompt = fields.get("ollama_prompt") or ""
    if prompt.strip() and "{transcript}" not in prompt:
        errors.append("Ollama prompt must contain {transcript} if non-empty.")

    try:
        min_sec = int(fields.get("min_recording_seconds", 0))
        if min_sec < 0:
            errors.append("Minimum recording seconds must be >= 0.")
    except (ValueError, TypeError):
        errors.append("Minimum recording seconds must be an integer.")

    try:
        low_disk = int(fields.get("low_disk_threshold_mb", 0))
        if low_disk < 0:
            errors.append("Low disk threshold must be >= 0.")
    except (ValueError, TypeError):
        errors.append("Low disk threshold must be an integer.")

    try:
        mic_thr = int(fields.get("mic_threshold", 0))
        if not (0 <= mic_thr <= 32767):
            errors.append("Mic threshold must be between 0 and 32767.")
    except (ValueError, TypeError):
        errors.append("Mic threshold must be an integer.")

    return errors


def build_toml_text(fields: dict) -> str:
    """Serialise *fields* to a TOML string with explanatory comments.

    tomllib is read-only so we cannot round-trip comments; instead we always
    emit the standard comments from config.template.toml so a saved file is
    at least as documented as the original template.
    """
    def _escape(s: str) -> str:
        return s.replace("\\", "\\\\").replace('"', '\\"')

    output_dir = str(fields.get("output_dir", ""))
    system_device = _escape(str(fields.get("system_device", "")))
    mic_device = _escape(str(fields.get("mic_device", "")))
    whisper_model = _escape(str(fields.get("whisper_model", "")))
    whisper_binary = _escape(str(fields.get("whisper_binary", "")))
    ollama_model = _escape(str(fields.get("ollama_model", "")))
    ollama_host = _escape(str(fields.get("ollama_host", "")))
    keep_audio = "true" if fields.get("keep_audio") else "false"
    min_rec = int(fields.get("min_recording_seconds", 30))
    low_disk = int(fields.get("low_disk_threshold_mb", 500))
    mic_thr = int(fields.get("mic_threshold", 300))

    lines = [
        "[paths]",
        "# Where meeting notes and audio are saved (Obsidian vault subfolder recommended)",
        f'output_dir = "{_escape(output_dir)}"',
        "",
        "[audio]",
        '# Run: python3 -c "import sounddevice; print(sounddevice.query_devices())" to list devices',
        f'system_device = "{system_device}"',
        f'mic_device = "{mic_device}"',
        "",
        "[whisper]",
        "# Install: brew install whisper-cpp",
        "# Download model: whisper-cpp --download-model large-v3  (or adjust path below)",
        f'model = "{whisper_model}"',
        f'binary = "{whisper_binary}"',
        "",
        "[ollama]",
        "# Install: brew install ollama && ollama pull llama3.1:8b",
        f'model = "{ollama_model}"',
        f'host = "{ollama_host}"',
    ]

    prompt = fields.get("ollama_prompt") or ""
    if prompt.strip():
        # Use multiline TOML string; escape any embedded triple-quote sequences
        safe_prompt = prompt.replace('"""', '""\\\"')
        lines += [
            "# Optional custom prompt — must contain {transcript}",
            f'prompt = """\n{safe_prompt}\n"""',
        ]

    lines += [
        "",
        "[processing]",
        f"keep_audio = {keep_audio}",
        f"min_recording_seconds = {min_rec}",
        f"low_disk_threshold_mb = {low_disk}",
        "# Mic RMS threshold (0-32767). Frames below this are silenced (suppresses speaker backwash).",
        "# Raise if you still hear bleed; lower if your own voice is being cut off.",
        f"mic_threshold = {mic_thr}",
    ]

    return "\n".join(lines) + "\n"


def atomic_save_config(path: Path, toml_text: str) -> None:
    """Write *toml_text* to *path* safely.

    Steps:
    1. Back up the existing file to ``path.with_suffix('.toml.bak')`` (single
       rolling backup).  A backup failure is logged but never blocks the save.
    2. Write to a sibling temp file in the same directory.
    3. ``os.replace()`` the temp file onto *path* — atomic on the same
       filesystem, so an interrupted write cannot leave a truncated config.

    Raises ``OSError`` on write or rename failure (caller shows an alert).
    """
    import os
    import tempfile

    path.parent.mkdir(parents=True, exist_ok=True)

    # Step 1: backup
    if path.exists():
        bak = path.parent / (path.name + ".bak")
        try:
            import shutil
            shutil.copy2(str(path), str(bak))
            log.info("Config backed up to %s", bak)
        except Exception:
            log.warning("Could not back up config to %s — continuing anyway", bak, exc_info=True)

    # Step 2+3: atomic write
    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(toml_text)
        os.replace(tmp_path, str(path))
    except Exception:
        # Clean up temp file if replace failed
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def query_input_devices() -> list[str]:
    """Return names of currently connected input devices (max_input_channels > 0)."""
    try:
        import sounddevice as sd  # type: ignore
        devices = sd.query_devices()
        return [d["name"] for d in devices if d.get("max_input_channels", 0) > 0]
    except Exception:
        log.warning("Could not query sounddevice input devices", exc_info=True)
        return []


# ---------------------------------------------------------------------------
# AppKit window — only imported / instantiated on macOS inside the real app
# ---------------------------------------------------------------------------

def _clear_active_refs() -> None:
    global _active_window, _active_delegate
    _active_window = None
    _active_delegate = None


def open_settings_window(app_instance, focus_output_dir: bool = False) -> None:
    """Create and show the Settings window on the current (main) thread.

    *app_instance* is the MeetingRecorderApp rumps.App instance; we read
    app_instance.config and write back after a successful save.
    """
    try:
        _open_settings_window_impl(app_instance, focus_output_dir=focus_output_dir)
    except Exception:
        log.exception("Failed to open Settings window")
        _show_alert("Could not open Settings", "An unexpected error occurred. Check the log for details.")


def _show_alert(title: str, message: str) -> None:
    try:
        from AppKit import NSAlert  # type: ignore
        alert = NSAlert.alloc().init()
        alert.setMessageText_(title)
        alert.setInformativeText_(message)
        alert.addButtonWithTitle_("OK")
        alert.runModal()
    except Exception:
        log.error("Alert: %s — %s", title, message)


def _open_settings_window_impl(app_instance, focus_output_dir: bool = False) -> None:
    from AppKit import (  # type: ignore
        NSApp,
        NSWindow,
        NSBackingStoreBuffered,
        NSWindowStyleMaskTitled,
        NSWindowStyleMaskClosable,
        NSWindowStyleMaskResizable,
        NSWindowStyleMaskMiniaturizable,
        NSTextField,
        NSSecureTextField,
        NSButton,
        NSButtonTypeMomentaryPushIn,
        NSButtonTypeSwitch,
        NSPopUpButton,
        NSAlert,
        NSScrollView,
        NSTextView,
        NSFont,
        NSColor,
        NSMakeRect,
        NSMakeSize,
    )
    from Foundation import NSObject, NSMakeRange  # type: ignore
    import objc  # type: ignore

    cfg = app_instance.config

    # ---- Window ----
    WINDOW_W, WINDOW_H = 560, 720
    style = (
        NSWindowStyleMaskTitled
        | NSWindowStyleMaskClosable
        | NSWindowStyleMaskResizable
        | NSWindowStyleMaskMiniaturizable
    )
    window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        NSMakeRect(0, 0, WINDOW_W, WINDOW_H),
        style,
        NSBackingStoreBuffered,
        False,
    )
    window.setTitle_("Meeting Recorder — Settings")
    window.center()
    window.setReleasedWhenClosed_(False)

    content = window.contentView()

    # ---- Layout helpers ----
    y_cursor = [WINDOW_H - 20]  # mutable via list so nested fns can mutate

    def next_y(height: int, gap: int = 8) -> float:
        y_cursor[0] -= height + gap
        return y_cursor[0]

    def add_section_label(text: str) -> None:
        y = next_y(22, gap=14)
        lbl = NSTextField.alloc().initWithFrame_(NSMakeRect(16, y, WINDOW_W - 32, 18))
        lbl.setStringValue_(text.upper())
        lbl.setBezeled_(False)
        lbl.setDrawsBackground_(False)
        lbl.setEditable_(False)
        lbl.setSelectable_(False)
        lbl.setTextColor_(NSColor.secondaryLabelColor())
        lbl.setFont_(NSFont.boldSystemFontOfSize_(10))
        content.addSubview_(lbl)

    def add_label(text: str, y: float, width: float = 160) -> None:
        lbl = NSTextField.alloc().initWithFrame_(NSMakeRect(16, y + 2, width, 17))
        lbl.setStringValue_(text)
        lbl.setBezeled_(False)
        lbl.setDrawsBackground_(False)
        lbl.setEditable_(False)
        lbl.setSelectable_(False)
        lbl.setFont_(NSFont.systemFontOfSize_(13))
        content.addSubview_(lbl)

    LABEL_W = 170
    CTRL_X = 190
    CTRL_W = WINDOW_W - CTRL_X - 16

    # ---- Storage: output_dir ----
    add_section_label("Recording Storage")

    y = next_y(24)
    add_label("Output Folder", y, LABEL_W)

    output_dir_label = NSTextField.alloc().initWithFrame_(NSMakeRect(CTRL_X, y + 2, CTRL_W - 80, 20))
    output_dir_label.setStringValue_(str(cfg.output_dir))
    output_dir_label.setBezeled_(False)
    output_dir_label.setDrawsBackground_(False)
    output_dir_label.setEditable_(False)
    output_dir_label.setSelectable_(True)
    output_dir_label.setFont_(NSFont.systemFontOfSize_(12))
    output_dir_label.setTextColor_(NSColor.secondaryLabelColor())
    content.addSubview_(output_dir_label)

    choose_folder_btn = NSButton.alloc().initWithFrame_(NSMakeRect(CTRL_X + CTRL_W - 74, y, 74, 24))
    choose_folder_btn.setTitle_("Choose…")
    choose_folder_btn.setBezelStyle_(1)  # NSBezelStyleRounded
    content.addSubview_(choose_folder_btn)

    # ---- Audio Devices ----
    add_section_label("Audio Devices")

    input_devices = query_input_devices()

    y = next_y(24)
    add_label("System Audio Device", y, LABEL_W)
    system_device_popup = NSPopUpButton.alloc().initWithFrame_(NSMakeRect(CTRL_X, y, CTRL_W, 24))
    sys_items = build_device_list(input_devices, cfg.system_device)
    for item in sys_items:
        system_device_popup.addItemWithTitle_(item)
    # Select configured item (or its (not connected) version)
    if cfg.system_device in sys_items:
        system_device_popup.selectItemWithTitle_(cfg.system_device)
    else:
        system_device_popup.selectItemWithTitle_(f"{cfg.system_device} (not connected)")
    content.addSubview_(system_device_popup)

    y = next_y(24)
    add_label("Microphone Device", y, LABEL_W)
    mic_device_popup = NSPopUpButton.alloc().initWithFrame_(NSMakeRect(CTRL_X, y, CTRL_W, 24))
    mic_items = build_device_list(input_devices, cfg.mic_device)
    for item in mic_items:
        mic_device_popup.addItemWithTitle_(item)
    if cfg.mic_device in mic_items:
        mic_device_popup.selectItemWithTitle_(cfg.mic_device)
    else:
        mic_device_popup.selectItemWithTitle_(f"{cfg.mic_device} (not connected)")
    content.addSubview_(mic_device_popup)

    y = next_y(24)
    add_label("Mic Threshold (0–32767)", y, LABEL_W)
    mic_threshold_field = NSTextField.alloc().initWithFrame_(NSMakeRect(CTRL_X, y, 80, 22))
    mic_threshold_field.setStringValue_(str(cfg.mic_threshold))
    content.addSubview_(mic_threshold_field)

    mic_threshold_note = NSTextField.alloc().initWithFrame_(NSMakeRect(CTRL_X + 86, y + 3, CTRL_W - 86, 16))
    mic_threshold_note.setStringValue_("Raise to suppress speaker bleed")
    mic_threshold_note.setBezeled_(False)
    mic_threshold_note.setDrawsBackground_(False)
    mic_threshold_note.setEditable_(False)
    mic_threshold_note.setSelectable_(False)
    mic_threshold_note.setFont_(NSFont.systemFontOfSize_(11))
    mic_threshold_note.setTextColor_(NSColor.tertiaryLabelColor())
    content.addSubview_(mic_threshold_note)

    # ---- Transcription ----
    add_section_label("Transcription")

    y = next_y(24)
    add_label("Whisper Binary", y, LABEL_W)
    whisper_binary_field = NSTextField.alloc().initWithFrame_(NSMakeRect(CTRL_X, y, CTRL_W - 80, 22))
    whisper_binary_field.setStringValue_(str(cfg.whisper_binary))
    whisper_binary_field.setPlaceholderString_("/opt/homebrew/bin/whisper-cli")
    content.addSubview_(whisper_binary_field)
    choose_binary_btn = NSButton.alloc().initWithFrame_(NSMakeRect(CTRL_X + CTRL_W - 74, y, 74, 24))
    choose_binary_btn.setTitle_("Browse…")
    choose_binary_btn.setBezelStyle_(1)
    content.addSubview_(choose_binary_btn)

    y = next_y(24)
    add_label("Whisper Model", y, LABEL_W)
    whisper_model_field = NSTextField.alloc().initWithFrame_(NSMakeRect(CTRL_X, y, CTRL_W - 80, 22))
    whisper_model_field.setStringValue_(str(cfg.whisper_model))
    whisper_model_field.setPlaceholderString_("/path/to/ggml-large-v3.bin")
    content.addSubview_(whisper_model_field)
    choose_model_btn = NSButton.alloc().initWithFrame_(NSMakeRect(CTRL_X + CTRL_W - 74, y, 74, 24))
    choose_model_btn.setTitle_("Browse…")
    choose_model_btn.setBezelStyle_(1)
    content.addSubview_(choose_model_btn)

    # ---- AI Summary ----
    add_section_label("AI Summary (Ollama)")

    y = next_y(24)
    add_label("Ollama Model", y, LABEL_W)
    ollama_model_field = NSTextField.alloc().initWithFrame_(NSMakeRect(CTRL_X, y, CTRL_W, 22))
    ollama_model_field.setStringValue_(str(cfg.ollama_model))
    ollama_model_field.setPlaceholderString_("llama3.1:8b")
    content.addSubview_(ollama_model_field)

    y = next_y(24)
    add_label("Ollama Host", y, LABEL_W)
    ollama_host_field = NSTextField.alloc().initWithFrame_(NSMakeRect(CTRL_X, y, CTRL_W, 22))
    ollama_host_field.setStringValue_(str(cfg.ollama_host))
    ollama_host_field.setPlaceholderString_("http://localhost:11434")
    content.addSubview_(ollama_host_field)

    y = next_y(90)
    add_label("Custom Prompt", y + 68, LABEL_W)
    prompt_note = NSTextField.alloc().initWithFrame_(NSMakeRect(16, y + 46, LABEL_W - 4, 18))
    prompt_note.setStringValue_("Must contain {transcript}")
    prompt_note.setBezeled_(False)
    prompt_note.setDrawsBackground_(False)
    prompt_note.setEditable_(False)
    prompt_note.setSelectable_(False)
    prompt_note.setFont_(NSFont.systemFontOfSize_(10))
    prompt_note.setTextColor_(NSColor.tertiaryLabelColor())
    content.addSubview_(prompt_note)

    scroll_view = NSScrollView.alloc().initWithFrame_(NSMakeRect(CTRL_X, y, CTRL_W, 88))
    scroll_view.setHasVerticalScroller_(True)
    scroll_view.setHasHorizontalScroller_(False)
    scroll_view.setBorderType_(2)  # NSBezelBorder

    prompt_text_view = NSTextView.alloc().initWithFrame_(NSMakeRect(0, 0, CTRL_W - 16, 88))
    prompt_text_view.setString_(cfg.ollama_prompt or "")
    prompt_text_view.setFont_(NSFont.systemFontOfSize_(12))
    scroll_view.setDocumentView_(prompt_text_view)
    content.addSubview_(scroll_view)

    # ---- Processing ----
    add_section_label("Processing")

    y = next_y(24)
    add_label("Keep Audio Files", y, LABEL_W)
    keep_audio_checkbox = NSButton.alloc().initWithFrame_(NSMakeRect(CTRL_X, y, 24, 24))
    keep_audio_checkbox.setButtonType_(NSButtonTypeSwitch)
    keep_audio_checkbox.setTitle_("")
    keep_audio_checkbox.setState_(1 if cfg.keep_audio else 0)
    content.addSubview_(keep_audio_checkbox)

    y = next_y(24)
    add_label("Min Recording (seconds)", y, LABEL_W)
    min_rec_field = NSTextField.alloc().initWithFrame_(NSMakeRect(CTRL_X, y, 80, 22))
    min_rec_field.setStringValue_(str(cfg.min_recording_seconds))
    content.addSubview_(min_rec_field)

    y = next_y(24)
    add_label("Low Disk Alert (MB)", y, LABEL_W)
    low_disk_field = NSTextField.alloc().initWithFrame_(NSMakeRect(CTRL_X, y, 80, 22))
    low_disk_field.setStringValue_(str(cfg.low_disk_threshold_mb))
    content.addSubview_(low_disk_field)

    # ---- Restart notice ----
    y = next_y(18, gap=10)
    restart_note = NSTextField.alloc().initWithFrame_(NSMakeRect(16, y, WINDOW_W - 32, 16))
    restart_note.setStringValue_("Audio device and threshold changes take effect on the next recording.")
    restart_note.setBezeled_(False)
    restart_note.setDrawsBackground_(False)
    restart_note.setEditable_(False)
    restart_note.setSelectable_(False)
    restart_note.setFont_(NSFont.systemFontOfSize_(11))
    restart_note.setTextColor_(NSColor.secondaryLabelColor())
    content.addSubview_(restart_note)

    # ---- Cancel / Save ----
    y = next_y(28, gap=12)
    cancel_btn = NSButton.alloc().initWithFrame_(NSMakeRect(WINDOW_W - 200, y, 90, 28))
    cancel_btn.setTitle_("Cancel")
    cancel_btn.setBezelStyle_(1)
    content.addSubview_(cancel_btn)

    save_btn = NSButton.alloc().initWithFrame_(NSMakeRect(WINDOW_W - 104, y, 88, 28))
    save_btn.setTitle_("Save")
    save_btn.setBezelStyle_(1)
    save_btn.setKeyEquivalent_("\r")
    content.addSubview_(save_btn)

    # ---- Delegate / callbacks via ObjC subclass ----

    SettingsDelegate = _make_delegate_class()

    delegate = SettingsDelegate.alloc().init()
    delegate._window = window
    delegate._app_instance = app_instance
    delegate._output_dir_label = output_dir_label
    delegate._system_device_popup = system_device_popup
    delegate._mic_device_popup = mic_device_popup
    delegate._mic_threshold_field = mic_threshold_field
    delegate._whisper_binary_field = whisper_binary_field
    delegate._whisper_model_field = whisper_model_field
    delegate._ollama_model_field = ollama_model_field
    delegate._ollama_host_field = ollama_host_field
    delegate._prompt_text_view = prompt_text_view
    delegate._keep_audio_checkbox = keep_audio_checkbox
    delegate._min_rec_field = min_rec_field
    delegate._low_disk_field = low_disk_field

    choose_folder_btn.setTarget_(delegate)
    choose_folder_btn.setAction_("chooseFolderClicked:")
    choose_binary_btn.setTarget_(delegate)
    choose_binary_btn.setAction_("chooseBinaryClicked:")
    choose_model_btn.setTarget_(delegate)
    choose_model_btn.setAction_("chooseModelClicked:")
    cancel_btn.setTarget_(delegate)
    cancel_btn.setAction_("cancelClicked:")
    save_btn.setTarget_(delegate)
    save_btn.setAction_("saveClicked:")

    # Keep strong references at module level — NSWindow doesn't allow arbitrary
    # Python attribute assignment, and PyObjC's GC would otherwise collect the delegate.
    global _active_window, _active_delegate
    _active_window = window
    _active_delegate = delegate

    NSApp.activateIgnoringOtherApps_(True)
    window.makeKeyAndOrderFront_(None)

    if focus_output_dir:
        window.makeFirstResponder_(choose_folder_btn)


def _make_delegate_class():
    """Create (once) the ObjC delegate class for the Settings window."""
    import objc  # type: ignore
    from Foundation import NSObject  # type: ignore

    # Re-use if already registered (can happen if window is opened twice)
    import objc as _objc
    try:
        existing = _objc.lookUpClass("MRSettingsWindowDelegate")
        if existing is not None:
            return existing
    except Exception:
        pass

    class MRSettingsWindowDelegate(NSObject):  # type: ignore

        def chooseFolderClicked_(self, sender):
            from AppKit import NSOpenPanel  # type: ignore
            panel = NSOpenPanel.openPanel()
            panel.setCanChooseDirectories_(True)
            panel.setCanChooseFiles_(False)
            panel.setAllowsMultipleSelection_(False)
            panel.setTitle_("Choose Output Folder")
            panel.setPrompt_("Select")
            current = self._output_dir_label.stringValue()
            if current:
                from Foundation import NSURL  # type: ignore
                url = NSURL.fileURLWithPath_(current)
                panel.setDirectoryURL_(url)
            result = panel.runModal()
            if result == 1:  # NSModalResponseOK
                path = str(panel.URL().path())
                self._output_dir_label.setStringValue_(path)

        def chooseBinaryClicked_(self, sender):
            path = _pick_file("Choose Whisper Binary", None)
            if path:
                self._whisper_binary_field.setStringValue_(path)

        def chooseModelClicked_(self, sender):
            path = _pick_file("Choose Whisper Model", [["Whisper model files", ["bin", "gguf"]]])
            if path:
                self._whisper_model_field.setStringValue_(path)

        def cancelClicked_(self, sender):
            self._window.close()
            _clear_active_refs()

        def saveClicked_(self, sender):
            self._do_save()

        def _do_save(self):
            from config import USER_CONFIG_PATH, load_config  # type: ignore
            from AppKit import NSAlert  # type: ignore

            fields = {
                "output_dir": self._output_dir_label.stringValue(),
                "system_device": selected_device_value(
                    self._system_device_popup.titleOfSelectedItem() or ""
                ),
                "mic_device": selected_device_value(
                    self._mic_device_popup.titleOfSelectedItem() or ""
                ),
                "mic_threshold": self._mic_threshold_field.stringValue(),
                "whisper_binary": self._whisper_binary_field.stringValue(),
                "whisper_model": self._whisper_model_field.stringValue(),
                "ollama_model": self._ollama_model_field.stringValue(),
                "ollama_host": self._ollama_host_field.stringValue(),
                "ollama_prompt": self._prompt_text_view.string() or "",
                "keep_audio": self._keep_audio_checkbox.state() == 1,
                "min_recording_seconds": self._min_rec_field.stringValue(),
                "low_disk_threshold_mb": self._low_disk_field.stringValue(),
            }

            errors = validate_settings(fields)
            if errors:
                alert = NSAlert.alloc().init()
                alert.setMessageText_("Please fix the following before saving:")
                alert.setInformativeText_("\n".join(f"• {e}" for e in errors))
                alert.addButtonWithTitle_("OK")
                alert.runModal()
                return

            toml_text = build_toml_text(fields)

            try:
                atomic_save_config(USER_CONFIG_PATH, toml_text)
            except OSError as exc:
                alert = NSAlert.alloc().init()
                alert.setMessageText_("Could not save settings")
                alert.setInformativeText_(str(exc))
                alert.addButtonWithTitle_("OK")
                alert.runModal()
                return

            # Reload config and push to running app
            try:
                new_config = load_config(USER_CONFIG_PATH)
                # Config is a frozen dataclass; replace it on the app instance
                object.__setattr__(self._app_instance, "config", new_config)
                log.info("Settings saved and reloaded: %s", USER_CONFIG_PATH)
            except Exception as exc:
                alert = NSAlert.alloc().init()
                alert.setMessageText_("Settings saved but could not reload")
                alert.setInformativeText_(
                    f"{exc}\n\nThe file was written. Restart the app to apply all changes."
                )
                alert.addButtonWithTitle_("OK")
                alert.runModal()
                return

            _clear_active_refs()
            self._window.close()

    return MRSettingsWindowDelegate


def _pick_file(title: str, file_types: list | None) -> str | None:
    """Run an NSOpenPanel for files (not directories). Returns path string or None."""
    from AppKit import NSOpenPanel  # type: ignore
    panel = NSOpenPanel.openPanel()
    panel.setCanChooseDirectories_(False)
    panel.setCanChooseFiles_(True)
    panel.setAllowsMultipleSelection_(False)
    panel.setTitle_(title)
    panel.setPrompt_("Select")
    if file_types:
        # file_types: [["label", ["ext1", "ext2"]], ...]
        exts = []
        for _, exts_list in file_types:
            exts.extend(exts_list)
        try:
            from Foundation import NSArray  # type: ignore
            panel.setAllowedFileTypes_(exts)
        except Exception:
            pass
    result = panel.runModal()
    if result == 1:
        return str(panel.URL().path())
    return None
