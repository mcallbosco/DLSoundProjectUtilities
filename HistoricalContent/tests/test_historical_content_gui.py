from __future__ import annotations

import tkinter as tk
import unittest
from unittest.mock import patch

from HistoricalContent.historical_content_gui import HistoricalContentGUI


class _FakeButton:
    def __init__(self):
        self.state = tk.NORMAL

    def configure(self, *, state):
        self.state = state


class HistoricalContentGuiTests(unittest.TestCase):
    def test_operations_are_mutually_exclusive(self):
        gui = object.__new__(HistoricalContentGUI)
        gui.active_operation = None
        gui.create_button = _FakeButton()
        gui.preview_button = _FakeButton()
        gui.categories_button = _FakeButton()
        gui.local_versions_button = _FakeButton()
        gui.publish_button = _FakeButton()

        self.assertTrue(gui._begin_operation("content regeneration"))
        self.assertEqual(gui.active_operation, "content regeneration")
        self.assertTrue(all(
            button.state == tk.DISABLED
            for button in (
                gui.create_button,
                gui.preview_button,
                gui.categories_button,
                gui.local_versions_button,
                gui.publish_button,
            )
        ))

        with patch(
            "HistoricalContent.historical_content_gui.messagebox.showwarning"
        ) as warning:
            self.assertFalse(gui._begin_operation("local preview seeding"))
        warning.assert_called_once()
        self.assertEqual(gui.active_operation, "content regeneration")

        gui._finish_operation()
        self.assertIsNone(gui.active_operation)
        self.assertTrue(all(
            button.state == tk.NORMAL
            for button in (
                gui.create_button,
                gui.preview_button,
                gui.categories_button,
                gui.local_versions_button,
                gui.publish_button,
            )
        ))


if __name__ == "__main__":
    unittest.main()
