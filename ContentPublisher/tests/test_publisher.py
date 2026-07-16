from __future__ import annotations

import json
import io
import tempfile
import unittest
from pathlib import Path

from ContentPublisher.publisher import (
    PublisherSettings,
    R2Publisher,
    build_publish_plan,
    inventory_payload,
    validate_version_source,
    version_manifest_entry,
)


class MissingObjectError(Exception):
    response = {
        "Error": {"Code": "NoSuchKey"},
        "ResponseMetadata": {"HTTPStatusCode": 404},
    }


class FakeR2Client:
    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []
        self.metadata: dict[str, dict] = {}
        self.objects: dict[str, bytes] = {}

    def head_object(self, *, Bucket: str, Key: str) -> dict:
        if Key not in self.metadata:
            raise MissingObjectError(Key)
        return {"Metadata": self.metadata[Key]}

    def upload_file(self, filename: str, bucket: str, key: str, ExtraArgs: dict) -> None:
        self.events.append(("upload", key))
        self.metadata[key] = dict(ExtraArgs["Metadata"])

    def get_object(self, *, Bucket: str, Key: str) -> dict:
        if Key not in self.objects:
            raise MissingObjectError(Key)
        return {"Body": io.BytesIO(self.objects[Key])}

    def put_object(self, *, Bucket: str, Key: str, **kwargs) -> None:
        self.events.append(("put", Key))
        self.metadata[Key] = dict(kwargs.get("Metadata", {}))
        body = kwargs.get("Body", b"")
        self.objects[Key] = body if isinstance(body, bytes) else body.read()


class PublisherCoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        (self.root / "Audio").mkdir()
        (self.root / "Audio" / "line_01.mp3").write_bytes(b"audio-one")
        (self.root / "all_conversations.json").write_text(
            json.dumps(
                {
                    "conversations": [
                        {"id": "conversation-1", "lines": [{"filename": "line_01.mp3"}]}
                    ]
                }
            ),
            encoding="utf-8",
        )
        (self.root / "all_voicelines.json").write_text(
            json.dumps({"hero": {"lines": [{"filename": "line_01.mp3"}]}}),
            encoding="utf-8",
        )
        (self.root / "coverage.json").write_text(
            json.dumps({"summary": {"matched_files": 1, "total_files": 1}}),
            encoding="utf-8",
        )
        (self.root / "Localization").mkdir()
        (self.root / "Localization" / "manifest.json").write_text(
            json.dumps({"languages": []}), encoding="utf-8"
        )
        (self.root / "IconPacks" / "default" / "normal").mkdir(parents=True)
        (self.root / "IconPacks" / "default" / "normal" / "hero.png").write_bytes(
            b"icon-one"
        )
        (self.root / "IconPacks" / "default" / "manifest.json").write_text(
            json.dumps(
                {
                    "id": "default",
                    "icons": {"normal": {"hero": "normal/hero.png"}},
                    "fallbackIcon": "normal/hero.png",
                }
            ),
            encoding="utf-8",
        )
        self.settings = PublisherSettings(
            source_dir=self.root,
            game="deadlock",
            version="deadlock-test",
            label="Test version",
            state_dir=self.root / ".state",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_validation_and_legacy_path_mapping(self) -> None:
        report = validate_version_source(self.root)
        self.assertTrue(report.valid, report.errors)
        paths = {item.relative_path for item in report.files}
        self.assertIn("conversations.json", paths)
        self.assertIn("voicelines.json", paths)
        self.assertIn("audio/line_01.mp3", paths)
        self.assertIn("localization/manifest.json", paths)
        self.assertIn("icons/default/manifest.json", paths)
        self.assertEqual(report.referenced_audio_count, 1)

    def test_missing_referenced_audio_is_an_error(self) -> None:
        (self.root / "Audio" / "line_01.mp3").unlink()
        report = validate_version_source(self.root)
        self.assertFalse(report.valid)
        self.assertTrue(any("missing audio" in item for item in report.errors))

    def test_json_changes_are_mutable_and_binary_changes_conflict(self) -> None:
        initial = build_publish_plan(self.settings)
        remote = inventory_payload(self.settings, initial, content_revision=1)

        unchanged = build_publish_plan(self.settings, remote)
        self.assertFalse(unchanged.upload_new)
        self.assertFalse(unchanged.upload_changed_json)
        self.assertFalse(unchanged.immutable_conflicts)

        coverage_path = self.root / "coverage.json"
        coverage_path.write_text(json.dumps({"summary": {"matched_files": 2}}), encoding="utf-8")
        changed_json = build_publish_plan(self.settings, remote)
        self.assertEqual(
            [item.relative_path for item in changed_json.upload_changed_json],
            ["coverage.json"],
        )

        (self.root / "Audio" / "line_01.mp3").write_bytes(b"audio-two")
        changed_binary = build_publish_plan(self.settings, remote)
        self.assertEqual(
            [item.relative_path for item, _ in changed_binary.immutable_conflicts],
            ["audio/line_01.mp3"],
        )
        self.assertFalse(changed_binary.can_publish)

    def test_remote_only_binary_stays_in_next_inventory(self) -> None:
        initial = build_publish_plan(self.settings)
        remote = inventory_payload(self.settings, initial, content_revision=1)
        remote["files"]["audio/old_line.mp3"] = {
            "size": 3,
            "sha256": "abc",
            "contentType": "audio/mpeg",
            "mutable": False,
        }
        plan = build_publish_plan(self.settings, remote)
        next_inventory = inventory_payload(self.settings, plan, content_revision=1)
        retained = next_inventory["files"]["audio/old_line.mp3"]
        self.assertFalse(retained["presentInSource"])

    def test_manifest_keeps_logical_version_and_tracks_revision(self) -> None:
        entry = version_manifest_entry(self.settings, content_revision=7)
        self.assertEqual(entry["id"], "deadlock-test")
        self.assertEqual(entry["contentRevision"], 7)
        self.assertTrue(entry["audioBaseUrl"].endswith("/deadlock/versions/deadlock-test/audio/"))
        self.assertFalse(entry["hidden"])

    def test_hidden_version_cannot_be_promoted_during_publish(self) -> None:
        settings = PublisherSettings(
            source_dir=self.root,
            game="deadlock",
            version="deadlock-hidden",
            label="Hidden version",
            hidden=True,
            promote_to_latest=True,
        )
        with self.assertRaisesRegex(Exception, "hidden and promoted"):
            build_publish_plan(settings)

    def test_republishing_preserves_order_and_visibility_is_editable(self) -> None:
        settings = PublisherSettings(
            source_dir=self.root,
            game="deadlock",
            version="deadlock-test",
            label="Updated test label",
            bucket="test-bucket",
            endpoint_url="https://example.r2.cloudflarestorage.com",
            hidden=True,
            promote_to_latest=False,
        )
        client = FakeR2Client()
        client.objects["deadlock/manifest.json"] = json.dumps(
            {
                "schemaVersion": 1,
                "game": "deadlock",
                "latestVersion": "deadlock-newer",
                "versions": [
                    {"id": "deadlock-newer", "label": "Newer", "hidden": False},
                    {"id": "deadlock-test", "label": "Old label", "hidden": False},
                ],
            }
        ).encode("utf-8")
        publisher = R2Publisher(settings)
        publisher._client = client
        manifest, entry = publisher._build_game_manifest(content_revision=3)
        self.assertEqual(
            [item["id"] for item in manifest["versions"]],
            ["deadlock-newer", "deadlock-test"],
        )
        self.assertTrue(entry["hidden"])
        self.assertEqual(entry["label"], "Updated test label")

    def test_catalog_can_promote_an_older_unhidden_version_in_new_order(self) -> None:
        settings = PublisherSettings(
            source_dir=self.root,
            game="deadlock",
            version="",
            label="",
            bucket="test-bucket",
            endpoint_url="https://example.r2.cloudflarestorage.com",
        )
        client = FakeR2Client()
        publisher = R2Publisher(settings)
        publisher._client = client
        manifest = {
            "latestVersion": "deadlock-old",
            "versions": [
                {"id": "deadlock-old", "label": "Old", "hidden": False},
                {"id": "deadlock-new", "label": "New", "hidden": False},
            ],
        }
        saved = publisher.save_game_manifest(manifest)
        self.assertEqual(saved["latestVersion"], "deadlock-old")
        self.assertEqual(saved["versions"][0]["id"], "deadlock-old")
        stored = json.loads(client.objects["deadlock/manifest.json"])
        self.assertEqual(stored["latestVersion"], "deadlock-old")

    def test_catalog_rejects_hidden_latest_version(self) -> None:
        settings = PublisherSettings(
            source_dir=self.root,
            game="deadlock",
            version="",
            label="",
            bucket="test-bucket",
            endpoint_url="https://example.r2.cloudflarestorage.com",
        )
        publisher = R2Publisher(settings)
        publisher._client = FakeR2Client()
        manifest = {
            "latestVersion": "deadlock-hidden",
            "versions": [
                {"id": "deadlock-hidden", "label": "Hidden", "hidden": True}
            ],
        }
        with self.assertRaisesRegex(Exception, "latest version cannot be hidden"):
            publisher.save_game_manifest(manifest)

    def test_publish_orders_binary_before_json_and_game_manifest_last(self) -> None:
        settings = PublisherSettings(
            source_dir=self.root,
            game="deadlock",
            version="deadlock-test",
            label="Test version",
            bucket="test-bucket",
            endpoint_url="https://example.r2.cloudflarestorage.com",
            state_dir=self.root / ".state",
        )
        plan = build_publish_plan(settings)
        client = FakeR2Client()
        publisher = R2Publisher(settings)
        publisher._client = client
        result = publisher.publish(plan)

        upload_events = [event for event in client.events if event[0] == "upload"]
        mutable_indexes = [
            index for index, (_action, key) in enumerate(upload_events) if key.endswith(".json")
        ]
        binary_indexes = [
            index for index, (_action, key) in enumerate(upload_events) if not key.endswith(".json")
        ]
        self.assertLess(max(binary_indexes), min(mutable_indexes))
        self.assertEqual(client.events[-1], ("put", "deadlock/manifest.json"))
        self.assertEqual(result["contentRevision"], 1)


if __name__ == "__main__":
    unittest.main()
