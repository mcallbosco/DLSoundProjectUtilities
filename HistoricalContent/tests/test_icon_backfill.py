from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from HistoricalContent.historical_content.icon_backfill import (
    IconBackfillSettings,
    backfill_historical_icons,
)
from HistoricalContent.historical_content.vpk_pipeline import HISTORICAL_ICON_FORMAT_VERSION


class HistoricalIconBackfillTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data = self.root / "data"
        self.binary = self.root / "Source2Viewer-CLI.exe"
        self.mappings = self.root / "character-mappings.json"
        self.binary.write_bytes(b"binary")
        self.mappings.write_text("{}", encoding="utf-8")
        catalog = {
            "schemaVersion": 1,
            "game": "deadlock",
            "latestVersion": "latest",
            "versions": [
                {"id": "latest", "label": "Latest", "hidden": False},
                {"id": "old", "label": "Old", "hidden": False},
                {"id": "missing", "label": "Missing", "hidden": False},
            ],
        }
        self._write_json(self.data / "catalogs" / "deadlock.json", catalog)
        for version_id in ("latest", "old"):
            workspace = self.data / "workspaces" / "deadlock" / version_id
            (workspace / "source").mkdir(parents=True)
            vpk = self.root / f"{version_id}.vpk"
            vpk.write_bytes(b"vpk")
            self._write_json(workspace / "pipeline-state.json", {
                "schemaVersion": 1,
                "game": "deadlock",
                "versionId": version_id,
                "vpkPath": str(vpk),
            })
            generated = self.data / "generated" / version_id
            generated.mkdir(parents=True)
            (generated / "keep.json").write_text("{}", encoding="utf-8")
            (
                self.data / "preview-content" / "deadlock" / "versions"
                / f"preview-{version_id}"
            ).mkdir(parents=True)

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def _write_json(path: Path, value: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")

    def test_backfill_uses_four_variants_only_for_latest_and_syncs_outputs(self) -> None:
        calls: list[tuple[str, bool]] = []

        def fake_export(**kwargs):
            source_dir = kwargs["source_dir"]
            include_highlights = kwargs["include_highlight_variants"]
            version_id = source_dir.parent.name
            calls.append((version_id, include_highlights))
            variants = ["minimap", "normal"]
            if include_highlights:
                variants.extend(("gloat", "critical"))
            pack = source_dir / "IconPacks" / "default"
            icons = {}
            for variant in variants:
                folder = pack / variant
                folder.mkdir(parents=True, exist_ok=True)
                (folder / "hero.png").write_bytes(variant.encode("ascii"))
                icons[variant] = {"hero": f"{variant}/hero.png"}
            self._write_json(pack / "manifest.json", {
                "extractionFormatVersion": HISTORICAL_ICON_FORMAT_VERSION,
                "icons": icons,
            })
            return len(variants)

        with patch(
            "HistoricalContent.historical_content.icon_backfill._export_historical_icons_from_vpk",
            side_effect=fake_export,
        ):
            result = backfill_historical_icons(IconBackfillSettings(
                data_dir=self.data,
                game="deadlock",
                source2viewer_binary=self.binary,
                character_mappings=self.mappings,
            ), progress=lambda _message: None)

        self.assertEqual(calls, [("latest", True), ("old", False)])
        self.assertEqual(result.updated_versions, ("latest", "old"))
        self.assertEqual(result.skipped_versions, ("missing",))
        self.assertFalse(result.failed_versions)
        self.assertEqual(result.image_count, 6)
        latest_manifest = json.loads((
            self.data / "generated" / "latest" / "IconPacks" / "default" / "manifest.json"
        ).read_text(encoding="utf-8"))
        old_manifest = json.loads((
            self.data / "generated" / "old" / "IconPacks" / "default" / "manifest.json"
        ).read_text(encoding="utf-8"))
        self.assertEqual(set(latest_manifest["icons"]), {"minimap", "normal", "gloat", "critical"})
        self.assertEqual(set(old_manifest["icons"]), {"minimap", "normal"})
        self.assertTrue((self.data / "generated" / "old" / "keep.json").is_file())
        self.assertTrue((
            self.data / "preview-content" / "deadlock" / "versions" / "preview-old"
            / "icons" / "default" / "normal" / "hero.png"
        ).is_file())
        old_state = json.loads((
            self.data / "workspaces" / "deadlock" / "old" / "pipeline-state.json"
        ).read_text(encoding="utf-8"))
        self.assertEqual(old_state["historicalIcons"]["variants"], ["minimap", "normal"])
        self.assertEqual(
            old_state["historicalIcons"]["extractionFormatVersion"],
            HISTORICAL_ICON_FORMAT_VERSION,
        )


if __name__ == "__main__":
    unittest.main()
