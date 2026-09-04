"""Preserve exports captured from the pre-extraction Historical Content parser."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from historical_content.parsing.conversations import parse_conversations
from historical_content.parsing.voicelines import parse_voicelines

TESTS = Path(__file__).resolve().parent
PACKAGE_ROOT = TESTS.parent
ASSETS = PACKAGE_ROOT / "historical_content" / "defaults"


class ParserParityTests(unittest.TestCase):
    def test_legacy_export_parity(self):
        # Recorded before extraction at df149f7d: current and rr_test filenames,
        # historical folders, multipart aliases, grouping, overrides, collisions,
        # official text, phantom lines, and conversation variations/completeness.
        fixture = json.loads(
            (TESTS / "fixtures/parser-parity.json").read_text(encoding="utf-8")
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            audio = root / "audio"
            audio.mkdir()
            for filename in fixture["filenames"]:
                path = audio / filename
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"audio")
                timestamp = datetime(2023, 11, 14, 12).timestamp()  # noqa: DTZ001 - local export date
                os.utime(path, (timestamp, timestamp))
            vdf = root / "vo.txt"
            vdf.write_text("\n".join(fixture["vdfLines"]), encoding="utf-8")
            overrides = root / "conversation-overrides.json"
            overrides.write_text(
                json.dumps(
                    {
                        "complete_conversations": fixture["completeConversations"],
                    }
                ),
                encoding="utf-8",
            )

            voices, unresolved = parse_voicelines(
                audio,
                ASSETS / "character_mappings.json",
                ASSETS / "topic_mappings.json",
                ASSETS / "voiceline_groups.json",
                vdf,
                include_phantom=True,
                progress=lambda _message: None,
                audio_filename_overrides=fixture["audioFilenameOverrides"],
            )
            conversations = parse_conversations(
                audio,
                ASSETS / "character_mappings.json",
                overrides,
                vdf,
                include_phantom=True,
                audio_filename_overrides=fixture["audioFilenameOverrides"],
            )
        conversations.pop("export_date")
        # Compare serialization too: topic ordering is part of the export.
        self.assertEqual(json.dumps(voices), json.dumps(fixture["voicelines"]))
        self.assertEqual(sorted(unresolved), fixture["unresolved"])
        self.assertEqual(
            json.dumps(conversations), json.dumps(fixture["conversations"])
        )

    def test_parsers_run_without_tkinter_or_legacy_import_paths(self):
        script = """
import builtins
import runpy
import sys
import unittest

original_import = builtins.__import__
def headless_import(name, *args, **kwargs):
    if name == "tkinter" or name.startswith("tkinter."):
        raise AssertionError("Content parsers must not import Tkinter")
    if name == "modules" or name.startswith("modules."):
        raise AssertionError("Content parsers must use package imports")
    return original_import(name, *args, **kwargs)
builtins.__import__ = headless_import
before = list(sys.path)
namespace = runpy.run_path(sys.argv[1])
suite = unittest.TestSuite([namespace["ParserParityTests"]("test_legacy_export_parity")])
result = unittest.TextTestRunner().run(suite)
assert sys.path == before, "Content parsers must not mutate sys.path"
sys.exit(not result.wasSuccessful())
"""
        result = subprocess.run(
            [sys.executable, "-c", script, str(Path(__file__).resolve())],
            env={**os.environ, "PYTHONPATH": str(PACKAGE_ROOT)},
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
