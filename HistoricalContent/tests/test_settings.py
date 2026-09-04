from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from historical_content import settings
from historical_content.vpk_pipeline import VpkPipelineSettings, ensure_game_configs


class SettingsMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.app = self.root / "HistoricalContent"
        self.app.mkdir()
        self.legacy = self.root / "ContentPublisher"
        self.legacy.mkdir()

    def test_saved_bundled_paths_seed_a_new_transcript_repository(self):
        config = self.app / "config.json"
        config.write_text(json.dumps({
            key: str(self.root / "Assets" / name)
            for key, name in settings.SEED_FILES.items()
        }))
        with patch.object(settings, "CONFIG_PATH", config), patch.object(settings, "REPOSITORY_DIR", self.root):
            values = settings.load_config()

        pipeline = VpkPipelineSettings(
            source2viewer_binary=self.root / "Source2Viewer",
            vpk_path=self.root / "test.vpk",
            data_dir=self.root / "data",
            transcript_repo=self.root / "transcripts",
            version_id="test",
            character_mappings=Path(values["characterMappings"]),
            topic_aliases=Path(values["topicAliases"]),
            voiceline_groups=Path(values["voicelineGroups"]),
            conversation_overrides=Path(values["conversationOverrides"]),
            transcription_vocabulary=Path(values["transcriptionVocabulary"]),
        )
        created = ensure_game_configs(pipeline)
        for path, filename in zip(created, settings.SEED_FILES.values()):
            self.assertEqual(path.read_bytes(), (settings.DEFAULTS_DIR / filename).read_bytes())

    def test_custom_paths_and_new_settings_win_over_legacy(self):
        config = self.app / "config.json"
        custom = self.root / "my-mappings.json"
        config.write_text(json.dumps({"characterMappings": str(custom), "vpkPath": "chosen.vpk"}))
        old = self.root / "AllInOne"
        old.mkdir()
        (old / "config.json").write_text(json.dumps({
            "source2viewer_binary": "saved-source2viewer",
            "vpk_path": "old.vpk",
        }))
        with patch.object(settings, "CONFIG_PATH", config), patch.object(settings, "REPOSITORY_DIR", self.root):
            values = settings.load_config()
        self.assertEqual(values["characterMappings"], str(custom))
        self.assertEqual(values["vpkPath"], "chosen.vpk")
        self.assertEqual(values["source2viewerBinary"], "saved-source2viewer")

    def test_invalid_settings_fall_back_to_defaults(self):
        config = self.app / "config.json"
        config.write_text("{broken")
        with patch.object(settings, "CONFIG_PATH", config), patch.object(settings, "REPOSITORY_DIR", self.root):
            values = settings.load_config()
        self.assertEqual(values, settings.DEFAULTS)

    def test_publisher_migration_preserves_originals_and_existing_destinations(self):
        destination = self.app / "publisher-state"
        destination.mkdir()
        (destination / "config.json").write_text('{"bucket":"new"}')
        (self.legacy / "config.json").write_text('{"bucket":"old"}')
        encrypted = b"opaque legacy encrypted bytes"
        (self.legacy / "credentials.dpapi").write_bytes(encrypted)
        cache = self.legacy / ".state" / "deadlock" / "base" / "local-hashes.json"
        cache.parent.mkdir(parents=True)
        cache.write_text('{"files":{}}')

        settings.migrate_publisher_state(self.app)

        self.assertEqual(json.loads((destination / "config.json").read_text()), {"bucket": "new"})
        self.assertEqual((destination / "credentials.dpapi").read_bytes(), encrypted)
        self.assertEqual((self.legacy / "credentials.dpapi").read_bytes(), encrypted)
        self.assertEqual((destination / cache.relative_to(self.legacy)).read_bytes(), cache.read_bytes())

    def test_forgotten_credentials_are_not_imported_again(self):
        (self.legacy / "credentials.dpapi").write_bytes(b"old credential")
        settings.migrate_publisher_state(self.app)
        saved = self.app / "publisher-state" / "credentials.dpapi"
        saved.unlink()

        settings.migrate_publisher_state(self.app)

        self.assertFalse(saved.exists())

    def test_failed_credential_copy_can_retry_without_overwriting_settings(self):
        (self.legacy / "config.json").write_text('{"bucket":"old"}')
        (self.legacy / "credentials.dpapi").write_bytes(b"encrypted")
        real_copy = settings._copy_missing

        def fail_credentials(source, destination):
            if source.name == "credentials.dpapi":
                raise OSError("unreadable credential")
            real_copy(source, destination)

        with patch.object(settings, "_copy_missing", side_effect=fail_credentials):
            with self.assertRaisesRegex(OSError, "unreadable credential"):
                settings.migrate_publisher_state(self.app)
        destination = self.app / "publisher-state"
        self.assertFalse((destination / "migration-v1.complete").exists())
        (destination / "config.json").write_text('{"bucket":"edited"}')

        settings.migrate_publisher_state(self.app)

        self.assertEqual(json.loads((destination / "config.json").read_text()), {"bucket": "edited"})
        self.assertEqual((destination / "credentials.dpapi").read_bytes(), b"encrypted")

    def test_cache_copy_failure_does_not_block_startup(self):
        cache = self.legacy / ".state" / "local-hashes.json"
        cache.parent.mkdir()
        cache.write_text("not important")
        real_copy = settings._copy_missing

        def fail_cache(source, destination):
            if source.name == "local-hashes.json":
                raise OSError("unreadable cache")
            real_copy(source, destination)

        with patch.object(settings, "_copy_missing", side_effect=fail_cache):
            settings.migrate_publisher_state(self.app)
        self.assertTrue((self.app / "publisher-state" / "migration-v1.complete").exists())


if __name__ == "__main__":
    unittest.main()
