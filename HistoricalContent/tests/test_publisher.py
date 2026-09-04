from __future__ import annotations

import json
import io
import hashlib
import tempfile
import unittest
from pathlib import Path

from historical_content.publishing.core import (
    PublisherError,
    PublisherSettings,
    R2Publisher,
    build_publish_plan,
    collect_content_characters,
    game_characters_payload,
    inventory_payload,
    validate_publisher_source,
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

    def list_objects_v2(self, *, Bucket: str, Prefix: str, MaxKeys: int, **kwargs) -> dict:
        keys = sorted(
            key for key in set(self.metadata) | set(self.objects)
            if key.startswith(Prefix)
        )
        return {
            "KeyCount": len(keys[:MaxKeys]),
            "Contents": [
                {"Key": key, "Size": len(self.objects.get(key, b""))}
                for key in keys[:MaxKeys]
            ],
            "IsTruncated": False,
        }

    def delete_objects(self, *, Bucket: str, Delete: dict) -> dict:
        for item in Delete.get("Objects", []):
            key = item["Key"]
            self.events.append(("delete", key))
            self.metadata.pop(key, None)
            self.objects.pop(key, None)
        return {"Deleted": Delete.get("Objects", [])}


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
        (self.root / "character-names.json").write_text(
            json.dumps({
                "schemaVersion": 1,
                "game": "deadlock",
                "names": {"hero": "Hero", "internal_hero": "Hero"},
            }),
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

    def use_shared_audio(self) -> tuple[str, str]:
        audio_bytes = (self.root / "Audio" / "line_01.mp3").read_bytes()
        digest = hashlib.sha256(audio_bytes).hexdigest()
        audio_key = f"sha256/{digest[:2]}/{digest}.mp3"
        for filename in ("all_conversations.json", "all_voicelines.json"):
            path = self.root / filename
            payload = json.loads(path.read_text(encoding="utf-8"))

            def apply(value):
                if isinstance(value, dict):
                    result = {key: apply(child) for key, child in value.items()}
                    if result.get("filename") == "line_01.mp3":
                        result["audioKey"] = audio_key
                    return result
                if isinstance(value, list):
                    return [apply(child) for child in value]
                return value

            path.write_text(json.dumps(apply(payload)), encoding="utf-8")
        shared_path = self.root / "SharedAudio" / Path(*audio_key.split("/"))
        shared_path.parent.mkdir(parents=True)
        shared_path.write_bytes(audio_bytes)
        (self.root / "Audio" / "line_01.mp3").unlink()
        (self.root / "Audio").rmdir()
        return digest, audio_key

    def add_character_name_images(self) -> None:
        root = self.root / "CharacterNameImages"
        (root / "english").mkdir(parents=True)
        (root / "english" / "hero.hash.webp").write_bytes(b"webp-image")
        (root / "manifest.json").write_text(
            json.dumps({
                "schemaVersion": 1,
                "extractionFormatVersion": 1,
                "maxHeight": 512,
                "languages": {
                    "english": {
                        "hero": {
                            "path": "english/hero.hash.webp",
                            "width": 640,
                            "height": 512,
                        }
                    }
                },
            }),
            encoding="utf-8",
        )

    def add_character_select_backgrounds(self) -> None:
        root = self.root / "CharacterSelectBackgrounds"
        root.mkdir(parents=True)
        (root / "familiar.hash.webp").write_bytes(b"background-webp")
        (root / "manifest.json").write_text(
            json.dumps({
                "schemaVersion": 1,
                "extractionFormatVersion": 2,
                "crop": "right-half",
                "maxWidth": 1024,
                "backgrounds": {
                    "rem": {
                        "path": "familiar.hash.webp",
                        "width": 1024,
                        "height": 1024,
                        "accentColor": "#284b3a",
                    }
                },
            }),
            encoding="utf-8",
        )
    def configure_custom_source(self, *, warning_count: int = 0) -> PublisherSettings:
        for filename in ("all_conversations.json", "all_voicelines.json"):
            path = self.root / filename
            payload = json.loads(path.read_text(encoding="utf-8"))

            def apply(value):
                if isinstance(value, dict):
                    result = {key: apply(child) for key, child in value.items()}
                    if result.get("filename") == "line_01.mp3":
                        result["transcription"] = "Закрепленный текст"
                        result["officialtranscription"] = False
                        result.pop("audioKey", None)
                        result.pop("audioUrl", None)
                        result["versionStatus"] = {}
                    return result
                if isinstance(value, list):
                    return [apply(child) for child in value]
                return value

            path.write_text(json.dumps(apply(payload), ensure_ascii=False), encoding="utf-8")

        transcript = self.root / "TranscriptSource" / "source.vdf"
        transcript.parent.mkdir()
        transcript.write_text('"line" "Закрепленный текст"', encoding="utf-8")
        source = {
            "repository": "example/transcripts",
            "revision": "abc123",
            "repositoryPath": "localizations/source.vdf",
            "path": "source.vdf",
            "sha256": hashlib.sha256(transcript.read_bytes()).hexdigest(),
            "size": transcript.stat().st_size,
            "entryCount": 1,
            "parser": "vlviewer-vdf-kv-v1",
            "pinVerified": True,
            "attribution": {"credits": [{"name": "Community translator"}]},
        }
        (self.root / "transcript-source.json").write_text(
            json.dumps(source), encoding="utf-8"
        )
        (self.root / "custom-import-report.json").write_text(
            json.dumps({
                "schemaVersion": 1,
                "speechToTextUsed": False,
                "publishable": True,
                "warningCount": warning_count,
                "blockingWarningCount": 0,
                "warnings": [] if warning_count == 0 else [{"reason": "missing"}],
            }),
            encoding="utf-8",
        )
        (self.root / "custom-version.json").write_text(
            json.dumps({
                "schemaVersion": 1,
                "kind": "custom",
                "basedOnVersion": "ognb",
                "defaultLocalizationLanguage": "russian",
                "transcriptMode": "embedded",
                "embeddedTranscriptLanguage": "russian",
                "transcriptSource": source,
            }),
            encoding="utf-8",
        )
        return PublisherSettings(
            source_dir=self.root,
            game="deadlock",
            version="ognb-russian-mod",
            label="Russian Voice Mod",
            state_dir=self.root / ".state",
            promote_to_latest=False,
            hidden=True,
        )
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

    def test_custom_source_requires_pinned_text_and_version_local_audio(self) -> None:
        settings = self.configure_custom_source()
        report = validate_publisher_source(settings)
        self.assertTrue(report.valid, report.errors)
        plan = build_publish_plan(settings)
        self.assertTrue(plan.can_publish, plan.validation.errors)
        self.assertFalse(any(record.scope == "game" for record in plan.local_records.values()))

        entry = version_manifest_entry(settings, content_revision=1)
        self.assertEqual(entry["kind"], "custom")
        self.assertEqual(entry["basedOnVersion"], "ognb")
        self.assertEqual(entry["defaultLocalizationLanguage"], "russian")
        self.assertEqual(entry["transcriptMode"], "embedded")
        self.assertNotIn("fanLocalizationManifestUrl", entry)

    def test_custom_source_allows_non_blocking_correlation_warnings(self) -> None:
        settings = self.configure_custom_source(warning_count=1)
        report = validate_publisher_source(settings)
        self.assertTrue(report.valid, report.errors)
        self.assertTrue(any("non-blocking" in warning for warning in report.warnings))

    def test_custom_source_allows_blank_embedded_transcripts(self) -> None:
        settings = self.configure_custom_source(warning_count=1)
        for filename in ("all_conversations.json", "all_voicelines.json"):
            path = self.root / filename
            payload = json.loads(path.read_text(encoding="utf-8"))

            def blank(value):
                if isinstance(value, dict):
                    result = {key: blank(child) for key, child in value.items()}
                    if result.get("filename") == "line_01.mp3":
                        result["transcription"] = ""
                    return result
                if isinstance(value, list):
                    return [blank(child) for child in value]
                return value

            path.write_text(json.dumps(blank(payload)), encoding="utf-8")

        report = validate_publisher_source(settings)
        self.assertTrue(report.valid, report.errors)

    def test_custom_audio_can_be_replaced_under_the_same_version_id(self) -> None:
        settings = self.configure_custom_source()
        initial = build_publish_plan(settings)
        remote = inventory_payload(settings, initial, content_revision=1)
        (self.root / "Audio" / "line_01.mp3").write_bytes(b"updated mod audio")

        changed = build_publish_plan(settings, remote)

        self.assertEqual(
            [item.relative_path for item in changed.upload_changed_custom_audio],
            ["audio/line_01.mp3"],
        )
        self.assertFalse(changed.immutable_conflicts)
        self.assertTrue(changed.can_publish)

    def test_custom_base_is_validated_before_any_upload(self) -> None:
        settings = self.configure_custom_source()
        plan = build_publish_plan(settings)
        client = FakeR2Client()
        publisher = R2Publisher(settings)
        publisher._client = client

        with self.assertRaisesRegex(PublisherError, "base version is not published"):
            publisher.publish(plan)

        self.assertEqual(client.events, [])

    def test_custom_source_never_accepts_shared_audio(self) -> None:
        settings = self.configure_custom_source()
        self.use_shared_audio()
        report = validate_publisher_source(settings)
        self.assertFalse(report.valid)
        self.assertTrue(any("never SharedAudio" in error for error in report.errors))

    def test_custom_source_cannot_be_latest(self) -> None:
        settings = self.configure_custom_source()
        settings.promote_to_latest = True
        report = validate_publisher_source(settings)
        self.assertFalse(report.valid)
        self.assertIn("Custom content cannot be promoted to latest.", report.errors)

    def test_custom_source_rejects_shared_audio_references_and_source_hash_drift(self) -> None:
        settings = self.configure_custom_source()
        path = self.root / "all_voicelines.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["hero"]["lines"][0]["audioKey"] = "sha256/aa/official.mp3"
        path.write_text(json.dumps(payload), encoding="utf-8")
        (self.root / "TranscriptSource" / "source.vdf").write_text(
            '"line" "Changed later"', encoding="utf-8"
        )

        report = validate_publisher_source(settings)
        self.assertFalse(report.valid)
        self.assertTrue(any("audioKey" in error for error in report.errors))
        self.assertTrue(any("SHA-256 does not match" in error for error in report.errors))

    def test_character_name_images_validate_map_and_advertise_webp(self) -> None:
        self.add_character_name_images()
        report = validate_version_source(self.root)
        self.assertTrue(report.valid, report.errors)
        records = {item.relative_path: item for item in report.files}
        image_path = "character-name-images/english/hero.hash.webp"
        self.assertIn("character-name-images/manifest.json", records)
        self.assertEqual(records[image_path].content_type, "image/webp")

        entry = version_manifest_entry(
            self.settings,
            content_revision=3,
            has_character_name_images=True,
        )
        self.assertTrue(
            entry["characterNameImagesUrl"].endswith(
                "/character-name-images/manifest.json"
            )
        )

    def test_missing_character_name_image_reference_is_an_error(self) -> None:
        self.add_character_name_images()
        (self.root / "CharacterNameImages" / "english" / "hero.hash.webp").unlink()
        report = validate_version_source(self.root)
        self.assertFalse(report.valid)
        self.assertTrue(any("missing WebP" in error for error in report.errors))

    def test_character_select_backgrounds_validate_map_and_advertise_webp(self) -> None:
        self.add_character_select_backgrounds()
        report = validate_version_source(self.root)
        self.assertTrue(report.valid, report.errors)
        records = {item.relative_path: item for item in report.files}
        self.assertIn("character-select-backgrounds/manifest.json", records)
        self.assertEqual(
            records["character-select-backgrounds/familiar.hash.webp"].content_type,
            "image/webp",
        )

        entry = version_manifest_entry(
            self.settings,
            content_revision=3,
            has_character_select_backgrounds=True,
        )
        self.assertTrue(
            entry["characterSelectBackgroundsUrl"].endswith(
                "/character-select-backgrounds/manifest.json"
            )
        )

    def test_character_select_backgrounds_require_an_accent_color(self) -> None:
        self.add_character_select_backgrounds()
        manifest_path = self.root / "CharacterSelectBackgrounds" / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        del manifest["backgrounds"]["rem"]["accentColor"]
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        report = validate_version_source(self.root)
        self.assertFalse(report.valid)
        self.assertTrue(any("invalid accent color" in error for error in report.errors))

    def test_unsafe_character_name_image_url_is_an_error(self) -> None:
        self.add_character_name_images()
        manifest_path = self.root / "CharacterNameImages" / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["languages"]["english"]["hero"]["path"] = "https://example.com/hero.webp"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        report = validate_version_source(self.root)
        self.assertFalse(report.valid)
        self.assertTrue(any("unsafe or non-WebP" in error for error in report.errors))

    def test_shared_audio_validates_and_maps_to_game_scope(self) -> None:
        digest, audio_key = self.use_shared_audio()
        report = validate_version_source(self.root)
        self.assertTrue(report.valid, report.errors)
        shared = next(item for item in report.files if item.scope == "game")
        self.assertEqual(shared.relative_path, f"shared-audio/{audio_key}")
        self.assertEqual(shared.published_path, f"audio/{audio_key}")

        plan = build_publish_plan(self.settings)
        record = plan.local_records[f"shared-audio/{audio_key}"]
        self.assertEqual(record.sha256, digest)
        self.assertEqual(record.scope, "game")

    def test_shared_audio_is_uploaded_once_and_advertised(self) -> None:
        digest, audio_key = self.use_shared_audio()
        settings = PublisherSettings(
            source_dir=self.root,
            game="deadlock",
            version="deadlock-shared",
            label="Shared version",
            bucket="test-bucket",
            endpoint_url="https://example.r2.cloudflarestorage.com",
            state_dir=self.root / ".state",
        )
        plan = build_publish_plan(settings)
        client = FakeR2Client()
        publisher = R2Publisher(settings)
        publisher._client = client
        result = publisher.publish(plan)
        shared_object = f"deadlock/audio/{audio_key}"
        self.assertIn(("upload", shared_object), client.events)
        self.assertEqual(client.metadata[shared_object]["sha256"], digest)
        self.assertEqual(
            result["manifest"]["sharedAudioBaseUrl"],
            "https://cdn.vlviewer.com/deadlock/audio/",
        )

    def test_new_version_reuses_existing_shared_audio_object(self) -> None:
        digest, audio_key = self.use_shared_audio()
        settings = PublisherSettings(
            source_dir=self.root,
            game="deadlock",
            version="deadlock-second",
            label="Second version",
            bucket="test-bucket",
            endpoint_url="https://example.r2.cloudflarestorage.com",
            state_dir=self.root / ".state",
        )
        shared_object = f"deadlock/audio/{audio_key}"
        client = FakeR2Client()
        client.metadata[shared_object] = {"sha256": digest}
        publisher = R2Publisher(settings)
        publisher._client = client

        plan = publisher.create_plan()

        self.assertNotIn(
            f"shared-audio/{audio_key}",
            {record.relative_path for record in plan.upload_new},
        )
        self.assertIn(
            f"shared-audio/{audio_key}",
            {record.relative_path for record in plan.unchanged},
        )

    def test_missing_referenced_audio_is_an_error(self) -> None:
        (self.root / "Audio" / "line_01.mp3").unlink()
        report = validate_version_source(self.root)
        self.assertFalse(report.valid)
        self.assertTrue(any("missing audio" in item for item in report.errors))

    def test_nested_audio_keys_distinguish_duplicate_basenames(self) -> None:
        (self.root / "Audio" / "chrono").mkdir()
        (self.root / "Audio" / "paradox").mkdir()
        (self.root / "Audio" / "chrono" / "paradox_select_01.mp3").write_bytes(b"chrono")
        (self.root / "Audio" / "paradox" / "paradox_select_01.mp3").write_bytes(b"paradox")
        (self.root / "all_voicelines.json").write_text(
            json.dumps({
                "hero": {
                    "lines": [
                        {"filename": "line_01.mp3"},
                        {"filename": "chrono/paradox_select_01.mp3"},
                        {"filename": "paradox/paradox_select_01.mp3"},
                    ]
                }
            }),
            encoding="utf-8",
        )

        report = validate_version_source(self.root)
        self.assertTrue(report.valid, report.errors)
        self.assertEqual(report.audio_file_count, 3)
        self.assertEqual(report.referenced_audio_count, 3)
        paths = {item.relative_path for item in report.files}
        self.assertIn("audio/chrono/paradox_select_01.mp3", paths)
        self.assertIn("audio/paradox/paradox_select_01.mp3", paths)

        (self.root / "Audio" / "paradox" / "paradox_select_01.mp3").unlink()
        report = validate_version_source(self.root)
        self.assertFalse(report.valid)
        self.assertTrue(any(
            "paradox/paradox_select_01.mp3" in error for error in report.errors
        ))

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
        self.assertNotIn("categoriesUrl", entry)

    def test_character_routes_include_speakers_targets_and_conversations(self) -> None:
        characters = collect_content_characters(
            {
                "conversations": [{
                    "speakers": ["Abrams", "Paradox"],
                    "lines": [{"speaker": "PARADOX"}],
                }]
            },
            {
                "abrams": {"Self": {}, "Butcher": {}},
                "Haze": {"self": {}},
            },
        )
        self.assertEqual(characters, ["abrams", "Butcher", "Haze", "Paradox"])

    def test_game_character_routes_union_all_versions_in_catalog_order(self) -> None:
        payload = game_characters_payload(
            "deadlock",
            {
                "new": ["Abrams", "Haze"],
                "hidden-old": ["butcher", "ABRAMS"],
                "removed": ["not-published"],
            },
            ["new", "hidden-old"],
        )
        self.assertEqual(payload["characters"], ["Abrams", "butcher", "Haze"])
        self.assertEqual(list(payload["versions"]), ["new", "hidden-old"])
        self.assertNotIn("removed", payload["versions"])

    def test_optional_version_categories_are_validated_and_advertised(self) -> None:
        (self.root / "categories.json").write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "defaultCategory": "Characters",
                    "categories": [
                        {"name": "Characters", "characters": []},
                        {"name": "NPCs", "characters": ["shopkeeper"]},
                    ],
                }
            ),
            encoding="utf-8",
        )
        report = validate_version_source(self.root)
        self.assertTrue(report.valid, report.errors)
        self.assertIn("categories.json", {item.relative_path for item in report.files})
        entry = version_manifest_entry(self.settings, content_revision=2, has_categories=True)
        self.assertTrue(entry["categoriesUrl"].endswith("/categories.json"))

    def test_optional_version_character_names_are_validated_and_advertised(self) -> None:
        (self.root / "character-names-overlay.json").write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "game": "deadlock",
                    "names": {
                        "patron_female": "The Sapphire Flame",
                        "patron_male": "The Amber Hand",
                    },
                }
            ),
            encoding="utf-8",
        )
        report = validate_version_source(self.root)
        self.assertTrue(report.valid, report.errors)
        self.assertIn("character-names.json", {item.relative_path for item in report.files})
        entry = version_manifest_entry(
            self.settings,
            content_revision=2,
            has_character_names=True,
        )
        self.assertTrue(entry["characterNamesUrl"].endswith("/character-names.json"))

    def test_invalid_version_character_names_are_rejected(self) -> None:
        (self.root / "character-names-overlay.json").write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "game": "other-game",
                    "names": {"patron_female": "The Sapphire Flame"},
                }
            ),
            encoding="utf-8",
        )
        report = validate_version_source(self.root, "deadlock")
        self.assertFalse(report.valid)
        self.assertTrue(any("game must be 'deadlock'" in error for error in report.errors))

    def test_duplicate_character_category_assignment_is_rejected(self) -> None:
        (self.root / "categories.json").write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "defaultCategory": "Characters",
                    "categories": [
                        {"name": "Characters", "characters": ["hero"]},
                        {"name": "Other", "characters": ["HERO"]},
                    ],
                }
            ),
            encoding="utf-8",
        )
        report = validate_version_source(self.root)
        self.assertFalse(report.valid)
        self.assertTrue(any("assigns character more than once" in error for error in report.errors))

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

    def test_version_without_categories_keeps_per_game_default(self) -> None:
        settings = PublisherSettings(
            source_dir=self.root,
            game="deadlock",
            version="deadlock-test",
            label="Test version",
            bucket="test-bucket",
            endpoint_url="https://example.r2.cloudflarestorage.com",
        )
        client = FakeR2Client()
        client.objects["deadlock/manifest.json"] = json.dumps(
            {
                "schemaVersion": 1,
                "game": "deadlock",
                "latestVersion": "deadlock-test",
                "defaultCategoriesUrl": "https://cdn.vlviewer.com/deadlock/categories.json",
                "versions": [{"id": "deadlock-test", "label": "Old", "hidden": False}],
            }
        ).encode("utf-8")
        publisher = R2Publisher(settings)
        publisher._client = client
        manifest, entry = publisher._build_game_manifest(content_revision=2)
        self.assertEqual(
            manifest["defaultCategoriesUrl"],
            "https://cdn.vlviewer.com/deadlock/categories.json",
        )
        self.assertNotIn("categoriesUrl", entry)

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
        for version_id, hero in (
            ("deadlock-old", "old hero"),
            ("deadlock-new", "new hero"),
        ):
            prefix = f"deadlock/versions/{version_id}"
            client.objects[f"{prefix}/conversations.json"] = json.dumps(
                {"conversations": []}
            ).encode("utf-8")
            client.objects[f"{prefix}/voicelines.json"] = json.dumps(
                {hero: {"Self": {}}}
            ).encode("utf-8")
        saved = publisher.save_game_manifest(manifest)
        self.assertEqual(saved["latestVersion"], "deadlock-old")
        self.assertEqual(saved["versions"][0]["id"], "deadlock-old")
        self.assertEqual(
            saved["charactersUrl"],
            "https://cdn.vlviewer.com/deadlock/characters.json",
        )
        characters = json.loads(client.objects["deadlock/characters.json"])
        self.assertEqual(characters["characters"], ["new hero", "old hero"])
        self.assertEqual(client.events[-1], ("put", "deadlock/manifest.json"))
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
        characters = json.loads(client.objects["deadlock/characters.json"])
        self.assertEqual(characters["characters"], ["hero"])
        manifest = json.loads(client.objects["deadlock/manifest.json"])
        self.assertEqual(
            manifest["charactersUrl"],
            "https://cdn.vlviewer.com/deadlock/characters.json",
        )
        self.assertEqual(
            manifest["characterNamesUrl"],
            "https://cdn.vlviewer.com/deadlock/character-names.json",
        )
        character_names = json.loads(client.objects["deadlock/character-names.json"])
        self.assertEqual(character_names["names"]["internal_hero"], "Hero")
        self.assertEqual(result["contentRevision"], 1)

    def test_publish_can_set_per_game_default_categories(self) -> None:
        categories = {
            "schemaVersion": 1,
            "defaultCategory": "Characters",
            "categories": [{"name": "Characters", "characters": []}],
        }
        (self.root / "categories.json").write_text(json.dumps(categories), encoding="utf-8")
        settings = PublisherSettings(
            source_dir=self.root,
            game="deadlock",
            version="deadlock-test",
            label="Test version",
            bucket="test-bucket",
            endpoint_url="https://example.r2.cloudflarestorage.com",
        )
        client = FakeR2Client()
        client.objects["deadlock/manifest.json"] = json.dumps(
            {
                "schemaVersion": 1,
                "game": "deadlock",
                "latestVersion": "existing",
                "versions": [{"id": "existing", "label": "Existing", "hidden": False}],
            }
        ).encode("utf-8")
        publisher = R2Publisher(settings)
        publisher._client = client
        manifest = publisher.publish_game_default_categories()
        self.assertEqual(json.loads(client.objects["deadlock/categories.json"]), categories)
        self.assertEqual(
            manifest["defaultCategoriesUrl"],
            "https://cdn.vlviewer.com/deadlock/categories.json",
        )
        self.assertEqual(manifest["latestVersion"], "existing")
        self.assertEqual([item["id"] for item in manifest["versions"]], ["existing"])
        self.assertEqual(client.events[-1], ("put", "deadlock/manifest.json"))

    def test_publish_can_update_only_version_character_names(self) -> None:
        overlay = {
            "schemaVersion": 1,
            "game": "deadlock",
            "names": {
                "patron_female": "The Sapphire Flame",
                "patron_male": "The Amber Hand",
            },
        }
        (self.root / "character-names-overlay.json").write_text(
            json.dumps(overlay),
            encoding="utf-8",
        )
        settings = PublisherSettings(
            source_dir=self.root,
            game="deadlock",
            version="deadlock-test",
            label="Test version",
            bucket="test-bucket",
            endpoint_url="https://example.r2.cloudflarestorage.com",
            promote_to_latest=False,
        )
        client = FakeR2Client()
        prefix = "deadlock/versions/deadlock-test"
        client.objects["deadlock/manifest.json"] = json.dumps(
            {
                "schemaVersion": 1,
                "game": "deadlock",
                "latestVersion": "deadlock-test",
                "versions": [
                    {
                        "id": "deadlock-test",
                        "label": "Test version",
                        "hidden": False,
                        "contentRevision": 2,
                    }
                ],
            }
        ).encode("utf-8")
        client.objects[f"{prefix}/publish-inventory.json"] = json.dumps(
            {
                "schemaVersion": 2,
                "contentRevision": 2,
                "files": {
                    "categories.json": {
                        "size": 20,
                        "sha256": "old",
                        "contentType": "application/json; charset=utf-8",
                        "mutable": True,
                    }
                },
            }
        ).encode("utf-8")
        client.objects[f"{prefix}/release.json"] = json.dumps(
            {
                "schemaVersion": 1,
                "id": "deadlock-test",
                "contentRevision": 2,
                "fileCount": 1,
                "totalBytes": 20,
            }
        ).encode("utf-8")

        publisher = R2Publisher(settings)
        publisher._client = client
        manifest = publisher.publish_version_character_names()

        url = "https://cdn.vlviewer.com/deadlock/versions/deadlock-test/character-names.json"
        self.assertEqual(json.loads(client.objects[f"{prefix}/character-names.json"]), overlay)
        self.assertEqual(manifest["versions"][0]["characterNamesUrl"], url)
        self.assertEqual(manifest["versions"][0]["contentRevision"], 3)
        inventory = json.loads(client.objects[f"{prefix}/publish-inventory.json"])
        self.assertIn("character-names.json", inventory["files"])
        self.assertEqual(inventory["contentRevision"], 3)
        release = json.loads(client.objects[f"{prefix}/release.json"])
        self.assertEqual(release["characterNamesUrl"], url)
        self.assertEqual(client.events[-1], ("put", "deadlock/manifest.json"))

    def test_publish_can_use_explicit_game_categories_path(self) -> None:
        version_categories = {
            "schemaVersion": 1,
            "defaultCategory": "Characters",
            "categories": [{"name": "Characters", "characters": ["version-only"]}],
        }
        game_categories = {
            "schemaVersion": 1,
            "defaultCategory": "Characters",
            "categories": [{"name": "Characters", "characters": ["global"]}],
        }
        (self.root / "categories.json").write_text(
            json.dumps(version_categories), encoding="utf-8"
        )
        game_path = self.root / "game-categories.json"
        game_path.write_text(json.dumps(game_categories), encoding="utf-8")
        settings = PublisherSettings(
            source_dir=self.root,
            game="deadlock",
            version="deadlock-test",
            label="Test version",
            bucket="test-bucket",
            endpoint_url="https://example.r2.cloudflarestorage.com",
        )
        client = FakeR2Client()
        publisher = R2Publisher(settings)
        publisher._client = client

        publisher.publish_game_default_categories(game_path)

        self.assertEqual(
            json.loads(client.objects["deadlock/categories.json"]), game_categories
        )

    def test_clear_game_content_preserves_other_game_namespaces(self) -> None:
        settings = PublisherSettings(
            source_dir=self.root,
            game="deadlock",
            version="deadlock-test",
            label="Test version",
            bucket="test-bucket",
            endpoint_url="https://example.r2.cloudflarestorage.com",
        )
        client = FakeR2Client()
        client.objects.update({
            "deadlock/manifest.json": b"deadlock-manifest",
            "deadlock/versions/base/voicelines.json": b"deadlock-version",
            "overwatch/manifest.json": b"overwatch-manifest",
        })
        publisher = R2Publisher(settings)
        publisher._client = client

        result = publisher.clear_game_content()

        self.assertEqual(result["deleted"], 2)
        self.assertNotIn("deadlock/manifest.json", client.objects)
        self.assertNotIn("deadlock/versions/base/voicelines.json", client.objects)
        self.assertIn("overwatch/manifest.json", client.objects)

    def test_publish_can_update_game_character_names_without_a_version(self) -> None:
        settings = PublisherSettings(
            source_dir=self.root,
            game="deadlock",
            version="deadlock-test",
            label="Test version",
            bucket="test-bucket",
            endpoint_url="https://example.r2.cloudflarestorage.com",
        )
        client = FakeR2Client()
        client.objects["deadlock/manifest.json"] = json.dumps({
            "schemaVersion": 1,
            "game": "deadlock",
            "latestVersion": "existing",
            "versions": [{"id": "existing", "label": "Existing", "hidden": False}],
        }).encode("utf-8")
        publisher = R2Publisher(settings)
        publisher._client = client

        manifest = publisher.publish_game_character_names()

        self.assertEqual(
            manifest["characterNamesUrl"],
            "https://cdn.vlviewer.com/deadlock/character-names.json",
        )
        stored = json.loads(client.objects["deadlock/character-names.json"])
        self.assertEqual(stored["names"]["hero"], "Hero")
        self.assertEqual(client.events[-1], ("put", "deadlock/manifest.json"))


if __name__ == "__main__":
    unittest.main()
