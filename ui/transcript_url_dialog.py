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
from typing import Callable

log = logging.getLogger(__name__)

# Module-level strong references so the window and delegate survive GC.
_active_window = None
_active_delegate = None


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


# ---------------------------------------------------------------------------
# AppKit window — only imported/instantiated on macOS inside the real app
# ---------------------------------------------------------------------------

def _clear_active_refs() -> None:
    global _active_window, _active_delegate
    _active_window = None
    _active_delegate = None


def open_transcript_url_dialog(on_submit: Callable[[str], None]) -> None:
    """Show the URL dialog. Calls *on_submit(url)* with a normalised URL.

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
        NSTextField,
        NSTitledWindowMask,
        NSView,
        NSWindow,
    )
    import objc
    from Foundation import NSObject

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
            self._window = None
            return self

        def setField_window_(self, field, window):
            self._url_field = field
            self._window = window

        def scrapeClicked_(self, sender):
            raw = self._url_field.stringValue() if self._url_field else ""
            url = normalise_url(raw)
            if self._window:
                self._window.close()
            _clear_active_refs()
            if url and self._on_submit:
                self._on_submit(url)
            elif raw.strip():
                log.warning("Ignoring input that is not an http(s) URL")

        def cancelClicked_(self, sender):
            if self._window:
                self._window.close()
            _clear_active_refs()

    height = 210
    window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        NSMakeRect(0, 0, WIDTH, height),
        NSTitledWindowMask,
        NSBackingStoreBuffered,
        False,
    )
    window.setTitle_("Import Transcript from Stream")
    window.center()

    content = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, WIDTH, height))

    y = height - PAD - 20
    heading = NSTextField.alloc().initWithFrame_(NSMakeRect(PAD, y, INNER_W, 20))
    heading.setStringValue_("Recording URL")
    heading.setBezeled_(False)
    heading.setDrawsBackground_(False)
    heading.setEditable_(False)
    heading.setFont_(NSFont.boldSystemFontOfSize_(13))
    content.addSubview_(heading)

    y -= 28
    url_field = NSTextField.alloc().initWithFrame_(NSMakeRect(PAD, y, INNER_W, 24))
    url_field.setPlaceholderString_("Paste the URL from the browser address bar")
    url_field.setFont_(NSFont.systemFontOfSize_(12))
    content.addSubview_(url_field)

    y -= 74
    explainer = NSTextField.alloc().initWithFrame_(NSMakeRect(PAD, y, INNER_W, 66))
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
    delegate.setField_window_(url_field, window)

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
