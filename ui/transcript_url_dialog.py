# ui/transcript_url_dialog.py
"""
Native AppKit dialog asking for a Stream recording URL.

The scrape itself cannot run unattended — it needs a visible browser, a manual
Microsoft sign-in and a manually opened transcript panel — so this dialog only
collects the URL and hands off to Terminal, the same way the Ollama menu items
do. The user watches the run there and sees its diagnostics on failure.

All AppKit code runs on the main thread (enforced by callers via _call_on_main).
Pure helper functions at module level are fully unit-testable without an AppKit
event loop.
"""
from __future__ import annotations

import logging
import re
from typing import Callable

log = logging.getLogger(__name__)

# Module-level strong references so the window and delegate survive GC.
_active_window = None
_active_delegate = None

# Pattern for a single timestamp piece: seconds (e.g. 466), M:SS, or H:MM:SS
# Same rule as parse_timestamps in scripts/stream_transcript.py: 1-3 digit groups.
_TIMESTAMP_RE = re.compile(r'^\d+(?::\d+){0,2}$')

# "every 10" or "every 10s": a frame at that interval instead of explicit times.
_EVERY_RE = re.compile(r'^every\s+(\d+)\s*s?$', re.IGNORECASE)


def frames_cli_flag(spec: str | None) -> list[str]:
    """CLI arguments for a normalised frames spec: --frames-at or --frames-every.

    Returns [flag, value] or [] when there is nothing to capture. Callers quote
    each element.
    """
    if not spec:
        return []
    every = _EVERY_RE.match(spec)
    if every:
        return ["--frames-every", every.group(1)]
    return ["--frames-at", spec]


# ---------------------------------------------------------------------------
# Pure helpers — no AppKit dependency, fully unit-testable
# ---------------------------------------------------------------------------

def normalise_url(raw: str | None) -> str | None:
    """Strip a pasted URL; return None unless it is an http(s) URL.

    Browsers and chat clients wrap pasted links in angle brackets or quotes
    often enough to be worth handling here rather than failing in the browser.
    """
    if raw is None:
        return None
    stripped = raw.strip().strip("<>").strip("\"'").strip()
    if not stripped.lower().startswith(("http://", "https://")):
        return None
    return stripped


def looks_like_stream_url(url: str) -> bool:
    """True if *url* looks like a Stream/SharePoint recording page.

    Advisory only — the dialog warns but still lets the user proceed, since
    tenant URL shapes vary and a false negative should not block anyone.
    """
    lowered = url.lower()
    return "stream.aspx" in lowered or "sharepoint.com" in lowered


def normalise_frames_at(raw: str | None) -> str | None:
    """Normalise a frames-at string from the dialog field.

    Accepts a comma-separated list of timestamps, each of which may be:
      - plain seconds:   ``466``
      - M:SS:            ``7:46``
      - H:MM:SS:         ``1:07:46``

    Returns None when the input is empty or blank (treated as not provided).
    Raises ValueError with a descriptive message when any piece fails to
    match the expected format.

    The return value has spaces stripped and empty pieces dropped so it is
    safe to pass directly to ``--frames-at``.
    """
    if not raw:
        return None
    every = _EVERY_RE.match(raw.strip())
    if every:
        seconds = int(every.group(1))
        if seconds < 1:
            raise ValueError("Frame interval must be at least 1 second (e.g. every 10).")
        return f"every {seconds}"
    pieces = [p.strip() for p in raw.split(",")]
    pieces = [p for p in pieces if p]  # drop empty segments
    if not pieces:
        return None
    bad =[p for p in pieces if not _TIMESTAMP_RE.match(p)]
    if bad:
        raise ValueError(
            f"Invalid timestamp(s): {', '.join(bad)!r}. "
            "Use seconds (e.g. 466), M:SS (e.g. 7:46), or H:MM:SS (e.g. 1:07:46)."
        )
    return ",".join(pieces)


# ---------------------------------------------------------------------------
# AppKit window — only imported/instantiated on macOS inside the real app
# ---------------------------------------------------------------------------

def _clear_active_refs() -> None:
    global _active_window, _active_delegate
    _active_window = None
    _active_delegate = None


