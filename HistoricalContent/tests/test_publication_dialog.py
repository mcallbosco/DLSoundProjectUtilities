from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from historical_content.publishing.core import PublisherError
from historical_content.publishing.selection import bulk_publish_order, local_publish_versions


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

            versions, latest = local_publish_versions(generated / "new", "deadlock")

            self.assertEqual([item["id"] for item in versions], ["new", "old", "draft"])
            self.assertEqual([item["hidden"] for item in versions], [False, True, True])
            self.assertEqual(latest, "new")

    def test_orders_selected_custom_versions_after_their_official_bases(self) -> None:
        base = {"id": "base"}
        custom = {"id": "custom"}
        settings = {
            "base": SimpleNamespace(kind="official", based_on_version=""),
            "custom": SimpleNamespace(kind="custom", based_on_version="base"),
        }

        ordered = bulk_publish_order([base, custom], settings, {"versions": []})

        self.assertEqual([item["id"] for item in ordered], ["base", "custom"])

    def test_rejects_a_custom_batch_when_its_base_is_unavailable(self) -> None:
        custom = {"id": "custom"}
        settings = {
            "custom": SimpleNamespace(kind="custom", based_on_version="missing"),
        }

        with self.assertRaisesRegex(PublisherError, "neither selected nor published"):
            bulk_publish_order([custom], settings, {"versions": []})


if __name__ == "__main__":
    unittest.main()
