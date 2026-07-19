from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from HistoricalContent.historical_content.baseline import load_json, write_json
from HistoricalContent.historical_content.version_catalog import (
    apply_local_catalog,
    load_local_catalog,
    recalculate_version_statuses,
    register_local_version,
)


def audio_key(value: bytes) -> str:
    digest = hashlib.sha256(value).hexdigest()
    return f"sha256/{digest[:2]}/{digest}.mp3"


class LocalVersionCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name)
        self.game = "deadlock"
        self.game_root = self.data / "preview-content" / self.game
        self.game_root.mkdir(parents=True)
        write_json(self.game_root / "manifest.json", {
            "schemaVersion": 1,
            "game": self.game,
            "latestVersion": "",
            "versions": [],
        })

    def tearDown(self):
        self.temp.cleanup()

    def add_version(self, version_id: str, lines: list[dict[str, object]]) -> None:
        payload = {"abrams": {"Self": {"Test": lines}}}
        generated = self.data / "generated" / version_id
        preview = self.game_root / "versions" / f"preview-{version_id}"
        write_json(generated / "all_voicelines.json", payload)
        write_json(preview / "voicelines.json", payload)
        manifest = load_json(self.game_root / "manifest.json")
        manifest["versions"].insert(0, {
            "id": f"preview-{version_id}",
            "label": f"Preview: {version_id}",
            "hidden": False,
            "voiceLineUrl": (
                f"http://127.0.0.1:8787/{self.game}/versions/"
                f"preview-{version_id}/voicelines.json"
            ),
        })
        if not manifest["latestVersion"]:
            manifest["latestVersion"] = f"preview-{version_id}"
        write_json(self.game_root / "manifest.json", manifest)

    def test_registration_preserves_default_and_orders_new_version_first(self):
        self.add_version("base", [])
        register_local_version(self.data, self.game, "base", "Base")
        self.add_version("next", [])
        register_local_version(self.data, self.game, "next", "Next")

        catalog = load_local_catalog(self.data, self.game)
        self.assertEqual([value["id"] for value in catalog["versions"]], ["next", "base"])
        self.assertEqual(catalog["latestVersion"], "base")

    def test_adjacent_comparison_marks_new_modified_and_removed_next(self):
        self.add_version("base", [
            {"filename": "same.mp3", "audioKey": audio_key(b"old")},
            {"filename": "removed.mp3", "audioKey": audio_key(b"removed")},
        ])
        register_local_version(self.data, self.game, "base", "Base")
        self.add_version("next", [
            {"filename": "same.mp3", "audioKey": audio_key(b"new")},
            {"filename": "added.mp3", "audioKey": audio_key(b"added")},
        ])
        catalog = register_local_version(self.data, self.game, "next", "Next")
        recalculate_version_statuses(
            self.data, self.game, catalog, progress=lambda _message: None
        )

        next_payload = load_json(self.data / "generated" / "next" / "all_voicelines.json")
        next_lines = {
            line["filename"]: line for line in next_payload["abrams"]["Self"]["Test"]
        }
        self.assertEqual(next_lines["same.mp3"]["versionStatus"], {
            "comparedTo": "base",
            "change": "modified",
        })
        self.assertEqual(next_lines["added.mp3"]["versionStatus"], {
            "comparedTo": "base",
            "change": "new",
        })

        base_payload = load_json(self.data / "generated" / "base" / "all_voicelines.json")
        base_lines = {
            line["filename"]: line for line in base_payload["abrams"]["Self"]["Test"]
        }
        self.assertEqual(base_lines["same.mp3"]["versionStatus"], {})
        self.assertEqual(base_lines["removed.mp3"]["versionStatus"], {
            "removedInNextVersion": True,
            "nextVersion": "next",
        })
        preview_payload = load_json(
            self.game_root / "versions" / "preview-next" / "voicelines.json"
        )
        self.assertEqual(
            preview_payload["abrams"]["Self"]["Test"][0]["versionStatus"]["change"],
            "modified",
        )

    def test_latest_does_not_change_order_and_cannot_be_hidden(self):
        self.add_version("base", [])
        register_local_version(self.data, self.game, "base", "Base")
        self.add_version("next", [])
        catalog = register_local_version(self.data, self.game, "next", "Next")
        catalog["latestVersion"] = "base"
        applied = apply_local_catalog(
            self.data, self.game, catalog, progress=lambda _message: None
        )
        self.assertEqual([value["id"] for value in applied["versions"]], ["next", "base"])
        manifest = load_json(self.game_root / "manifest.json")
        self.assertEqual(manifest["latestVersion"], "preview-base")

        applied["versions"][1]["hidden"] = True
        with self.assertRaisesRegex(ValueError, "cannot be hidden"):
            apply_local_catalog(
                self.data, self.game, applied, progress=lambda _message: None
            )


if __name__ == "__main__":
    unittest.main()
