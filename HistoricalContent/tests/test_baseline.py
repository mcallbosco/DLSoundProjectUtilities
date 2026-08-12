from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from HistoricalContent.historical_content.baseline import (
    AudioIndex,
    BaselineError,
    BaselineSettings,
    build_transcription_prompt,
    create_baseline,
    load_json,
    refresh_preview_categories,
    validate_categories,
    write_json,
)


class BaselineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        audio = self.source / "Audio"
        audio.mkdir(parents=True)
        (audio / "abrams_test.mp3").write_bytes(b"fake-mp3-abrams")
        (audio / "paradox_match_start_abrams_paradox_convo01_01.mp3").write_bytes(b"fake-mp3-paradox")
        (audio / "unused.mp3").write_bytes(b"not-referenced")
        write_json(self.source / "all_voicelines.json", {
            "abrams": {
                "Self": {
                    "Test": [{
                        "filename": "abrams_test.mp3",
                        "voiceline_id": "abrams_test",
                        "transcription": "Baseline line.",
                    }]
                }
            }
        })
        write_json(self.source / "all_conversations.json", {
            "export_date": "2024-01-01T00:00:00Z",
            "total_conversations": 1,
            "conversations": [{
                "conversation_id": "abrams_paradox_convo01",
                "speakers": ["abrams", "paradox"],
                "lines": [{
                    "part": 1,
                    "variation": 1,
                    "speaker": "paradox",
                    "filename": "paradox_match_start_abrams_paradox_convo01_01.mp3",
                    "transcription": "Conversation line.",
                }],
            }],
        })
        self.repo = self.root / "transcripts"
        self.data = self.root / "data"
        self.vocabulary = self.root / "transcription-vocabulary.json"
        write_json(self.vocabulary, {
            "Characters": ["Abrams", "Paradox"],
            "Transcription Guidelines": ["Use standard punctuation."],
        })

    def tearDown(self):
        self.temp.cleanup()

    def settings(self):
        return BaselineSettings(
            source_dir=self.source,
            transcript_repo=self.repo,
            data_dir=self.data,
            transcription_vocabulary=self.vocabulary,
            transcribe_missing=False,
            initialize_git=False,
        )

    def transcript_path(self, filename: str) -> Path:
        return self.repo / "transcripts" / Path(f"{filename}.json")

    def transcript_revision(self, filename: str, index: int = 0):
        payload = load_json(self.transcript_path(filename))
        return payload["revisions"][index]

    def test_creates_transcripts_database_and_preview(self):
        name_images = self.source / "CharacterNameImages"
        (name_images / "english").mkdir(parents=True)
        (name_images / "english" / "abrams.hash.webp").write_bytes(b"webp")
        write_json(name_images / "manifest.json", {
            "schemaVersion": 1,
            "languages": {
                "english": {
                    "abrams": {
                        "path": "english/abrams.hash.webp",
                        "width": 640,
                        "height": 512,
                    }
                }
            },
        })
        result = create_baseline(self.settings(), progress=lambda message: None)
        voice = load_json(self.transcript_path("abrams_test.mp3"))
        self.assertEqual(voice["filename"], "abrams_test.mp3")
        self.assertEqual(voice["revisions"][0]["text"], "Baseline line.")
        conversation = load_json(
            self.transcript_path("paradox_match_start_abrams_paradox_convo01_01.mp3")
        )
        self.assertEqual(conversation["revisions"][0]["text"], "Conversation line.")
        self.assertFalse((self.repo / "voicelines").exists())
        self.assertFalse((self.repo / "conversations").exists())
        manifest = load_json(result.preview_root / "deadlock" / "manifest.json")
        self.assertEqual(manifest["latestVersion"], "preview-deadlock-base")
        self.assertEqual(
            manifest["charactersUrl"],
            "http://127.0.0.1:8787/deadlock/characters.json",
        )
        self.assertEqual(
            manifest["characterNamesUrl"],
            "http://127.0.0.1:8787/deadlock/character-names.json",
        )
        character_names = load_json(
            result.preview_root / "deadlock" / "character-names.json"
        )
        self.assertEqual(character_names["names"]["abrams"], "Abrams")
        self.assertTrue((result.publish_source / "character-names.json").is_file())
        self.assertTrue(
            (result.publish_source / "CharacterNameImages" / "english" / "abrams.hash.webp").is_file()
        )
        self.assertEqual(
            manifest["versions"][0]["characterNameImagesUrl"],
            "http://127.0.0.1:8787/deadlock/versions/"
            "preview-deadlock-base/character-name-images/manifest.json",
        )
        characters = load_json(result.preview_root / "deadlock" / "characters.json")
        self.assertEqual(characters["characters"], ["abrams", "paradox"])
        self.assertEqual(
            characters["versions"]["preview-deadlock-base"],
            ["abrams", "paradox"],
        )
        audio_hash = hashlib.sha256(b"fake-mp3-abrams").hexdigest()
        shared_key = Path("sha256") / audio_hash[:2] / f"{audio_hash}.mp3"
        public = load_json(
            result.preview_root / "deadlock" / "versions" /
            "preview-deadlock-base" / "voicelines.json"
        )
        self.assertEqual(
            public["abrams"]["Self"]["Test"][0]["audioKey"],
            shared_key.as_posix(),
        )
        self.assertTrue((result.preview_root / "deadlock" / "audio" / shared_key).is_file())
        self.assertTrue((result.publish_source / "SharedAudio" / shared_key).is_file())
        self.assertFalse((result.publish_source / "Audio").exists())
        self.assertEqual(
            manifest["sharedAudioBaseUrl"],
            "http://127.0.0.1:8787/deadlock/audio/",
        )
        with closing(sqlite3.connect(result.database_path)) as database:
            count = database.execute("SELECT COUNT(*) FROM version_assets").fetchone()[0]
        self.assertEqual(count, 2)

    def test_adds_cached_audio_duration_to_both_content_files(self):
        with patch.object(AudioIndex, "duration", return_value=1.234):
            result = create_baseline(self.settings(), progress=lambda message: None)

        version_root = (
            result.preview_root / "deadlock" / "versions" / "preview-deadlock-base"
        )
        voicelines = load_json(version_root / "voicelines.json")
        conversations = load_json(version_root / "conversations.json")
        self.assertEqual(
            voicelines["abrams"]["Self"]["Test"][0]["duration"],
            1.234,
        )
        self.assertEqual(
            conversations["conversations"][0]["lines"][0]["duration"],
            1.234,
        )

    def test_version_character_names_are_copied_to_preview_and_publisher(self):
        overlay = {
            "schemaVersion": 1,
            "game": "deadlock",
            "names": {
                "patron_female": "The Sapphire Flame",
                "patron_male": "The Amber Hand",
            },
        }
        write_json(
            self.repo / "config" / "deadlock" / "versions"
            / "deadlock-base" / "character-names.json",
            overlay,
        )

        result = create_baseline(self.settings(), progress=lambda message: None)
        manifest = load_json(result.preview_root / "deadlock" / "manifest.json")

        self.assertEqual(
            load_json(
                result.preview_root / "deadlock" / "versions"
                / "preview-deadlock-base" / "character-names.json"
            ),
            overlay,
        )
        self.assertEqual(
            load_json(result.publish_source / "character-names-overlay.json"),
            overlay,
        )
        self.assertEqual(
            manifest["versions"][0]["characterNamesUrl"],
            "http://127.0.0.1:8787/deadlock/versions/"
            "preview-deadlock-base/character-names.json",
        )

    def test_text_only_phantom_voiceline_is_preserved_without_transcript_file(self):
        payload = load_json(self.source / "all_voicelines.json")
        payload["abrams"]["Self"]["Test"].append({
            "filename": "",
            "is_phantom": True,
            "voiceline_id": "abrams_vdf_only_01_hero_3d",
            "transcription": "Official text without audio.",
            "officialtranscription": True,
        })
        write_json(self.source / "all_voicelines.json", payload)

        result = create_baseline(self.settings(), progress=lambda _message: None)

        self.assertEqual(result.voiceline_count, 1)
        transcript_files = list((self.repo / "transcripts").rglob("*.json"))
        self.assertEqual(len(transcript_files), 2)
        public = load_json(
            result.preview_root / "deadlock" / "versions" /
            "preview-deadlock-base" / "voicelines.json"
        )
        phantom = public["abrams"]["Self"]["Test"][1]
        self.assertEqual(phantom["filename"], "")
        self.assertTrue(phantom["is_phantom"])
        self.assertEqual(
            phantom["transcription"],
            "Official text without audio.",
        )
        self.assertTrue(phantom["officialtranscription"])
        self.assertEqual(phantom["versionStatus"], {})
        self.assertNotIn("audioKey", phantom)
        self.assertNotIn("duration", phantom)

    def test_text_only_phantom_conversation_line_is_preserved(self):
        payload = load_json(self.source / "all_conversations.json")
        payload["conversations"][0]["lines"].append({
            "part": 2,
            "variation": 1,
            "speaker": "abrams",
            "filename": "",
            "is_phantom": True,
            "transcription": "Official conversation text without audio.",
            "officialtranscription": True,
        })
        write_json(self.source / "all_conversations.json", payload)

        result = create_baseline(self.settings(), progress=lambda _message: None)

        self.assertEqual(result.conversation_line_count, 1)
        public = load_json(
            result.preview_root / "deadlock" / "versions" /
            "preview-deadlock-base" / "conversations.json"
        )
        phantom = public["conversations"][0]["lines"][1]
        self.assertEqual(phantom["filename"], "")
        self.assertTrue(phantom["is_phantom"])
        self.assertEqual(
            phantom["transcription"],
            "Official conversation text without audio.",
        )
        self.assertTrue(phantom["officialtranscription"])
        self.assertNotIn("audioKey", phantom)
        self.assertNotIn("duration", phantom)

    def test_empty_voiceline_filename_requires_phantom_marker(self):
        payload = load_json(self.source / "all_voicelines.json")
        payload["abrams"]["Self"]["Test"].append({
            "filename": "",
            "voiceline_id": "broken_line",
            "transcription": "Malformed line.",
        })
        write_json(self.source / "all_voicelines.json", payload)

        with self.assertRaisesRegex(
            BaselineError,
            "Voiceline 'broken_line' has no audio filename and is not marked",
        ):
            create_baseline(self.settings(), progress=lambda _message: None)

    def test_manual_transcript_survives_regeneration(self):
        create_baseline(self.settings(), progress=lambda message: None)
        path = self.transcript_path("abrams_test.mp3")
        payload = load_json(path)
        payload["revisions"][0]["text"] = "Corrected manually."
        payload["revisions"][0]["source"] = "manual"
        payload["revisions"][0].pop("model", None)
        write_json(path, payload)
        result = create_baseline(self.settings(), progress=lambda message: None)
        regenerated = load_json(path)
        self.assertEqual(regenerated["revisions"][0]["text"], "Corrected manually.")
        public = load_json(
            result.preview_root / "deadlock" / "versions" /
            "preview-deadlock-base" / "voicelines.json"
        )
        self.assertEqual(
            public["abrams"]["Self"]["Test"][0]["transcription"],
            "Corrected manually.",
        )

    def test_predefined_official_transcript_fills_missing_before_openai(self):
        payload = load_json(self.source / "all_voicelines.json")
        payload["abrams"]["Self"]["Test"][0]["transcription"] = ""
        write_json(self.source / "all_voicelines.json", payload)
        predefined = self.root / "predefined.csv"
        predefined.write_text(
            '"file_path","vo_root","file_basename","transcription",'
            '"localization_key","removed_localization_suffix","match_status"\n'
            '"sounds/vo/abrams/abrams_test.vsnd_c","abrams","abrams_test",'
            '"Official current line.","abrams_test_hero_3d","hero_3d",'
            '"single_match"\n',
            encoding="utf-8",
        )
        audio = self.source / "Audio"
        (audio / "abrams").mkdir()
        (audio / "abrams_test.mp3").replace(audio / "abrams" / "abrams_test.mp3")
        payload = load_json(self.source / "all_voicelines.json")
        payload["abrams"]["Self"]["Test"][0]["filename"] = "abrams/abrams_test.mp3"
        write_json(self.source / "all_voicelines.json", payload)
        settings = BaselineSettings(
            source_dir=self.source,
            transcript_repo=self.repo,
            data_dir=self.data,
            api_key="test-key",
            transcription_vocabulary=self.vocabulary,
            predefined_transcripts=predefined,
            transcribe_missing=True,
            initialize_git=False,
        )
        progress_messages = []

        with patch(
            "HistoricalContent.historical_content.baseline.transcribe_audio"
        ) as transcribe:
            result = create_baseline(settings, progress=progress_messages.append)

        transcribe.assert_not_called()
        revision = self.transcript_revision("abrams/abrams_test.mp3")
        self.assertEqual(revision["text"], "Official current line.")
        self.assertEqual(revision["source"], "official")
        self.assertNotIn("model", revision)
        public = load_json(
            result.preview_root / "deadlock" / "versions" /
            "preview-deadlock-base" / "voicelines.json"
        )
        line = public["abrams"]["Self"]["Test"][0]
        self.assertEqual(line["transcription"], "Official current line.")
        self.assertTrue(line["officialtranscription"])
        self.assertTrue(any(
            "accepted 1 safe official transcripts" in message
            for message in progress_messages
        ))
        self.assertTrue(any(
            "Applied 1 predefined official transcripts" in message
            for message in progress_messages
        ))

    def test_predefined_official_transcript_applies_to_conversation_line(self):
        conversations = load_json(self.source / "all_conversations.json")
        line = conversations["conversations"][0]["lines"][0]
        line["transcription"] = ""
        line["filename"] = (
            "paradox/paradox_match_start_abrams_paradox_convo01_01.mp3"
        )
        write_json(self.source / "all_conversations.json", conversations)
        audio = self.source / "Audio"
        (audio / "paradox").mkdir()
        original = audio / "paradox_match_start_abrams_paradox_convo01_01.mp3"
        original.replace(audio / "paradox" / original.name)
        predefined = self.root / "predefined.csv"
        predefined.write_text(
            '"file_path","vo_root","file_basename","transcription",'
            '"localization_key","removed_localization_suffix","match_status"\n'
            '"sounds/vo/paradox/'
            'paradox_match_start_abrams_paradox_convo01_01.vsnd_c",'
            '"paradox","paradox_match_start_abrams_paradox_convo01_01",'
            '"Official conversation line.","conversation_key","hero_3d",'
            '"single_match"\n',
            encoding="utf-8",
        )
        settings = self.settings()
        settings = replace(settings, predefined_transcripts=predefined)

        result = create_baseline(settings, progress=lambda _message: None)

        conversations = load_json(
            result.preview_root / "deadlock" / "versions" /
            "preview-deadlock-base" / "conversations.json"
        )
        line = conversations["conversations"][0]["lines"][0]
        self.assertEqual(line["transcription"], "Official conversation line.")
        self.assertTrue(line["officialtranscription"])

    def test_predefined_transcript_does_not_replace_existing_text(self):
        predefined = self.root / "predefined.csv"
        predefined.write_text(
            '"file_path","vo_root","file_basename","transcription",'
            '"localization_key","removed_localization_suffix","match_status"\n'
            '"sounds/vo/abrams/abrams_test.vsnd_c","abrams","abrams_test",'
            '"Replacement text.","key","hero_3d","single_match"\n',
            encoding="utf-8",
        )
        audio = self.source / "Audio"
        (audio / "abrams").mkdir()
        (audio / "abrams_test.mp3").replace(audio / "abrams" / "abrams_test.mp3")
        payload = load_json(self.source / "all_voicelines.json")
        payload["abrams"]["Self"]["Test"][0]["filename"] = "abrams/abrams_test.mp3"
        write_json(self.source / "all_voicelines.json", payload)
        settings = self.settings()
        settings = replace(settings, predefined_transcripts=predefined)

        create_baseline(settings, progress=lambda _message: None)

        revision = self.transcript_revision("abrams/abrams_test.mp3")
        self.assertEqual(revision["text"], "Baseline line.")
        self.assertEqual(revision["source"], "generated")

    def test_predefined_official_transcript_overrides_effort_skip(self):
        filename = "abrams/abrams_effort_special_01.mp3"
        audio_path = self.source / "Audio" / Path(filename)
        audio_path.parent.mkdir(parents=True)
        audio_path.write_bytes(b"spoken-effort-audio")
        payload = load_json(self.source / "all_voicelines.json")
        payload["abrams"]["Self"]["Test"].append({
            "filename": filename,
            "voiceline_id": "abrams_effort_special_01",
            "transcription": "",
        })
        write_json(self.source / "all_voicelines.json", payload)
        predefined = self.root / "predefined.csv"
        predefined.write_text(
            '"file_path","vo_root","file_basename","transcription",'
            '"localization_key","removed_localization_suffix","match_status"\n'
            '"sounds/vo/abrams/abrams_effort_special_01.vsnd_c","abrams",'
            '"abrams_effort_special_01","Push through!","key","hero_3d",'
            '"single_match"\n',
            encoding="utf-8",
        )
        settings = self.settings()
        settings = replace(settings, predefined_transcripts=predefined)

        create_baseline(settings, progress=lambda _message: None)

        revision = self.transcript_revision(filename)
        self.assertEqual(revision["text"], "Push through!")
        self.assertEqual(revision["source"], "official")

    def test_effort_recording_is_stored_but_not_transcribed(self):
        effort_filename = "abrams_effort_general_01.mp3"
        (self.source / "Audio" / effort_filename).write_bytes(b"effort-audio")
        payload = load_json(self.source / "all_voicelines.json")
        payload["abrams"]["Self"]["Test"].append({
            "filename": effort_filename,
            "voiceline_id": "abrams_effort_general_01",
            "transcription": "",
        })
        write_json(self.source / "all_voicelines.json", payload)
        settings = BaselineSettings(
            source_dir=self.source,
            transcript_repo=self.repo,
            data_dir=self.data,
            api_key="test-key",
            transcription_vocabulary=self.vocabulary,
            transcribe_missing=True,
            initialize_git=False,
        )

        progress_messages = []
        with patch(
            "HistoricalContent.historical_content.baseline.transcribe_audio"
        ) as transcribe:
            result = create_baseline(settings, progress=progress_messages.append)
        transcribe.assert_not_called()

        revision = self.transcript_revision(effort_filename)
        self.assertEqual(revision["text"], "")
        self.assertEqual(revision["source"], "skippedeffort")
        self.assertNotIn("model", revision)
        self.assertIn(
            "Skipped transcription for 1 effort recordings.",
            progress_messages,
        )
        public = load_json(
            result.preview_root / "deadlock" / "versions" /
            "preview-deadlock-base" / "voicelines.json"
        )
        effort = public["abrams"]["Self"]["Test"][1]
        self.assertEqual(effort["transcription"], "")

        # Migrate hallucinated output from runs made before effort skipping.
        document = load_json(self.transcript_path(effort_filename))
        document["revisions"][0].update({
            "text": "Hello.",
            "source": "generated",
            "model": "gpt-4o-mini-transcribe",
        })
        write_json(self.transcript_path(effort_filename), document)
        with patch(
            "HistoricalContent.historical_content.baseline.transcribe_audio"
        ) as transcribe:
            create_baseline(settings, progress=lambda _message: None)
        transcribe.assert_not_called()
        revision = self.transcript_revision(effort_filename)
        self.assertEqual(revision["text"], "")
        self.assertEqual(revision["source"], "skippedeffort")
        self.assertNotIn("model", revision)

        # Explicitly curated effort text remains available.
        document = load_json(self.transcript_path(effort_filename))
        document["revisions"][0].update({
            "text": "Hup!",
            "source": "manual",
        })
        write_json(self.transcript_path(effort_filename), document)
        create_baseline(settings, progress=lambda _message: None)
        revision = self.transcript_revision(effort_filename)
        self.assertEqual(revision["text"], "Hup!")
        self.assertEqual(revision["source"], "manual")

    def test_known_non_speech_recordings_are_stored_but_not_transcribed(self):
        filenames = (
            "abrams/emote/abrams_laugh_01.mp3",
            "book/sfx/door_close_01.mp3",
            "butcher/rr_test_21_pain_big_01.mp3",
        )
        payload = load_json(self.source / "all_voicelines.json")
        for index, filename in enumerate(filenames):
            audio_path = self.source / "Audio" / Path(filename)
            audio_path.parent.mkdir(parents=True, exist_ok=True)
            audio_path.write_bytes(f"non-speech-{index}".encode())
            payload["abrams"]["Self"]["Test"].append({
                "filename": filename,
                "voiceline_id": f"non_speech_{index}",
                "transcription": "",
            })
        write_json(self.source / "all_voicelines.json", payload)
        settings = BaselineSettings(
            source_dir=self.source,
            transcript_repo=self.repo,
            data_dir=self.data,
            api_key="test-key",
            transcription_vocabulary=self.vocabulary,
            transcribe_missing=True,
            initialize_git=False,
        )

        progress_messages = []
        with patch(
            "HistoricalContent.historical_content.baseline.transcribe_audio"
        ) as transcribe:
            create_baseline(settings, progress=progress_messages.append)
        transcribe.assert_not_called()

        for filename in filenames:
            revision = self.transcript_revision(filename)
            self.assertEqual(revision["text"], "")
            self.assertEqual(revision["source"], "skippednonspeech")
            self.assertNotIn("model", revision)
        self.assertIn(
            "Skipped transcription for 3 non-speech recordings.",
            progress_messages,
        )

    def test_blank_model_response_is_terminal_on_later_runs(self):
        payload = load_json(self.source / "all_voicelines.json")
        payload["abrams"]["Self"]["Test"][0]["transcription"] = ""
        write_json(self.source / "all_voicelines.json", payload)
        settings = BaselineSettings(
            source_dir=self.source,
            transcript_repo=self.repo,
            data_dir=self.data,
            api_key="test-key",
            transcription_vocabulary=self.vocabulary,
            transcribe_missing=True,
            initialize_git=False,
        )

        progress_messages = []
        with patch(
            "HistoricalContent.historical_content.baseline.transcribe_audio",
            return_value="",
        ) as transcribe:
            create_baseline(settings, progress=progress_messages.append)
        transcribe.assert_called_once()
        revision = self.transcript_revision("abrams_test.mp3")
        self.assertEqual(revision["text"], "")
        self.assertEqual(revision["source"], "skippednonspeech")
        self.assertEqual(revision["model"], "gpt-4o-transcribe")
        self.assertTrue(any(
            "accepted 1 blank non-speech results" in message
            for message in progress_messages
        ))

        with patch(
            "HistoricalContent.historical_content.baseline.transcribe_audio"
        ) as transcribe:
            create_baseline(settings, progress=lambda _message: None)
        transcribe.assert_not_called()

    def test_identical_audio_reuses_known_transcript(self):
        duplicate = self.source / "Audio" / "abrams_duplicate.mp3"
        duplicate.write_bytes((self.source / "Audio" / "abrams_test.mp3").read_bytes())
        payload = load_json(self.source / "all_voicelines.json")
        payload["abrams"]["Self"]["Test"].append({
            "filename": "abrams_duplicate.mp3",
            "voiceline_id": "abrams_duplicate",
            "transcription": "",
        })
        write_json(self.source / "all_voicelines.json", payload)

        create_baseline(self.settings(), progress=lambda message: None)

        duplicate_transcript = load_json(self.transcript_path("abrams_duplicate.mp3"))
        self.assertEqual(duplicate_transcript["revisions"][0]["text"], "Baseline line.")

    def test_completed_transcription_is_checkpointed_before_a_later_failure(self):
        audio = self.source / "Audio"
        (audio / "second_missing.mp3").write_bytes(b"second-missing-audio")
        payload = load_json(self.source / "all_voicelines.json")
        payload["abrams"]["Self"]["Test"][0]["transcription"] = ""
        payload["abrams"]["Self"]["Test"].append({
            "filename": "second_missing.mp3",
            "voiceline_id": "second_missing",
            "transcription": "",
        })
        write_json(self.source / "all_voicelines.json", payload)
        settings = BaselineSettings(
            source_dir=self.source,
            transcript_repo=self.repo,
            data_dir=self.data,
            api_key="test-key",
            transcription_vocabulary=self.vocabulary,
            transcribe_missing=True,
            workers=1,
            initialize_git=False,
        )

        prompts = []

        def fake_transcribe(path, **kwargs):
            prompts.append(kwargs["prompt"])
            if path.name == "abrams_test.mp3":
                return "Checkpointed line."
            raise RuntimeError("simulated later failure")

        with patch(
            "HistoricalContent.historical_content.baseline.transcribe_audio",
            side_effect=fake_transcribe,
        ):
            with self.assertRaisesRegex(RuntimeError, "simulated later failure"):
                create_baseline(settings, progress=lambda _message: None)

        checkpoint = load_json(self.transcript_path("abrams_test.mp3"))
        self.assertEqual(checkpoint["revisions"][0]["text"], "Checkpointed line.")
        self.assertEqual(checkpoint["revisions"][0]["source"], "generated")
        self.assertEqual(
            checkpoint["revisions"][0]["model"],
            "gpt-4o-transcribe",
        )
        self.assertIn('"Characters":["Abrams","Paradox"]', prompts[0])

    def test_transcription_prompt_attaches_structured_vocabulary_json(self):
        prompt = build_transcription_prompt(self.vocabulary)
        self.assertIn("Transcribe this Deadlock voice line in English", prompt)
        self.assertIn('"Characters":["Abrams","Paradox"]', prompt)
        self.assertIn(
            '"Transcription Guidelines":["Use standard punctuation."]',
            prompt,
        )

    def test_voiceline_and_conversation_share_one_audio_transcript(self):
        conversations = load_json(self.source / "all_conversations.json")
        conversations["conversations"][0]["lines"][0]["filename"] = "abrams_test.mp3"
        conversations["conversations"][0]["lines"][0]["transcription"] = ""
        write_json(self.source / "all_conversations.json", conversations)

        result = create_baseline(self.settings(), progress=lambda _message: None)

        transcript_files = list((self.repo / "transcripts").rglob("*.json"))
        self.assertEqual(transcript_files, [self.transcript_path("abrams_test.mp3")])
        conversation_output = load_json(
            result.preview_root / "deadlock" / "versions" /
            "preview-deadlock-base" / "conversations.json"
        )
        self.assertEqual(
            conversation_output["conversations"][0]["lines"][0]["transcription"],
            "Baseline line.",
        )

    def test_same_basename_in_different_folders_keeps_distinct_audio_keys(self):
        audio = self.source / "Audio"
        voice_root = audio / "sounds" / "vo"
        voice_root.mkdir(parents=True)
        for path in list(audio.glob("*.mp3")):
            path.replace(voice_root / path.name)
        (voice_root / "chrono").mkdir()
        (voice_root / "paradox").mkdir()
        (voice_root / "chrono" / "paradox_select_01.mp3").write_bytes(b"chrono-version")
        (voice_root / "paradox" / "paradox_select_01.mp3").write_bytes(b"paradox-version")

        payload = load_json(self.source / "all_voicelines.json")
        payload["paradox"] = {
            "Self": {
                "Select": [
                    {
                        "filename": "chrono/paradox_select_01.mp3",
                        "voiceline_id": "paradox_select_01",
                        "transcription": "Chrono recording.",
                    },
                    {
                        "filename": "paradox/paradox_select_01.mp3",
                        "voiceline_id": "paradox_select_01",
                        "transcription": "Paradox recording.",
                    },
                ]
            }
        }
        write_json(self.source / "all_voicelines.json", payload)

        result = create_baseline(self.settings(), progress=lambda _message: None)
        public = load_json(
            result.preview_root / "deadlock" / "versions" /
            "preview-deadlock-base" / "voicelines.json"
        )
        filenames = {
            line["filename"] for line in public["paradox"]["Self"]["Select"]
        }
        self.assertEqual(filenames, {
            "chrono/paradox_select_01.mp3",
            "paradox/paradox_select_01.mp3",
        })
        audio_keys = {
            line["audioKey"] for line in public["paradox"]["Self"]["Select"]
        }
        self.assertEqual(len(audio_keys), 2)
        for audio_key in audio_keys:
            relative = Path(*audio_key.split("/"))
            self.assertTrue((result.preview_root / "deadlock" / "audio" / relative).is_file())
            self.assertTrue((result.publish_source / "SharedAudio" / relative).is_file())
        for filename in filenames:
            transcript = load_json(self.transcript_path(filename))
            self.assertEqual(transcript["filename"], filename)

    def test_preview_keeps_generated_versions_and_reuses_shared_audio(self):
        first = create_baseline(self.settings(), progress=lambda _message: None)
        second_settings = BaselineSettings(
            source_dir=self.source,
            transcript_repo=self.repo,
            data_dir=self.data,
            version_id="deadlock-next",
            label="Next build",
            transcription_vocabulary=self.vocabulary,
            transcribe_missing=False,
            initialize_git=False,
        )
        second = create_baseline(second_settings, progress=lambda _message: None)

        manifest = load_json(second.preview_root / "deadlock" / "manifest.json")
        self.assertEqual(manifest["latestVersion"], "preview-deadlock-base")
        self.assertEqual(
            [entry["id"] for entry in manifest["versions"]],
            ["preview-deadlock-next", "preview-deadlock-base"],
        )
        characters = load_json(second.preview_root / "deadlock" / "characters.json")
        self.assertEqual(
            list(characters["versions"]),
            ["preview-deadlock-next", "preview-deadlock-base"],
        )
        self.assertEqual(
            len(list((second.preview_root / "deadlock" / "audio").rglob("*.mp3"))),
            2,
        )
        self.assertTrue(
            (first.preview_root / "deadlock" / "versions" / "preview-deadlock-base").is_dir()
        )

    def test_changed_audio_adds_a_revision_without_losing_the_old_one(self):
        create_baseline(self.settings(), progress=lambda _message: None)
        path = self.transcript_path("abrams_test.mp3")
        original = load_json(path)
        original_hash = original["revisions"][0]["sha256"]

        (self.source / "Audio" / "abrams_test.mp3").write_bytes(b"changed-audio")
        payload = load_json(self.source / "all_voicelines.json")
        payload["abrams"]["Self"]["Test"][0]["transcription"] = "Changed recording."
        write_json(self.source / "all_voicelines.json", payload)
        create_baseline(self.settings(), progress=lambda _message: None)

        updated = load_json(path)
        self.assertEqual(len(updated["revisions"]), 2)
        self.assertEqual(updated["revisions"][0]["sha256"], original_hash)
        self.assertEqual(updated["revisions"][0]["text"], "Baseline line.")
        self.assertEqual(updated["revisions"][1]["text"], "Changed recording.")

    def test_legacy_character_file_migrates_to_per_audio_json(self):
        legacy = self.repo / "voicelines" / "abrams.json"
        audio_hash = hashlib.sha256(b"fake-mp3-abrams").hexdigest()
        write_json(legacy, {
            "schemaVersion": 1,
            "speaker": "abrams",
            "lines": [{
                "lineId": "abrams_test",
                "audioSha256": audio_hash,
                "filename": "abrams_test.mp3",
                "location": ["Self", "Test"],
                "text": "Historical correction.",
                "source": "manual",
            }],
        })

        create_baseline(self.settings(), progress=lambda _message: None)

        migrated = load_json(self.transcript_path("abrams_test.mp3"))
        revisions = {revision["sha256"]: revision for revision in migrated["revisions"]}
        self.assertEqual(revisions[audio_hash]["text"], "Historical correction.")
        self.assertEqual(revisions[audio_hash]["source"], "manual")
        self.assertFalse(legacy.exists())

    def test_category_validation_reports_duplicates(self):
        errors, warnings = validate_categories({
            "schemaVersion": 1,
            "defaultCategory": "Characters",
            "categories": [
                {"name": "Characters", "characters": ["abrams"]},
                {"name": "NPCs", "characters": ["abrams", "unknown"]},
            ],
        }, {"abrams"})
        self.assertTrue(any("assigned to both" in error for error in errors))
        self.assertTrue(any("unknown character" in warning for warning in warnings))

    def test_category_refresh_does_not_regenerate_other_content(self):
        result = create_baseline(self.settings(), progress=lambda message: None)
        voice_path = (
            result.preview_root / "deadlock" / "versions" /
            "preview-deadlock-base" / "voicelines.json"
        )
        before = voice_path.read_bytes()
        categories_path = result.categories_path
        payload = load_json(categories_path)
        payload["categories"].append({"name": "NPCs", "characters": ["paradox"]})
        write_json(categories_path, payload)
        refresh_preview_categories(
            source_dir=self.source,
            transcript_repo=self.repo,
            data_dir=self.data,
            version_id="deadlock-base",
            progress=lambda message: None,
        )
        preview_categories = load_json(
            result.preview_root / "deadlock" / "versions" /
            "preview-deadlock-base" / "categories.json"
        )
        self.assertEqual(preview_categories["categories"][-1]["name"], "NPCs")
        self.assertEqual(voice_path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
