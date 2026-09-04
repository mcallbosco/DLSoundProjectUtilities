from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from historical_content import settings


class EntryPointTests(unittest.TestCase):
    def test_clis_run_from_an_unrelated_working_directory(self):
        commands = [
            ["-m", "historical_content.baseline_cli"],
            ["-m", "historical_content.custom_voice_mod_cli"],
            ["-m", "historical_content.publishing.cli"],
            [str(settings.APP_DIR / "baseline_cli.py")],
            [str(settings.APP_DIR / "custom_voice_mod_cli.py")],
            [str(settings.REPOSITORY_DIR / "ContentPublisher" / "publisher_cli.py")],
        ]
        with tempfile.TemporaryDirectory() as directory:
            for command in commands:
                with self.subTest(command=command):
                    result = subprocess.run(
                        [sys.executable, *command, "--help"], cwd=directory,
                        capture_output=True, text=True, timeout=30,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn("usage:", result.stdout)

    def test_domain_imports_and_bundled_defaults_need_no_gui_or_repository_cwd(self):
        script = """
import importlib.abc
import json
import sys
from importlib.resources import files

class NoGui(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, *args):
        if fullname.split('.')[0] in {'tkinter', 'AllInOne', 'modules', 'ContentPublisher'}:
            raise AssertionError('Retired or GUI import: ' + fullname)

sys.meta_path.insert(0, NoGui())
import historical_content.baseline
import historical_content.custom_voice_mod
import historical_content.publishing.core
import historical_content.publishing.selection
import historical_content.extraction.localization
from historical_content.settings import SEED_FILES
for name in SEED_FILES.values():
    assert isinstance(json.loads(files('historical_content').joinpath('defaults', name).read_text()), dict)
"""
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [sys.executable, "-I", "-c", script], cwd=directory,
                capture_output=True, text=True, timeout=30,
            )
        self.assertEqual(result.returncode, 0, result.stderr)


@unittest.skipUnless(sys.platform == "win32" or os.environ.get("DISPLAY"), "Native GUI requires a display")
class DesktopSmokeTests(unittest.TestCase):
    def test_application_and_integrated_dialogs_open_without_external_services(self):
        from historical_content.app import gui, publication_dialog
        from historical_content.app.custom_voice_mod_dialog import CustomVoiceModDialog
        from historical_content.app.local_version_dialog import LocalVersionManagerDialog
        from historical_content.app.version_manager import VersionManagerDialog
        from historical_content.publishing.core import PublisherSettings, R2Publisher

        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory)
            config = {**settings.DEFAULTS, "dataDir": str(data)}
            with patch.object(gui, "load_config", return_value=config), patch.object(gui, "load_saved_api_key", return_value=None):
                application = gui.HistoricalContentGUI()
            try:
                application.update()
                self.assertEqual(application.title(), "VLViewer Historical Content")
                self.assertTrue(application.custom_mod_button.winfo_exists())
                application.geometry("960x700")
                application.update()
                self._assert_buttons_visible(application, application)
                application._begin_operation("test")
                self.assertEqual(str(application.publish_button["state"]), "disabled")
                application._finish_operation()
                self.assertEqual(str(application.custom_mod_button["state"]), "normal")

                local = LocalVersionManagerDialog(application, data_dir=data, game="deadlock")
                local.update_idletasks()
                self.assertEqual(local.tree.get_children(), ())
                local.destroy()

                custom = CustomVoiceModDialog(
                    application, data_dir=data, game="deadlock", suggested_version="base",
                    source2viewer_binary=data / "Source2Viewer", extraction_threads=1,
                    on_import=lambda _settings: self.fail("Unexpected import"),
                )
                custom.update_idletasks()
                self.assertEqual(custom.vars["base"].get(), "base")
                custom.destroy()

                with patch.object(publication_dialog, "_load_config", return_value=publication_dialog.DEFAULTS), patch.object(publication_dialog, "load_credentials", return_value={}):
                    publish = publication_dialog.PublicationDialog(
                        application, source_dir=data, game="deadlock", version="base", label="Base",
                    )
                publish.update_idletasks()
                self.assertTrue(publish.hidden_var.get())
                publish.destroy()

                publisher = R2Publisher(PublisherSettings(data, "deadlock", "base", "Base"))
                with patch.object(VersionManagerDialog, "_refresh"):
                    remote = VersionManagerDialog(application, publisher)
                remote.update_idletasks()
                self.assertEqual(remote.tree.get_children(), ())
                remote.destroy()
            finally:
                application.destroy()

    def _assert_buttons_visible(self, application, widget):
        for child in widget.winfo_children():
            if child.winfo_class() == "TButton":
                with self.subTest(button=child.cget("text")):
                    self.assertTrue(child.winfo_ismapped())
                    right = child.winfo_rootx() - application.winfo_rootx() + child.winfo_width()
                    bottom = child.winfo_rooty() - application.winfo_rooty() + child.winfo_height()
                    self.assertLessEqual(right, application.winfo_width())
                    self.assertLessEqual(bottom, application.winfo_height())
            self._assert_buttons_visible(application, child)


if __name__ == "__main__":
    unittest.main()
