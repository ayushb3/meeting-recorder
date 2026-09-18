# ui/edit_menu.py
"""
Install a standard Edit menu so ⌘X / ⌘C / ⌘V work in this app's text fields.

A rumps app is an accessory (LSUIElement): no Dock icon and no application menu
bar. AppKit routes ⌘V by looking for a menu item whose selector is ``paste:``,
so with no menu bar the shortcut reaches nothing and typing into any NSTextField
in this app cannot paste — the stop dialog, the settings window and the import
dialogs all share the problem.

Installing a hidden application menu gives those keystrokes somewhere to
dispatch. The menu bar itself stays invisible because the app is an accessory;
only the key equivalents become live.

Call install_edit_menu() once at startup.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

_installed = False

# (title, selector, key equivalent) for the standard editing commands.
_EDIT_ITEMS = [
    ("Undo", "undo:", "z"),
    ("Redo", "redo:", "Z"),
    (None, None, None),  # separator
    ("Cut", "cut:", "x"),
    ("Copy", "copy:", "c"),
    ("Paste", "paste:", "v"),
    ("Select All", "selectAll:", "a"),
]


def install_edit_menu() -> bool:
    """Install the Edit menu. Returns True if installed, False if unavailable.

    Idempotent, and never raises: a failure here should not stop the app
    launching, it just means the shortcuts stay dead.
    """
    global _installed
    if _installed:
        return True

    try:
        from AppKit import NSApplication, NSMenu, NSMenuItem  # noqa: PLC0415
    except ImportError:  # non-macOS or stripped test environment
        return False

    try:
        app = NSApplication.sharedApplication()
        main_menu = app.mainMenu()
        if main_menu is None:
            main_menu = NSMenu.alloc().init()
            app.setMainMenu_(main_menu)

        # Every top-level menu needs a container item holding the real submenu.
        edit_container = NSMenuItem.alloc().init()
        edit_menu = NSMenu.alloc().initWithTitle_("Edit")

        for title, selector, key in _EDIT_ITEMS:
            if title is None:
                edit_menu.addItem_(NSMenuItem.separatorItem())
                continue
            item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                title, selector, key
            )
            # A nil target sends the action down the responder chain, which is
            # what lands it on whichever text field is focused.
            item.setTarget_(None)
            edit_menu.addItem_(item)

        edit_container.setSubmenu_(edit_menu)
        main_menu.addItem_(edit_container)
        _installed = True
        log.debug("Edit menu installed — clipboard shortcuts are live")
        return True
    except Exception:
        log.warning("Could not install the Edit menu; ⌘V will not work", exc_info=True)
        return False
