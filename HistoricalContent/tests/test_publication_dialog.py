from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from historical_content import settings
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


@unittest.skipUnless(sys.platform == "win32" or os.environ.get("DISPLAY"), "Native GUI requires a display")
class PublicationMigrationTests(unittest.TestCase):
    def test_unreadable_legacy_credentials_leave_dialog_usable_and_migration_retryable(self):
        import tkinter as tk

        from historical_content.app import publication_dialog

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_dir = root / "HistoricalContent"
            app_dir.mkdir()
            legacy = root / "ContentPublisher"
            legacy.mkdir()
            legacy_config = legacy / "config.json"
            legacy_config.write_text(json.dumps({"bucket": "saved-bucket"}), encoding="utf-8")
            legacy_credentials = legacy / "credentials.dpapi"
            legacy_credentials.write_bytes(b"legacy encrypted credentials")
            destination = app_dir / "publisher-state"
            read_bytes = Path.read_bytes

            def read_unless_credentials(path):
                if path == legacy_credentials:
                    raise PermissionError("Legacy credentials cannot be read")
                return read_bytes(path)

            application = tk.Tk()
            self.addCleanup(application.destroy)
            application.withdraw()
            with (
                patch.object(publication_dialog, "CONFIG_PATH", destination / "config.json"),
                patch.object(publication_dialog, "CREDENTIAL_PATH", destination / "credentials.dpapi"),
                patch.object(publication_dialog, "STATE_DIR", destination / ".state"),
                patch.object(publication_dialog, "migrate_publisher_state", side_effect=lambda: settings.migrate_publisher_state(app_dir)),
                patch.object(Path, "read_bytes", read_unless_credentials),
                patch.dict(os.environ, {
                    "R2_ACCESS_KEY_ID": "environment-access",
                    "R2_SECRET_ACCESS_KEY": "environment-secret",
                    "CLOUDFLARE_API_TOKEN": "environment-token",
                }),
            ):
                dialog = publication_dialog.PublicationDialog(
                    application, source_dir=app_dir, game="deadlock", version="base", label="Base",
                )
                dialog.update()
                self.assertEqual(dialog.bucket_var.get(), "saved-bucket")
                self.assertEqual(dialog.access_var.get(), "environment-access")
                self.assertEqual(dialog.secret_var.get(), "environment-secret")
                self.assertEqual(dialog.token_var.get(), "environment-token")
                self.assertTrue(dialog.buttons)
                for button in dialog.buttons:
                    self.assertTrue(button.winfo_exists())
                    self.assertEqual(str(button["state"]), "normal")
                warning = dialog.log.get("1.0", "end")
                self.assertIn("WARNING:", warning)
                self.assertIn("Legacy credentials cannot be read", warning)
                self.assertIn("enter credentials", warning)
                self.assertFalse((destination / "migration-v1.complete").exists())

                dialog.access_var.set("manual-access")
                dialog.secret_var.set("manual-secret")
                dialog.token_var.set("manual-token")
                publisher_settings = dialog._settings(require_cloud=True)
                self.assertEqual(publisher_settings.bucket, "saved-bucket")
                self.assertEqual(os.environ["R2_ACCESS_KEY_ID"], "manual-access")
                self.assertEqual(os.environ["R2_SECRET_ACCESS_KEY"], "manual-secret")
                self.assertEqual(os.environ["CLOUDFLARE_API_TOKEN"], "manual-token")
                dialog.destroy()

            settings.migrate_publisher_state(app_dir)
            self.assertTrue((destination / "migration-v1.complete").is_file())
            self.assertEqual((destination / "credentials.dpapi").read_bytes(), legacy_credentials.read_bytes())
            self.assertEqual((destination / "config.json").read_bytes(), legacy_config.read_bytes())


if __name__ == "__main__":
    unittest.main()