def open_transcript_url_dialog(
    on_submit: Callable[[str, str | None, str | None, str | None], None],
) -> None:
    """Show the URL dialog. Calls *on_submit(url, name, frames_at, context)*.

    *url* is a normalised http(s) URL.  *name*, *frames_at*, and *context* are
    optional strings (None when blank / not provided by the user).

    Cancelling, or submitting something that is not an http(s) URL, calls
    nothing — the dialog simply closes.
    """
    global _active_window, _active_delegate

    from AppKit import (
        NSApplication,
        NSBackingStoreBuffered,
        NSButton,
        NSColor,
        NSFont,
        NSMakeRect,
        NSScrollView,
        NSTextView,
        NSTextField,
        NSView,
        NSWindow,
        NSWindowStyleMaskClosable,
        NSWindowStyleMaskTitled,
    )
    import objc
    from Foundation import NSObject
    from ui.stop_dialog import normalise_text_input  # reuse strip-and-None helper

    PAD = 20
    WIDTH = 460
    INNER_W = WIDTH - (PAD * 2)

    class _URLDialogDelegate(NSObject):
        def initWithCallback_(self, callback):
            self = objc.super(_URLDialogDelegate, self).init()
            if self is None:
                return None
            self._on_submit = callback
            self._url_field = None
            self._name_field = None
            self._frames_field = None
            self._context_text_view = None
            self._error_lbl = None
            self._window = None
            return self

        def setWidgets_(self, widgets):
            """Set all widget references in one call."""
            self._url_field = widgets["url_field"]
            self._name_field = widgets["name_field"]
            self._frames_field = widgets["frames_field"]
            self._context_text_view = widgets["context_text_view"]
            self._error_lbl = widgets["error_lbl"]
            self._window = widgets["window"]

        def scrapeClicked_(self, sender):
            raw_url = self._url_field.stringValue() if self._url_field else ""
            url = normalise_url(raw_url)
            if not url:
                if raw_url.strip():
                    if self._error_lbl:
                        self._error_lbl.setStringValue_("Please enter an https:// URL.")
                    log.warning("Ignoring input that is not an http(s) URL")
                return

            # Validate frames-at before closing
            raw_frames = self._frames_field.stringValue() if self._frames_field else ""
            try:
                frames_at = normalise_frames_at(raw_frames)
            except ValueError as exc:
                if self._error_lbl:
                    self._error_lbl.setStringValue_(str(exc))
                return

            name = normalise_text_input(
                self._name_field.stringValue() if self._name_field else ""
            )
            context = normalise_text_input(
                self._context_text_view.string() if self._context_text_view else ""
            )

            if self._window:
                self._window.close()
            _clear_active_refs()
            if self._on_submit:
                self._on_submit(url, name, frames_at, context)

        def cancelClicked_(self, sender):
            if self._window:
                self._window.close()
            _clear_active_refs()

    # ---- Layout (top-down, y decrements as we add rows) ----
    #
    # URL field:        24 px
    # Title field:      22 px
    # Frames-at field:  22 px
    # Context label:    16 px
    # Context scroll:   60 px
    # Error label:      18 px
    # Explainer text:   54 px
    # Buttons row:      30 px
    # + PAD top + PAD bottom + gaps
    height = 390
    window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        NSMakeRect(0, 0, WIDTH, height),
        NSWindowStyleMaskTitled | NSWindowStyleMaskClosable,
        NSBackingStoreBuffered,
        False,
    )
    window.setTitle_("Import Transcript from Stream")
    window.center()

    content = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, WIDTH, height))

    GAP = 8
    y = height - PAD - 20

    def _static_label(text, ypos, h=16, font_size=11, bold=False, color=None):
        lbl = NSTextField.alloc().initWithFrame_(NSMakeRect(PAD, ypos, INNER_W, h))
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

    # ---- Recording URL ----
    heading = NSTextField.alloc().initWithFrame_(NSMakeRect(PAD, y, INNER_W, 20))
    heading.setStringValue_("Recording URL")
    heading.setBezeled_(False)
    heading.setDrawsBackground_(False)
    heading.setEditable_(False)
    heading.setFont_(NSFont.boldSystemFontOfSize_(13))
    content.addSubview_(heading)

    y -= GAP + 24
    url_field = NSTextField.alloc().initWithFrame_(NSMakeRect(PAD, y, INNER_W, 24))
    url_field.setPlaceholderString_("Paste the URL from the browser address bar")
    url_field.setFont_(NSFont.systemFontOfSize_(12))
    content.addSubview_(url_field)

    # ---- Title (optional) ----
    y -= GAP + 16
    _static_label("Title (optional)", y,
                  color=NSColor.secondaryLabelColor())

    y -= GAP + 22
    name_field = NSTextField.alloc().initWithFrame_(NSMakeRect(PAD, y, INNER_W, 22))
    name_field.setPlaceholderString_("Leave blank — the AI will name it from the transcript")
    name_field.setFont_(NSFont.systemFontOfSize_(12))
    content.addSubview_(name_field)

    # ---- Frames at (optional) ----
    y -= GAP + 16
    _static_label("Frames at (optional)", y,
                  color=NSColor.secondaryLabelColor())

    y -= GAP + 22
    frames_field = NSTextField.alloc().initWithFrame_(NSMakeRect(PAD, y, INNER_W, 22))
    frames_field.setPlaceholderString_("7:46, 19:40  or  every 10 — optional")
    frames_field.setFont_(NSFont.systemFontOfSize_(12))
    content.addSubview_(frames_field)

    # ---- Context (optional multi-line) ----
    CONTEXT_H = 60
    y -= GAP + 16
    _static_label("Context for AI summary (optional)", y,
                  color=NSColor.secondaryLabelColor())

    y -= GAP + CONTEXT_H
    scroll_view = NSScrollView.alloc().initWithFrame_(NSMakeRect(PAD, y, INNER_W, CONTEXT_H))
    scroll_view.setHasVerticalScroller_(True)
    scroll_view.setHasHorizontalScroller_(False)
    scroll_view.setBorderType_(2)  # NSBezelBorder

    context_text_view = NSTextView.alloc().initWithFrame_(
        NSMakeRect(0, 0, INNER_W - 16, CONTEXT_H)
    )
    context_text_view.setFont_(NSFont.systemFontOfSize_(12))
    context_text_view.setString_("")
    scroll_view.setDocumentView_(context_text_view)
    content.addSubview_(scroll_view)

    # Placeholder overlay for the NSTextView
    placeholder_lbl = NSTextField.alloc().initWithFrame_(
        NSMakeRect(PAD + 5, y + CONTEXT_H - 18, INNER_W - 10, 18)
    )
    placeholder_lbl.setStringValue_("Attendees, project names, terms — optional")
    placeholder_lbl.setBezeled_(False)
    placeholder_lbl.setDrawsBackground_(False)
    placeholder_lbl.setEditable_(False)
    placeholder_lbl.setSelectable_(False)
    placeholder_lbl.setFont_(NSFont.systemFontOfSize_(12))
    placeholder_lbl.setTextColor_(NSColor.placeholderTextColor())
    content.addSubview_(placeholder_lbl)

    # ---- Error label (hidden until validation fails) ----
    y -= GAP + 18
    error_lbl = NSTextField.alloc().initWithFrame_(NSMakeRect(PAD, y, INNER_W, 16))
    error_lbl.setStringValue_("")
    error_lbl.setBezeled_(False)
    error_lbl.setDrawsBackground_(False)
    error_lbl.setEditable_(False)
    error_lbl.setFont_(NSFont.systemFontOfSize_(11))
    error_lbl.setTextColor_(NSColor.systemRedColor())
    content.addSubview_(error_lbl)

    # ---- Explainer ----
    y -= GAP + 54
    explainer = NSTextField.alloc().initWithFrame_(NSMakeRect(PAD, y, INNER_W, 50))
    explainer.setStringValue_(
        "Opens Terminal and a Chrome window. Sign in if prompted, then open the "
        "recording's transcript panel — the scrape needs it visible and cannot do "
        "these steps for you. It then collects the transcript and writes a note."
    )
    explainer.setBezeled_(False)
    explainer.setDrawsBackground_(False)
    explainer.setEditable_(False)
    explainer.setFont_(NSFont.systemFontOfSize_(11))
    explainer.setTextColor_(NSColor.secondaryLabelColor())
    content.addSubview_(explainer)

    delegate = _URLDialogDelegate.alloc().initWithCallback_(on_submit)
    delegate.setWidgets_({
        "url_field": url_field,
        "name_field": name_field,
        "frames_field": frames_field,
        "context_text_view": context_text_view,
        "error_lbl": error_lbl,
        "window": window,
    })

    y = PAD
    scrape_button = NSButton.alloc().initWithFrame_(NSMakeRect(WIDTH - PAD - 140, y, 140, 30))
    scrape_button.setTitle_("Scrape Transcript")
    scrape_button.setBezelStyle_(1)
    scrape_button.setKeyEquivalent_("\r")
    scrape_button.setTarget_(delegate)
    scrape_button.setAction_("scrapeClicked:")
    content.addSubview_(scrape_button)

    cancel_button = NSButton.alloc().initWithFrame_(NSMakeRect(WIDTH - PAD - 240, y, 90, 30))
    cancel_button.setTitle_("Cancel")
    cancel_button.setBezelStyle_(1)
    cancel_button.setKeyEquivalent_("\x1b")
    cancel_button.setTarget_(delegate)
    cancel_button.setAction_("cancelClicked:")
    content.addSubview_(cancel_button)

    window.setContentView_(content)
    window.makeFirstResponder_(url_field)

    _active_window = window
    _active_delegate = delegate

    NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
    window.makeKeyAndOrderFront_(None)
