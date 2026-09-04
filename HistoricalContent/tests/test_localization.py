from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from historical_content.extraction.localization import (
    LocalizationMetadataError,
    export_hero_names,
    export_localizations,
    get_language_metadata,
)
from historical_content.vpk_pipeline import VpkPipelineSettings, _export_localization

FIXTURE = Path(__file__).parent / "fixtures" / "localization"


def canonical_output(path: Path) -> str:
    data = json.loads(path.read_text(encoding="utf-8"))
    if "generated_at" in data:
        data["generated_at"] = "<generated_at>"
    if "source_directory" in data:
        data["source_directory"] = "<source_directory>"
    if "generated_at" in data.get("meta", {}):
        data["meta"]["generated_at"] = "<generated_at>"
    return json.dumps(data, indent=2, ensure_ascii=False)


class LocalizationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "input"
        shutil.copytree(FIXTURE / "input", self.source)
        self.destination = self.root / "output"
        self.messages = []

    def export_voices(self):
        export_localizations(
            self.source / "voicelines", self.destination, self.messages.append
        )

    def export_heroes(self, mappings=None):
        export_hero_names(
            self.source / "heroes",
            self.destination,
            mappings or self.source / "character_mappings.json",
            self.messages.append,
        )

    def assert_legacy_outputs(self, destination):
        expected = FIXTURE / "expected"
        self.assertEqual(
            sorted(path.name for path in expected.iterdir()),
            sorted(path.name for path in destination.iterdir()),
        )
        for path in expected.iterdir():
            with self.subTest(file=path.name):
                self.assertEqual(
                    canonical_output(destination / path.name),
                    path.read_text(encoding="utf-8"),
                )

    def test_exports_match_legacy_bytes_except_timestamp_and_source_path(self):
        self.export_voices()
        self.export_heroes()
        self.assert_legacy_outputs(self.destination)
        self.assertIn(
            "[Hero Names] Export complete. Keys: 3, unmatched tokens: 1, duplicate language hits: 2",
            self.messages,
        )

    def test_all_language_metadata_matches_legacy(self):
        expected = json.loads(
            (FIXTURE / "language_metadata.json").read_text(encoding="utf-8")
        )
        for language, metadata in expected.items():
            with self.subTest(language=language):
                self.assertEqual(
                    get_language_metadata(f" {language.upper()} "), metadata
                )

    def test_unknown_languages_report_the_offending_file(self):
        for folder, prefix, export in (
            ("voicelines", "citadel_generated_vo_", self.export_voices),
            ("heroes", "citadel_gc_hero_names_", self.export_heroes),
        ):
            with self.subTest(folder=folder):
                path = self.source / folder / f"{prefix}unknown.txt"
                path.write_text('"Tokens"\n{\n}\n', encoding="utf-8")
                with self.assertRaisesRegex(
                    LocalizationMetadataError,
                    f"file '{path.name}' is missing supporting info: Missing metadata entry for language 'unknown'",
                ):
                    export()

    def test_missing_source_is_reported_without_creating_output(self):
        export_localizations(
            self.root / "missing", self.destination, self.messages.append
        )
        self.assertFalse(self.destination.exists())
        self.assertIn("Source directory not found", self.messages[0])

    def test_empty_source_creates_no_output_files(self):
        empty = self.root / "empty"
        empty.mkdir()
        export_localizations(empty, self.destination, self.messages.append)
        self.assertEqual(list(self.destination.iterdir()), [])
        self.assertIn("No localization files found", self.messages[-1])

    def test_invalid_hero_mappings_raise_instead_of_silently_losing_names(self):
        with self.assertRaisesRegex(
            FileNotFoundError, "Character mappings file not found"
        ):
            self.export_heroes(self.root / "missing.json")
        invalid = self.root / "invalid.json"
        invalid.write_text("[]", encoding="utf-8")
        with self.assertRaisesRegex(
            ValueError, "Character mappings file is not a JSON object"
        ):
            self.export_heroes(invalid)

    def test_unreadable_language_is_skipped_and_not_advertised(self):
        path = self.source / "voicelines" / "citadel_generated_vo_french.txt"
        path.unlink()
        path.mkdir()
        self.export_voices()
        manifest = json.loads(
            (self.destination / "manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            [entry["language"] for entry in manifest["languages"]],
            ["english", "japanese"],
        )
        self.assertTrue(
            any(
                "Failed to parse citadel_generated_vo_french.txt" in message
                for message in self.messages
            )
        )
        self.assertFalse((self.destination / "french.json").exists())

    def test_failed_language_write_is_excluded_from_manifest_and_index(self):
        self.destination.mkdir()
        (self.destination / "french.json").mkdir()
        self.export_voices()
        manifest = json.loads(
            (self.destination / "manifest.json").read_text(encoding="utf-8")
        )
        index = json.loads(
            (self.destination / "voiceline_localizations.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            [entry["language"] for entry in manifest["languages"]],
            ["english", "japanese"],
        )
        self.assertTrue(all("french" not in languages for languages in index.values()))
        self.assertTrue(
            any("Failed to write french.json" in message for message in self.messages)
        )

    def test_pipeline_skips_localization_when_disabled_or_missing(self):
        settings = VpkPipelineSettings(
            self.root, self.root, self.root, self.root, "test"
        )
        with patch("historical_content.vpk_pipeline.export_localizations") as export:
            _export_localization(
                settings, self.root, None, None, self.root, self.messages.append
            )
            settings = replace(settings, extract_localization=False)
            _export_localization(
                settings, self.root, self.source, None, self.root, self.messages.append
            )
        export.assert_not_called()

    def test_pipeline_exports_from_another_directory_without_tk_or_legacy_imports(self):
        script = """
import importlib.abc
import sys
from pathlib import Path

class NoLegacyImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in {"tkinter", "AllInOne", "modules"}:
            raise AssertionError(f"Unexpected GUI/legacy import: {fullname}")

sys.meta_path.insert(0, NoLegacyImports())
from historical_content.vpk_pipeline import VpkPipelineSettings, _export_localization
source, destination = map(Path, sys.argv[1:])
settings = VpkPipelineSettings(destination, destination, destination, destination, "test")
_export_localization(
    settings, destination, source / "voicelines", source / "heroes",
    source / "character_mappings.json", lambda message: None,
)
assert "tkinter" not in sys.modules
"""
        completed = subprocess.run(
            [sys.executable, "-c", script, str(self.source), str(self.destination)],
            cwd=self.root,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assert_legacy_outputs(self.destination / "Localization")


if __name__ == "__main__":
    unittest.main()
