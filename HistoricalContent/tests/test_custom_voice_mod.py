from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from historical_content.custom_voice_mod import (
    CustomVoiceModError,
    CustomVoiceModSettings,
    _public_repository_url,
    build_custom_voice_mod,
    discover_transcript_provenance,
    parse_vdf_tokens,
)
from historical_content.extraction.source2viewer import VpkVoiceAudioResult


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


class CustomVoiceModTests(unittest.TestCase):
    @staticmethod
    def _records(value: object):
        if isinstance(value, dict):
            if isinstance(value.get("filename"), str):
                yield value
                return
            for child in value.values():
                yield from CustomVoiceModTests._records(child)
        elif isinstance(value, list):
            for child in value:
                yield from CustomVoiceModTests._records(child)

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name)
        self.base = self.data / "generated" / "ognb"
        write_json(self.base / "all_voicelines.json", {
            "hero": {
                "Self": {
                    "Test": [{
                        "filename": "hero/line.mp3",
                        "voiceline_id": "line",
                        "transcription": "English",
                        "officialtranscription": True,
                        "audioKey": "sha256/aa/" + "a" * 64 + ".mp3",
                        "status": "ADDED",
                        "versionStatus": {"change": "new"},
                    }],
                    "Text only": [{
                        "filename": "",
                        "voiceline_id": "text_only_phantom",
                        "transcription": "No audio exists for this record.",
                        "is_phantom": True,
                    }],
                },
            },
        })
        write_json(self.base / "all_conversations.json", {
            "total_conversations": 1,
            "conversations": [{
                "conversation_id": "hero-test",
                "character1": "hero",
                "character2": "other",
                "speakers": ["hero"],
                "lines": [{
                    "part": 1,
                    "speaker": "hero",
                    "filename": "hero/line.mp3",
                    "voiceline_id": "line",
                    "transcription": "English",
                    "audioKey": "sha256/aa/" + "a" * 64 + ".mp3",
                }],
            }],
        })
        write_json(self.base / "coverage.json", {"summary": {}})
        write_json(self.base / "Localization" / "manifest.json", {
            "languages": [{"language": "russian", "output_file": "russian.json", "entry_count": 1}],
        })
        write_json(self.base / "Localization" / "russian.json", {
            "lines": {"line": "Official Russian"},
        })
        game_root = self.data / "preview-content" / "deadlock"
        (game_root / "versions" / "preview-ognb").mkdir(parents=True)
        write_json(game_root / "manifest.json", {
            "schemaVersion": 1,
            "game": "deadlock",
            "latestVersion": "preview-ognb",
            "versions": [{
                "id": "preview-ognb",
                "label": "Preview: OGNB",
                "hidden": False,
                "conversationUrl": "http://127.0.0.1:8787/deadlock/versions/preview-ognb/conversations.json",
                "voiceLineUrl": "http://127.0.0.1:8787/deadlock/versions/preview-ognb/voicelines.json",
                "audioBaseUrl": "http://127.0.0.1:8787/deadlock/versions/preview-ognb/audio/",
            }],
        })
        self.mod_audio = self.data / "mod-audio"
        (self.mod_audio / "hero").mkdir(parents=True)
        (self.mod_audio / "hero" / "line.mp3").write_bytes(b"mod audio")
        (self.mod_audio / "hero" / "line.vsnd").write_bytes(b"Source2Viewer sidecar")
        self.source2viewer = self.data / "Source2Viewer-CLI.exe"
        self.source2viewer.write_bytes(b"fixture executable")
        self.mod_vpk = self.data / "russian_voice_dir.vpk"
        self.mod_vpk.write_bytes(b"fixture vpk")
        self.transcript = self.data / "citadel_vo_russian.vdf"
        self.transcript_metadata = self.data / "metadata.json"
        write_json(self.transcript_metadata, {
            "language": "russian_fan_ognb",
            "version": "ognb",
            "base_language": "russian",
            "credits": [{"name": "Community translator", "link": "https://example.test"}],
        })

    def tearDown(self) -> None:
        self.temp.cleanup()

    def settings(self, version: str) -> CustomVoiceModSettings:
        return CustomVoiceModSettings(
            data_dir=self.data,
            game="deadlock",
            version_id=version,
            label="Russian Voice Mod",
            based_on_version="ognb",
            source2viewer_binary=self.source2viewer,
            mod_vpk_path=self.mod_vpk,
            transcript_path=self.transcript,
            transcript_metadata_path=self.transcript_metadata,
            transcript_repository="https://example.com/transcripts",
            transcript_revision="abc123",
            transcript_source_path="localizations/citadel_vo_russian.vdf",
            expected_transcript_sha256=hashlib.sha256(self.transcript.read_bytes()).hexdigest(),
        )

    def build(self, settings: CustomVoiceModSettings, progress=lambda _message: None):
        extraction = VpkVoiceAudioResult(
            audio_dir=self.mod_audio,
            workspace=settings.extraction_workspace,
            state_path=settings.extraction_workspace / "vpk-audio-state.json",
            audio_count=1,
            vpk_fingerprint={"size": self.mod_vpk.stat().st_size, "sampleSha256": "abc"},
            reused=False,
        )
        with patch(
            "historical_content.custom_voice_mod.extract_vpk_voice_audio",
            return_value=extraction,
        ) as extract:
            result = build_custom_voice_mod(settings, progress=progress)
        extract.assert_called_once_with(
            source2viewer_binary=settings.source2viewer_binary,
            vpk_path=self.mod_vpk.resolve(),
            workspace=settings.extraction_workspace,
            extraction_threads=settings.extraction_threads,
            force_reextract=settings.force_reextract,
            progress=progress,
        )
        return result

    def test_embeds_pinned_text_and_uses_only_version_audio(self) -> None:
        self.transcript.write_text(
            '"lang"\n{\n"Tokens"\n{\n"line" "Закрепленный текст"\n}\n}\n',
            encoding="utf-8",
        )
        result = self.build(self.settings("ognb-russian-mod"))

        self.assertTrue(result.publishable)
        output = result.output_dir
        payload = json.loads((output / "all_voicelines.json").read_text(encoding="utf-8"))
        line = payload["hero"]["Self"]["Test"][0]
        self.assertEqual(line["transcription"], "Закрепленный текст")
        self.assertFalse(line["officialtranscription"])
        self.assertNotIn("audioKey", line)
        self.assertEqual(line["versionStatus"], {})
        self.assertNotIn("status", line)
        self.assertTrue((output / "Audio" / "hero" / "line.mp3").is_file())
        self.assertFalse((output / "SharedAudio").exists())
        report = json.loads((output / "custom-import-report.json").read_text(encoding="utf-8"))
        self.assertFalse(report["speechToTextUsed"])
        self.assertEqual(report["audioSource"]["type"], "vpk")
        self.assertEqual(report["audioSource"]["filename"], self.mod_vpk.name)
        self.assertEqual(report["warningCount"], 0)
        self.assertEqual(report["audioFileCount"], 1)
        self.assertEqual(report["ignoredExtractionSidecarCount"], 1)
        custom = json.loads((output / "custom-version.json").read_text(encoding="utf-8"))
        self.assertEqual(custom["defaultLocalizationLanguage"], "russian")
        self.assertEqual(custom["transcriptMode"], "embedded")
        self.assertEqual(
            custom["transcriptSource"]["attribution"]["credits"][0]["name"],
            "Community translator",
        )
        self.assertEqual(custom["transcriptSource"]["matchCounts"]["exact"], 2)

        manifest = json.loads(
            (self.data / "preview-content" / "deadlock" / "manifest.json").read_text(encoding="utf-8")
        )
        entry = next(item for item in manifest["versions"] if item["id"] == "preview-ognb-russian-mod")
        self.assertEqual(entry["kind"], "custom")
        self.assertEqual(entry["basedOnVersion"], "preview-ognb")
        self.assertTrue(entry["hidden"])
        self.assertEqual(manifest["latestVersion"], "preview-ognb")

    def test_duplicate_vdf_tokens_use_the_top_entry(self) -> None:
        self.transcript.write_text(
            '"line" "Верхний текст"\n"line" "Нижний текст"\n',
            encoding="utf-8",
        )

        tokens = parse_vdf_tokens(self.transcript)

        self.assertEqual(tokens["line"], "Верхний текст")

    def test_public_repository_url_strips_https_credentials(self) -> None:
        self.assertEqual(
            _public_repository_url(
                "https://user:secret-token@github.com/example/transcripts.git?token=leak"
            ),
            "https://github.com/example/transcripts",
        )
        with self.assertRaisesRegex(CustomVoiceModError, "local host"):
            _public_repository_url("https://user:secret@localhost/transcripts.git")

    def test_explicit_repository_credentials_are_not_written_to_release_metadata(self) -> None:
        self.transcript.write_text('"line" "Текст"\n', encoding="utf-8")
        settings = self.settings("ognb-russian-sanitized-origin")
        settings = CustomVoiceModSettings(
            **{
                **settings.__dict__,
                "transcript_repository": (
                    "https://user:secret-token@github.com/example/transcripts.git?token=leak"
                ),
            }
        )

        result = self.build(settings)

        metadata = json.loads(
            (result.output_dir / "custom-version.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            metadata["transcriptSource"]["repository"],
            "https://github.com/example/transcripts",
        )

    def test_missing_transcript_is_blank_and_warns_without_blocking_publication(self) -> None:
        self.transcript.write_text(
            '"lang"\n{\n"Tokens"\n{\n"different_line" "Другой текст"\n}\n}\n',
            encoding="utf-8",
        )
        messages: list[str] = []
        result = self.build(self.settings("ognb-russian-warning"), progress=messages.append)

        self.assertTrue(result.publishable)
        self.assertTrue(any("WARNING:" in message for message in messages))
        report = json.loads(
            (result.output_dir / "custom-import-report.json").read_text(encoding="utf-8")
        )
        self.assertFalse(report["speechToTextUsed"])
        self.assertGreater(report["warningCount"], 0)
        self.assertTrue(report["publishable"])
        self.assertEqual(report["blockingWarningCount"], 0)
        payload = json.loads(
            (result.output_dir / "all_voicelines.json").read_text(encoding="utf-8")
        )
        line = payload["hero"]["Self"]["Test"][0]
        self.assertEqual(line["transcription"], "")
        self.assertFalse(line["officialtranscription"])
        conversations = json.loads(
            (result.output_dir / "all_conversations.json").read_text(encoding="utf-8")
        )
        self.assertEqual(conversations["conversations"][0]["lines"][0]["transcription"], "")
        coverage = json.loads(
            (result.output_dir / "coverage.json").read_text(encoding="utf-8")
        )
        self.assertEqual(coverage["summary"]["unmatched_files"], 0)
        self.assertEqual(coverage["unmatched_by_folder"], {})
        self.assertTrue((result.output_dir / "Audio" / "hero" / "line.mp3").is_file())

    def test_ambiguous_transcript_uses_first_candidate_and_warns(self) -> None:
        for filename in ("all_voicelines.json", "all_conversations.json"):
            path = self.base / filename
            payload = json.loads(path.read_text(encoding="utf-8"))
            for record in self._records(payload):
                if record.get("filename") == "hero/line.mp3":
                    record["voiceline_id"] = "preferred_line"
            write_json(path, payload)
        self.transcript.write_text(
            '"preferred_line" "Первый текст"\n"line" "Второй текст"\n',
            encoding="utf-8",
        )

        result = self.build(self.settings("ognb-russian-ambiguous"))

        self.assertTrue(result.publishable)
        self.assertTrue(any(
            warning["stage"] == "transcript-ambiguous-first"
            for warning in result.warnings
        ))
        payload = json.loads(
            (result.output_dir / "all_voicelines.json").read_text(encoding="utf-8")
        )
        self.assertEqual(payload["hero"]["Self"]["Test"][0]["transcription"], "Первый текст")

    def test_repeated_base_filename_stem_is_repaired_with_first_match(self) -> None:
        for filename in ("all_voicelines.json", "all_conversations.json"):
            path = self.base / filename
            payload = json.loads(path.read_text(encoding="utf-8"))
            for record in self._records(payload):
                if record.get("filename") == "hero/line.mp3":
                    record["filename"] = "hero/lineline.mp3"
            write_json(path, payload)
        self.transcript.write_text('"line" "Закрепленный текст"\n', encoding="utf-8")

        result = self.build(self.settings("ognb-russian-repeated-stem"))

        self.assertTrue(result.publishable)
        self.assertTrue(any(
            warning["stage"] == "audio-to-base-repaired"
            for warning in result.warnings
        ))
        payload = json.loads(
            (result.output_dir / "all_voicelines.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            payload["hero"]["Self"]["Test"][0]["transcription"],
            "Закрепленный текст",
        )
        self.assertTrue((result.output_dir / "Audio" / "hero" / "lineline.mp3").is_file())

    def test_filtered_conversation_recomputes_completeness(self) -> None:
        path = self.base / "all_conversations.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        conversation = payload["conversations"][0]
        conversation["is_complete"] = True
        conversation["missing_parts"] = []
        conversation["speakers"] = ["hero", "other"]
        conversation["lines"].append({
            "part": 2,
            "speaker": "other",
            "filename": "other/missing.mp3",
            "voiceline_id": "missing",
            "transcription": "Missing mod audio",
        })
        write_json(path, payload)
        self.transcript.write_text('"line" "Закрепленный текст"\n', encoding="utf-8")

        result = self.build(self.settings("ognb-russian-partial-conversation"))

        output = json.loads(
            (result.output_dir / "all_conversations.json").read_text(encoding="utf-8")
        )["conversations"][0]
        self.assertFalse(output["is_complete"])
        self.assertEqual(output["missing_parts"], [2])
        self.assertEqual(output["speakers"], ["hero", "other"])

    def test_all_unmatched_audio_is_a_fatal_import_error(self) -> None:
        (self.mod_audio / "hero" / "line.mp3").unlink()
        (self.mod_audio / "hero" / "unknown.mp3").write_bytes(b"unknown audio")
        self.transcript.write_text('"line" "Закрепленный текст"\n', encoding="utf-8")

        with self.assertRaisesRegex(CustomVoiceModError, "No mod MP3 recording"):
            self.build(self.settings("ognb-russian-no-matches"))

    def test_orphaned_vsnd_warns_when_source2viewer_did_not_decode_an_mp3(self) -> None:
        self.transcript.write_text('"line" "Закрепленный текст"\n', encoding="utf-8")
        (self.mod_audio / "hero" / "orphan.vsnd").write_bytes(b"missing decoded audio")

        result = self.build(self.settings("ognb-russian-orphan"))

        self.assertTrue(result.publishable)
        orphan = next(
            warning for warning in result.warnings
            if warning["audioPath"] == "hero/orphan.vsnd"
        )
        self.assertEqual(orphan["stage"], "audio-format")
        self.assertIn("companion MP3", orphan["reason"])

    def test_reimport_replaces_existing_local_custom_source_and_preview(self) -> None:
        self.transcript.write_text('"line" "Первый текст"\n', encoding="utf-8")
        first = self.build(self.settings("ognb-russian-retry"))
        self.assertTrue(first.publishable)

        self.transcript.write_text('"line" "Обновленный текст"\n', encoding="utf-8")
        (self.mod_audio / "hero" / "line.mp3").write_bytes(b"updated mod audio")
        second = self.build(self.settings("ognb-russian-retry"))

        self.assertTrue(second.publishable)
        source_line = json.loads(
            (second.output_dir / "all_voicelines.json").read_text(encoding="utf-8")
        )["hero"]["Self"]["Test"][0]
        self.assertEqual(source_line["transcription"], "Обновленный текст")
        self.assertEqual(
            (second.output_dir / "Audio" / "hero" / "line.mp3").read_bytes(),
            b"updated mod audio",
        )
        preview = (
            self.data
            / "preview-content"
            / "deadlock"
            / "versions"
            / "preview-ognb-russian-retry"
        )
        preview_line = json.loads(
            (preview / "voicelines.json").read_text(encoding="utf-8")
        )["hero"]["Self"]["Test"][0]
        self.assertEqual(preview_line["transcription"], "Обновленный текст")
        self.assertFalse(
            second.output_dir.with_name(
                second.output_dir.name + ".custom-import.backup"
            ).exists()
        )

    def test_reimport_keeps_recovery_backups_until_downstream_updates_finish(self) -> None:
        self.transcript.write_text('"line" "Первый текст"\n', encoding="utf-8")
        first = self.build(self.settings("ognb-russian-transaction"))
        self.transcript.write_text('"line" "Обновленный текст"\n', encoding="utf-8")

        with patch(
            "historical_content.custom_voice_mod.register_local_version",
            side_effect=RuntimeError("catalog failed"),
        ):
            with self.assertRaisesRegex(RuntimeError, "catalog failed"):
                self.build(self.settings("ognb-russian-transaction"))

        output_backup = first.output_dir.with_name(
            first.output_dir.name + ".custom-import.backup"
        )
        preview = (
            self.data
            / "preview-content"
            / "deadlock"
            / "versions"
            / "preview-ognb-russian-transaction"
        )
        preview_backup = preview.with_name(preview.name + ".custom-import.backup")
        self.assertTrue(output_backup.is_dir())
        self.assertTrue(preview_backup.is_dir())
        old_source = json.loads(
            (output_backup / "all_voicelines.json").read_text(encoding="utf-8")
        )
        old_preview = json.loads(
            (preview_backup / "voicelines.json").read_text(encoding="utf-8")
        )
        self.assertEqual(old_source["hero"]["Self"]["Test"][0]["transcription"], "Первый текст")
        self.assertEqual(old_preview["hero"]["Self"]["Test"][0]["transcription"], "Первый текст")

    def test_reimport_refuses_to_replace_a_non_custom_generated_directory(self) -> None:
        self.transcript.write_text('"line" "Текст"\n', encoding="utf-8")
        collision = self.data / "generated" / "official-collision"
        write_json(collision / "custom-version.json", {"kind": "official"})

        with self.assertRaisesRegex(CustomVoiceModError, "not a generated custom version"):
            build_custom_voice_mod(
                CustomVoiceModSettings(
                    **{
                        **self.settings("official-collision").__dict__,
                        "version_id": "official-collision",
                    }
                ),
                progress=lambda _message: None,
            )

    def test_rejects_an_uncataloged_base_before_vpk_extraction(self) -> None:
        self.transcript.write_text('"line" "Текст"\n', encoding="utf-8")
        shutil.copytree(self.base, self.data / "generated" / "uncataloged")
        settings = CustomVoiceModSettings(
            **{
                **self.settings("uncataloged-custom").__dict__,
                "based_on_version": "uncataloged",
            }
        )

        with patch(
            "historical_content.custom_voice_mod.extract_vpk_voice_audio"
        ) as extract:
            with self.assertRaisesRegex(CustomVoiceModError, "not cataloged locally"):
                build_custom_voice_mod(settings, progress=lambda _message: None)

        extract.assert_not_called()

    def test_rejects_a_custom_base_before_vpk_extraction(self) -> None:
        self.transcript.write_text('"line" "Текст"\n', encoding="utf-8")
        shutil.copytree(self.base, self.data / "generated" / "custom-base")
        write_json(self.data / "catalogs" / "deadlock.json", {
            "latestVersion": "ognb",
            "versions": [
                {"id": "ognb", "label": "OGNB", "kind": "official"},
                {"id": "custom-base", "label": "Custom", "kind": "custom"},
            ],
        })
        settings = CustomVoiceModSettings(
            **{
                **self.settings("nested-custom").__dict__,
                "based_on_version": "custom-base",
            }
        )

        with patch(
            "historical_content.custom_voice_mod.extract_vpk_voice_audio"
        ) as extract:
            with self.assertRaisesRegex(CustomVoiceModError, "based on official content"):
                build_custom_voice_mod(settings, progress=lambda _message: None)

        extract.assert_not_called()

    def test_rejects_unsafe_version_ids_before_creating_output(self) -> None:
        self.transcript.write_text('"line" "Текст"', encoding="utf-8")
        unsafe = self.settings("../outside")
        with self.assertRaisesRegex(CustomVoiceModError, "Invalid version_id"):
            build_custom_voice_mod(unsafe, progress=lambda _message: None)
        self.assertFalse((self.data / "outside").exists())

    def test_rejects_a_transcript_that_does_not_match_the_expected_pin(self) -> None:
        self.transcript.write_text('"line" "Текст"', encoding="utf-8")
        settings = self.settings("hash-mismatch")
        settings = CustomVoiceModSettings(
            **{
                **settings.__dict__,
                "expected_transcript_sha256": "0" * 64,
            }
        )
        with self.assertRaisesRegex(CustomVoiceModError, "SHA-256 mismatch"):
            build_custom_voice_mod(settings, progress=lambda _message: None)

    def test_discovers_clean_transcript_git_provenance_and_sibling_metadata(self) -> None:
        repository = self.data / "fan-localization"
        localization = repository / "localizations" / "russian_fan_ognb"
        localization.mkdir(parents=True)
        transcript = localization / "citadel_vo_russian.vdf"
        metadata = localization / "metadata.json"
        transcript.write_text('"line" "Текст"\n', encoding="utf-8")
        write_json(metadata, {
            "language": "russian_fan_ognb",
            "credits": [{"name": "Translator"}],
        })

        def git(*arguments: str) -> None:
            subprocess.run(
                ["git", "-C", str(repository), *arguments],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

        git("init")
        git("config", "user.email", "test@example.test")
        git("config", "user.name", "Test")
        git("config", "core.autocrlf", "false")
        git("remote", "add", "origin", "git@github.com:example/fan-localization.git")
        git("add", ".")
        git("commit", "-m", "fixture")

        provenance = discover_transcript_provenance(transcript)

        self.assertEqual(provenance.metadata_path, metadata)
        self.assertEqual(provenance.repository, "https://github.com/example/fan-localization")
        self.assertEqual(
            provenance.source_path,
            "localizations/russian_fan_ognb/citadel_vo_russian.vdf",
        )
        self.assertEqual(provenance.sha256, hashlib.sha256(transcript.read_bytes()).hexdigest())
        self.assertRegex(provenance.revision, r"^[0-9a-f]{40}$")

        transcript.write_text('"line" "Изменено"\n', encoding="utf-8")
        with self.assertRaisesRegex(CustomVoiceModError, "uncommitted changes"):
            discover_transcript_provenance(transcript)


if __name__ == "__main__":
    unittest.main()
