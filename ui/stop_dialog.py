# ui/stop_dialog.py
"""
Native AppKit stop dialog for Meeting Recorder.

Presents a single window that collects both the meeting title (optional,
used as folder and note title) and optional LLM context, replacing the old
two-step rumps.Window sequence.

All AppKit code runs on the main thread (enforced by callers via _call_on_main).
Pure helper functions at module level are fully unit-testable without an AppKit
event loop.
"""
from __future__ import annotations

import logging
from typing import Callable, Optional

log = logging.getLogger(__name__)

# Module-level strong references so the window and delegate survive GC.
_active_window = None
_active_delegate = None


# ---------------------------------------------------------------------------
# Pure helpers — no AppKit dependency, fully unit-testable
# ---------------------------------------------------------------------------

def normalise_text_input(raw: str | None) -> str | None:
    """Normalise a text field value: strip whitespace; return None if blank.

    This is the canonical way to convert raw field content to the optional
    string values the pipeline expects (None → LLM decides / no context).
    """
    if raw is None:
        return None
    stripped = raw.strip()
    return stripped if stripped else None


# ---------------------------------------------------------------------------
# AppKit window — only imported/instantiated on macOS inside the real app
# ---------------------------------------------------------------------------

def _clear_active_refs() -> None:
    global _active_window, _active_delegate
    _active_window = None
    _active_delegate = None


def open_stop_dialog(
    on_process: Callable[[str | None, str | None], None],
    on_skip: Callable[[], None],
) -> None:
    """Create and show the stop dialog on the current (main) thread.

    *on_process* is called with ``(meeting_name, llm_context)`` when the user
    clicks Process/Save.  Both arguments are ``str | None``.

    *on_skip* is called with no arguments when the user clicks Skip/closes the
    window without processing.
    """
    try:
        _open_stop_dialog_impl(on_process, on_skip)
    except Exception:
        log.exception("Failed to open stop dialog — falling back to skip")
        on_skip()


