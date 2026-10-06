# ui/import_recording_dialog.py
"""
Native AppKit dialog for the Import Recording feature.

Presents a small window showing the detected file date/time and lets the user
confirm it or type a corrected one before the import pipeline is dispatched.
The meeting name field is optional; when blank, Ollama will suggest one from
the transcript (same behaviour as the Stop dialog).

An optional Context field (multi-line) mirrors the Stop dialog — attendees,
project names, terms the AI should recognise.

All AppKit code runs on the main thread (enforced by callers via _call_on_main).
Pure helper functions at module level are fully unit-testable without an AppKit
event loop.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Callable

log = logging.getLogger(__name__)

# Module-level strong references so the window and delegate survive GC.
_active_window = None
_active_delegate = None

# Expected input format from the date/time field.
_DT_FORMAT = "%Y-%m-%d %H:%M"


# ---------------------------------------------------------------------------
# Pure helpers — no AppKit dependency, fully unit-testable
# ---------------------------------------------------------------------------

def format_dt_for_field(dt: datetime) -> str:
    """Format a datetime as the string shown in the date/time field."""
    return dt.strftime(_DT_FORMAT)


def parse_dt_from_field(raw: str | None) -> datetime | None:
    """Parse a date/time string entered by the user.

    Accepts ``YYYY-MM-DD HH:MM`` (and tolerates surrounding whitespace).
    Returns None if the value is blank or does not match the expected format.
    """
    if not raw:
        return None
    stripped = raw.strip()
    if not stripped:
        return None
    try:
        return datetime.strptime(stripped, _DT_FORMAT)
    except ValueError:
        return None


def validate_dt_field(raw: str | None) -> tuple[datetime | None, str | None]:
    """Validate the raw date/time string from the field.

    Returns ``(datetime, None)`` on success or ``(None, error_message)`` on
    failure.  An empty/blank value is a parse failure.
    """
    dt = parse_dt_from_field(raw)
    if dt is None:
        return None, f"Date must be in the format YYYY-MM-DD HH:MM (e.g. {format_dt_for_field(datetime.now())})"
    return dt, None


def parse_frames_every(raw: str | None) -> int | None:
    """Parse the "capture frames every N seconds" field.

    Blank means no frame capture and returns None. Otherwise a whole number of
    seconds >= 1. Raises ValueError with a message fit to show the user.
    """
    if raw is None or not raw.strip():
        return None
    text = raw.strip()
    if not text.isdigit() or int(text) < 1:
        raise ValueError("Frame interval must be a whole number of seconds (e.g. 10).")
    return int(text)


# ---------------------------------------------------------------------------
# AppKit window — only imported/instantiated on macOS inside the real app
# ---------------------------------------------------------------------------

def _clear_active_refs() -> None:
    global _active_window, _active_delegate
    _active_window = None
    _active_delegate = None


def open_import_recording_dialog(
    default_dt: datetime,
    filename: str,
    on_import: Callable[[datetime, str | None, str | None, int | None], None],
    on_cancel: Callable[[], None],
) -> None:
    """Show the import confirmation dialog on the current (main) thread.

    *default_dt* is the file's mtime, pre-filled into the date/time field.
    *filename* is shown in the window subtitle so the user knows which file.

    *on_import* is called with ``(confirmed_dt, meeting_name_or_None,
    llm_context_or_None, frames_every_or_None)`` when the user clicks Import.  *on_cancel* is called
    if they cancel/close.
    """
    try:
        _open_import_recording_dialog_impl(default_dt, filename, on_import, on_cancel)
    except Exception:
        log.exception("Failed to open import recording dialog — cancelling")
        on_cancel()


def _open_import_recording_dialog_impl(
    default_dt: datetime,
    filename: str,
    on_import: Callable[[datetime, str | None, str | None, int | None], None],
    on_cancel: Callable[[], None],
) -> None:
    from AppKit import (  # type: ignore
        NSApp,
        NSWindow,
        NSBackingStoreBuffered,
        NSWindowStyleMaskTitled,
        NSWindowStyleMaskClosable,
        NSScrollView,
        NSTextView,
        NSTextField,
        NSButton,
        NSFont,
        NSColor,
        NSMakeRect,
    )
    from Foundation import NSObject  # type: ignore
    import objc  # type: ignore
    from ui.stop_dialog import normalise_text_input  # reuse strip-and-None helper

    WINDOW_W = 460
    PAD = 24
    GAP = 8
    INNER_W = WINDOW_W - PAD * 2
    CONTEXT_H = 72

    # Window height: title bar + filename + dt label + dt field + error +
    # title label + title field + context label + context scroll + buttons + padding
    WINDOW_H = 444
    style = NSWindowStyleMaskTitled | NSWindowStyleMaskClosable
    window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        NSMakeRect(0, 0, WINDOW_W, WINDOW_H),
        style,
        NSBackingStoreBuffered,
        False,
    )
    window.setTitle_("Import Recording")
    window.center()
    window.setReleasedWhenClosed_(False)

    content = window.contentView()

    y = WINDOW_H - PAD

    def _label(text: str, x: float, ypos: float, w: float, h: float,
                font_size: float = 13, bold: bool = False, color=None) -> NSTextField:
        lbl = NSTextField.alloc().initWithFrame_(NSMakeRect(x, ypos, w, h))
        lbl.setStringValue_(text)
        lbl.setBezeled_(False)
        lbl.setDrawsBackground_(False)
        lbl.setEditable_(False)
        lbl.setSelectable_(False)
        if bold:
            lbl.setFont_(NSFont.boldSystemFontOfSize_(font_size))
        else:
            lbl.setFont_(NSFont.systemFontOfSize_(font_size))
        if color is not None:
            lbl.setTextColor_(color)
        content.addSubview_(lbl)
        return lbl

    # ---- File subtitle ----
    y -= 24
    _label(
        filename,
        PAD, y, INNER_W, 20,
        font_size=12,
        color=NSColor.secondaryLabelColor(),
    )

    # ---- Date/time field ----
    y -= GAP + 16
    _label("Recording date and time", PAD, y, INNER_W, 16,
           font_size=11, color=NSColor.secondaryLabelColor())

    y -= GAP + 22
    dt_field = NSTextField.alloc().initWithFrame_(NSMakeRect(PAD, y, 200, 22))
    dt_field.setStringValue_(format_dt_for_field(default_dt))
    dt_field.setFont_(NSFont.systemFontOfSize_(13))
    dt_field.setPlaceholderString_("YYYY-MM-DD HH:MM")
    content.addSubview_(dt_field)

    # ---- Error label (hidden until validation fails) ----
    y -= GAP + 18
    error_lbl = _label("", PAD, y, INNER_W, 16,
                        font_size=11, color=NSColor.systemRedColor())

    # ---- Meeting title field ----
    y -= GAP + 16
    _label("Suggested title (optional)", PAD, y, INNER_W, 16,
           font_size=11, color=NSColor.secondaryLabelColor())

    y -= GAP + 22
    title_field = NSTextField.alloc().initWithFrame_(NSMakeRect(PAD, y, INNER_W, 22))
    title_field.setPlaceholderString_("Leave blank — the AI will name it from the transcript")
    title_field.setFont_(NSFont.systemFontOfSize_(13))
    content.addSubview_(title_field)

    # ---- Frame capture interval (video files only; blank = off) ----
    y -= GAP + 16
    _label("Capture a frame every N seconds (optional, video files)", PAD, y, INNER_W, 16,
           font_size=11, color=NSColor.secondaryLabelColor())

    y -= GAP + 22
    frames_field = NSTextField.alloc().initWithFrame_(NSMakeRect(PAD, y, 100, 22))
    frames_field.setPlaceholderString_("e.g. 10")
    frames_field.setFont_(NSFont.systemFontOfSize_(13))
    content.addSubview_(frames_field)

    # ---- Context field (multi-line, mirrors stop_dialog) ----
    y -= GAP * 2 + 16
    _label("Context for AI summary (optional)", PAD, y, INNER_W, 16,
           font_size=11, color=NSColor.secondaryLabelColor())

    y -= GAP + CONTEXT_H
    scroll_view = NSScrollView.alloc().initWithFrame_(NSMakeRect(PAD, y, INNER_W, CONTEXT_H))
    scroll_view.setHasVerticalScroller_(True)
    scroll_view.setHasHorizontalScroller_(False)
    scroll_view.setBorderType_(2)  # NSBezelBorder
    scroll_view.setAutoresizingMask_(0)

    context_text_view = NSTextView.alloc().initWithFrame_(
        NSMakeRect(0, 0, INNER_W - 16, CONTEXT_H)
    )
    context_text_view.setFont_(NSFont.systemFontOfSize_(13))
    context_text_view.setTextColor_(NSColor.labelColor())
    context_text_view.setString_("")
    scroll_view.setDocumentView_(context_text_view)
    content.addSubview_(scroll_view)

    # Placeholder overlay — hidden once user starts typing
    placeholder_lbl = NSTextField.alloc().initWithFrame_(
        NSMakeRect(PAD + 5, y + CONTEXT_H - 18, INNER_W - 10, 18)
    )
    placeholder_lbl.setStringValue_("e.g. Attendees, project names, technical terms")
    placeholder_lbl.setBezeled_(False)
    placeholder_lbl.setDrawsBackground_(False)
    placeholder_lbl.setEditable_(False)
    placeholder_lbl.setSelectable_(False)
    placeholder_lbl.setFont_(NSFont.systemFontOfSize_(13))
    placeholder_lbl.setTextColor_(NSColor.placeholderTextColor())
    content.addSubview_(placeholder_lbl)

    # ---- Buttons ----
    BTN_H = 28
    BTN_W = 90
    y -= GAP * 2 + BTN_H
    cancel_btn = NSButton.alloc().initWithFrame_(
        NSMakeRect(WINDOW_W - PAD - BTN_W * 2 - 8, y, BTN_W, BTN_H)
    )
    cancel_btn.setTitle_("Cancel")
    cancel_btn.setBezelStyle_(1)
    cancel_btn.setKeyEquivalent_("\x1b")
    content.addSubview_(cancel_btn)

    import_btn = NSButton.alloc().initWithFrame_(
        NSMakeRect(WINDOW_W - PAD - BTN_W, y, BTN_W, BTN_H)
    )
    import_btn.setTitle_("Import")
    import_btn.setBezelStyle_(1)
    import_btn.setKeyEquivalent_("\r")
    content.addSubview_(import_btn)

    # ---- Delegate ----
    ImportDelegate = _make_delegate_class()

    delegate = ImportDelegate.alloc().init()
    delegate._window = window
    delegate._dt_field = dt_field
    delegate._title_field = title_field
    delegate._frames_field = frames_field
    delegate._context_text_view = context_text_view
    delegate._placeholder_lbl = placeholder_lbl
    delegate._error_lbl = error_lbl
    delegate._on_import = on_import
    delegate._on_cancel = on_cancel

    cancel_btn.setTarget_(delegate)
    cancel_btn.setAction_("cancelClicked:")
    import_btn.setTarget_(delegate)
    import_btn.setAction_("importClicked:")
    window.setDelegate_(delegate)
    context_text_view.setDelegate_(delegate)

    global _active_window, _active_delegate
    _active_window = window
    _active_delegate = delegate

    NSApp.activateIgnoringOtherApps_(True)
    window.makeKeyAndOrderFront_(None)
    window.makeFirstResponder_(dt_field)


def _make_delegate_class():
    """Create (once) the ObjC delegate class for the Import Recording dialog."""
    import objc  # type: ignore
    from Foundation import NSObject  # type: ignore
    from ui.stop_dialog import normalise_text_input  # reuse strip-and-None helper

    try:
        existing = objc.lookUpClass("MRImportRecordingDelegate")
        if existing is not None:
            return existing
    except Exception:
        pass

    class MRImportRecordingDelegate(NSObject):  # type: ignore

        def importClicked_(self, sender):
            raw_dt = self._dt_field.stringValue() if self._dt_field else ""
            confirmed_dt, err = validate_dt_field(raw_dt)
            if err:
                self._error_lbl.setStringValue_(err)
                return

            raw_title = self._title_field.stringValue() if self._title_field else ""
            meeting_name = raw_title.strip() or None

            llm_context = normalise_text_input(
                self._context_text_view.string() if self._context_text_view else ""
            )

            try:
                frames_every = parse_frames_every(
                    self._frames_field.stringValue() if self._frames_field else ""
                )
            except ValueError as exc:
                self._error_lbl.setStringValue_(str(exc))
                return

            self._error_lbl.setStringValue_("")
            self._dispatch(confirmed_dt=confirmed_dt, meeting_name=meeting_name,
                           llm_context=llm_context, frames_every=frames_every,
                           cancelled=False)

        def cancelClicked_(self, sender):
            self._dispatch(confirmed_dt=None, meeting_name=None, llm_context=None, cancelled=True)

        def windowWillClose_(self, notification):
            self._dispatch(confirmed_dt=None, meeting_name=None, llm_context=None, cancelled=True)

        # NSTextViewDelegate — hide placeholder when text is entered
        def textDidChange_(self, notification):
            text = self._context_text_view.string() or ""
            self._placeholder_lbl.setHidden_(bool(text.strip()))

        def _dispatch(self, confirmed_dt, meeting_name, llm_context, cancelled: bool,
                      frames_every=None):
            # Guard against double-fire (window close + button click)
            if self._on_import is None and self._on_cancel is None:
                return
            on_import = self._on_import
            on_cancel = self._on_cancel
            self._on_import = None
            self._on_cancel = None

            self._window.close()
            _clear_active_refs()

            if cancelled:
                if on_cancel:
                    on_cancel()
            else:
                if on_import and confirmed_dt is not None:
                    on_import(confirmed_dt, meeting_name, llm_context, frames_every)

    return MRImportRecordingDelegate
