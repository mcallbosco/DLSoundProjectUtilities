from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from HistoricalContent.publication_dialog import _local_publish_versions


class BulkPublicationDiscoveryTests(unittest.TestCase):
    def test_uses_catalog_order_and_appends_uncataloged_generated_versions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data_dir = Path(temporary)
            generated = data_dir / "generated"
            for version_id in ("new", "old", "draft"):
                (generated / version_id).mkdir(parents=True)
            catalog_dir = data_dir / "catalogs"
            catalog_dir.mkdir()
            (catalog_dir / "deadlock.json").write_text(
                json.dumps({
                    "latestVersion": "new",
                    "versions": [
                        {"id": "new", "label": "New", "hidden": False},
                        {"id": "old", "label": "Old", "hidden": True},
                    ],
                }),
                encoding="utf-8",
            )

            versions, latest = _local_publish_versions(generated / "new", "deadlock")

            self.assertEqual([item["id"] for item in versions], ["new", "old", "draft"])
            self.assertEqual([item["hidden"] for item in versions], [False, True, True])
            self.assertEqual(latest, "new")


if __name__ == "__main__":
    unittest.main()