def _open_stop_dialog_impl(
    on_process: Callable[[str | None, str | None], None],
    on_skip: Callable[[], None],
) -> None:
    from AppKit import (  # type: ignore
        NSApp,
        NSWindow,
        NSBackingStoreBuffered,
        NSWindowStyleMaskTitled,
        NSWindowStyleMaskClosable,
        NSTextField,
        NSButton,
        NSScrollView,
        NSTextView,
        NSFont,
        NSColor,
        NSMakeRect,
    )
    from Foundation import NSObject  # type: ignore
    import objc  # type: ignore

    WINDOW_W = 480
    WINDOW_H = 340
    PAD = 24          # outer horizontal padding
    GAP = 10          # vertical gap between sections
    INNER_W = WINDOW_W - PAD * 2

    style = NSWindowStyleMaskTitled | NSWindowStyleMaskClosable
    window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        NSMakeRect(0, 0, WINDOW_W, WINDOW_H),
        style,
        NSBackingStoreBuffered,
        False,
    )
    window.setTitle_("Meeting Saved")
    window.center()
    window.setReleasedWhenClosed_(False)

    content = window.contentView()

    y = WINDOW_H - PAD  # current top cursor (top-down layout)

    def _label(text: str, x: float, ypos: float, w: float, h: float,
                font_size: float = 13, bold: bool = False,
                color=None) -> NSTextField:
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

    # ---- Subtitle / instruction ----
    y -= 28
    _label(
        "Optionally name and annotate this recording.",
        PAD, y, INNER_W, 20,
        font_size=13,
        color=NSColor.secondaryLabelColor(),
    )

    # ---- Meeting title field ----
    y -= GAP + 18
    _label("Suggested title", PAD, y, INNER_W, 16, font_size=11, bold=False,
           color=NSColor.secondaryLabelColor())

    y -= GAP + 22
    title_field = NSTextField.alloc().initWithFrame_(NSMakeRect(PAD, y, INNER_W, 22))
    title_field.setPlaceholderString_("Leave blank — the AI will name it from the transcript")
    title_field.setFont_(NSFont.systemFontOfSize_(13))
    content.addSubview_(title_field)

    # ---- Context field ----
    y -= GAP * 2 + 16
    _label("Context for AI summary (optional)", PAD, y, INNER_W, 16,
           font_size=11, bold=False, color=NSColor.secondaryLabelColor())

    CONTEXT_H = 80
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
    # Placeholder-style hint via string (cleared on first keypress via delegate)
    context_text_view.setString_("")
    scroll_view.setDocumentView_(context_text_view)
    content.addSubview_(scroll_view)

    # Placeholder for the NSTextView (shown as greyed-out text when empty)
    placeholder_lbl = NSTextField.alloc().initWithFrame_(
        NSMakeRect(PAD + 5, y + CONTEXT_H - 18, INNER_W - 10, 18)
    )
    placeholder_lbl.setStringValue_("e.g. Q2 planning with design team, focused on redesign timeline")
    placeholder_lbl.setBezeled_(False)
    placeholder_lbl.setDrawsBackground_(False)
    placeholder_lbl.setEditable_(False)
    placeholder_lbl.setSelectable_(False)
    placeholder_lbl.setFont_(NSFont.systemFontOfSize_(13))
    placeholder_lbl.setTextColor_(NSColor.placeholderTextColor())
    content.addSubview_(placeholder_lbl)

    # ---- Buttons ----
    BTN_H = 28
    y -= GAP * 2 + BTN_H
    BTN_W = 100

    skip_btn = NSButton.alloc().initWithFrame_(
        NSMakeRect(WINDOW_W - PAD - BTN_W * 2 - 8, y, BTN_W, BTN_H)
    )
    skip_btn.setTitle_("Skip")
    skip_btn.setBezelStyle_(1)  # NSBezelStyleRounded
    content.addSubview_(skip_btn)

    process_btn = NSButton.alloc().initWithFrame_(
        NSMakeRect(WINDOW_W - PAD - BTN_W, y, BTN_W, BTN_H)
    )
    process_btn.setTitle_("Process")
    process_btn.setBezelStyle_(1)
    process_btn.setKeyEquivalent_("\r")  # Return key → primary action
    content.addSubview_(process_btn)

    # ---- Delegate ----
    StopDelegate = _make_delegate_class()

    delegate = StopDelegate.alloc().init()
    delegate._window = window
    delegate._title_field = title_field
    delegate._context_text_view = context_text_view
    delegate._placeholder_lbl = placeholder_lbl
    delegate._on_process = on_process
    delegate._on_skip = on_skip

    skip_btn.setTarget_(delegate)
    skip_btn.setAction_("skipClicked:")
    process_btn.setTarget_(delegate)
    process_btn.setAction_("processClicked:")
    window.setDelegate_(delegate)

    global _active_window, _active_delegate
    _active_window = window
    _active_delegate = delegate

    NSApp.activateIgnoringOtherApps_(True)
    window.makeKeyAndOrderFront_(None)
    window.makeFirstResponder_(title_field)


def _make_delegate_class():
    """Create (once) the ObjC delegate class for the Stop dialog."""
    import objc  # type: ignore
    from Foundation import NSObject  # type: ignore

    try:
        existing = objc.lookUpClass("MRStopDialogDelegate")
        if existing is not None:
            return existing
    except Exception:
        pass

    class MRStopDialogDelegate(NSObject):  # type: ignore

        def processClicked_(self, sender):
            self._submit(skipped=False)

        def skipClicked_(self, sender):
            self._submit(skipped=True)

        def windowWillClose_(self, notification):
            # Window closed via the red traffic-light button → treat as skip
            self._submit(skipped=True)

        def _submit(self, skipped: bool):
            # Guard against double-fire (window close + button)
            if self._on_process is None and self._on_skip is None:
                return
            on_process = self._on_process
            on_skip = self._on_skip
            self._on_process = None
            self._on_skip = None

            self._window.close()
            _clear_active_refs()

            if skipped:
                if on_skip:
                    on_skip()
                return

            meeting_name = normalise_text_input(
                self._title_field.stringValue()
            )
            llm_context = normalise_text_input(
                self._context_text_view.string()
            )
            if on_process:
                on_process(meeting_name, llm_context)

        # NSTextViewDelegate — hide placeholder when text is entered
        def textDidChange_(self, notification):
            text = self._context_text_view.string() or ""
            # Show placeholder only when the text view is truly empty
            self._placeholder_lbl.setHidden_(bool(text.strip()))

    return MRStopDialogDelegate
