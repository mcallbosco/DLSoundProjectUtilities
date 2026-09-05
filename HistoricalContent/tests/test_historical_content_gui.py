from __future__ import annotations

import tkinter as tk
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from historical_content.settings import DEFAULT_WEBSITE_DIR, LEGACY_WEBSITE_DIR, load_config
from historical_content.app.gui import (
    HistoricalContentGUI,
)


class _FakeButton:
    def __init__(self):
        self.state = tk.NORMAL

    def configure(self, *, state):
        self.state = state


class HistoricalContentGuiTests(unittest.TestCase):
    def test_load_config_migrates_the_old_builtin_website_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            config_path = Path(temporary) / "config.json"
            config_path.write_text(
                json.dumps({"websiteDir": str(LEGACY_WEBSITE_DIR)}),
                encoding="utf-8",
            )
            with patch(
                "historical_content.settings.CONFIG_PATH",
                config_path,
            ):
                config = load_config()

        self.assertEqual(config["websiteDir"], str(DEFAULT_WEBSITE_DIR))

    def test_operations_are_mutually_exclusive(self):
        gui = object.__new__(HistoricalContentGUI)
        gui.active_operation = None
        gui.create_button = _FakeButton()
        gui.preview_button = _FakeButton()
        gui.categories_button = _FakeButton()
        gui.local_versions_button = _FakeButton()
        gui.publish_button = _FakeButton()
        gui.custom_mod_button = _FakeButton()

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
                gui.custom_mod_button,
            )
        ))

        with patch(
            "historical_content.app.gui.messagebox.showwarning"
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
                gui.custom_mod_button,
            )
        ))


if __name__ == "__main__":
    unittest.main()
