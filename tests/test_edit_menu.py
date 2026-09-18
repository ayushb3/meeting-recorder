"""The Edit menu exists so ⌘X/⌘C/⌘V work in this app's text fields.

An accessory app (LSUIElement) has no menu bar, so AppKit has nowhere to
dispatch the standard editing selectors and pasting into any NSTextField
silently does nothing. These tests cover the parts that do not need AppKit.
"""
import sys
from unittest.mock import MagicMock, patch

import ui.edit_menu as edit_menu


class TestEditItems:
    def test_covers_the_standard_editing_commands(self):
        selectors = {sel for _, sel, _ in edit_menu._EDIT_ITEMS if sel}
        assert {"cut:", "copy:", "paste:", "selectAll:"} <= selectors

    def test_paste_is_bound_to_cmd_v(self):
        paste = next(i for i in edit_menu._EDIT_ITEMS if i[1] == "paste:")
        assert paste[2] == "v"

    def test_redo_uses_shift_z(self):
        # Capital Z is how AppKit expresses ⇧⌘Z.
        redo = next(i for i in edit_menu._EDIT_ITEMS if i[1] == "redo:")
        assert redo[2] == "Z"

    def test_separator_is_a_none_triple(self):
        assert (None, None, None) in edit_menu._EDIT_ITEMS


class TestInstallEditMenu:
    def setup_method(self):
        edit_menu._installed = False

    def teardown_method(self):
        edit_menu._installed = False

    def test_returns_false_without_appkit(self):
        # Non-macOS or a stripped test environment: degrade, never raise.
        with patch.dict(sys.modules, {"AppKit": None}):
            assert edit_menu.install_edit_menu() is False

    def test_is_idempotent(self):
        edit_menu._installed = True
        # Already installed: returns True without touching AppKit at all.
        with patch.dict(sys.modules, {"AppKit": None}):
            assert edit_menu.install_edit_menu() is True

    def test_never_raises_when_appkit_misbehaves(self):
        fake = MagicMock()
        fake.NSApplication.sharedApplication.side_effect = RuntimeError("no app")
        with patch.dict(sys.modules, {"AppKit": fake}):
            assert edit_menu.install_edit_menu() is False
        assert edit_menu._installed is False
