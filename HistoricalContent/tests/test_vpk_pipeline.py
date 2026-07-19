from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from HistoricalContent.historical_content.vpk_pipeline import (
    VpkPipelineSettings,
    _build_historical_icon_pack,
    _validate_mapping,
    create_coverage,
    parse_conversations,
    parse_voicelines,
    prepare_vpk_export,
)


UTILITIES = Path(__file__).resolve().parents[2]
ASSETS = UTILITIES / "Assets"


class VpkPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.audio = self.root / "audio"
        self.audio.mkdir()
        (self.audio / "abrams_parry_01.mp3").write_bytes(b"parry")
        (self.audio / "abrams_match_start_abrams_paradox_convo01_01.mp3").write_bytes(b"one")
        (self.audio / "paradox_match_start_abrams_paradox_convo01_02.mp3").write_bytes(b"two")

    def tearDown(self):
        self.temp.cleanup()

    def test_headless_parsers_use_external_configuration(self):
        vdf = self.root / "citadel_generated_vo.txt"
        vdf.write_text(
            '"abrams_parry_01_hero" "Nice parry."\n'
            '"abrams_match_start_abrams_paradox_convo01_01_hero" "First line."\n',
            encoding="utf-8",
        )
        conversations = parse_conversations(
            self.audio,
            ASSETS / "character_mappings.json",
            ASSETS / "conversation_overrides.json",
            vdf,
            include_phantom=True,
        )
        self.assertEqual(conversations["total_conversations"], 1)
        conversation = conversations["conversations"][0]
        self.assertTrue(conversation["is_complete"])
        self.assertEqual(conversation["lines"][0]["transcription"], "First line.")

        voices, unresolved = parse_voicelines(
            self.audio,
            ASSETS / "character_mappings.json",
            ASSETS / "topic_mappings.json",
            ASSETS / "voiceline_groups.json",
            vdf,
            include_phantom=True,
            progress=lambda _message: None,
        )
        self.assertFalse(unresolved)
        parry = voices["abrams"]["Self"]["Combat"]["Parry"][0]
        self.assertEqual(parry["filename"], "abrams_parry_01.mp3")
        self.assertEqual(parry["transcription"], "Nice parry.")
        self.assertTrue(parry["officialtranscription"])

    def test_coverage_uses_audio_in_place(self):
        voices = {"abrams": {"Self": {"Parry": [{"filename": "abrams_parry_01.mp3"}]}}}
        conversations = {"conversations": []}
        coverage = create_coverage(self.audio, voices, conversations)
        self.assertEqual(coverage["summary"]["total_files"], 3)
        self.assertEqual(coverage["summary"]["matched_files"], 1)
        self.assertEqual(coverage["summary"]["unmatched_files"], 2)

    def test_voiceline_keys_keep_folders_when_basenames_collide(self):
        collision_root = self.root / "collision-audio"
        (collision_root / "chrono").mkdir(parents=True)
        (collision_root / "paradox").mkdir(parents=True)
        (collision_root / "chrono" / "paradox_select_01.mp3").write_bytes(b"chrono")
        (collision_root / "paradox" / "paradox_select_01.mp3").write_bytes(b"paradox")

        voices, _unresolved = parse_voicelines(
            collision_root,
            ASSETS / "character_mappings.json",
            ASSETS / "topic_mappings.json",
            ASSETS / "voiceline_groups.json",
            None,
            include_phantom=False,
            progress=lambda _message: None,
        )

        def filenames(value):
            if isinstance(value, dict):
                if isinstance(value.get("filename"), str):
                    yield value["filename"]
                for child in value.values():
                    yield from filenames(child)
            elif isinstance(value, list):
                for child in value:
                    yield from filenames(child)

        self.assertEqual(
            set(filenames(voices)),
            {
                "chrono/paradox_select_01.mp3",
                "paradox/paradox_select_01.mp3",
            },
        )
        coverage = create_coverage(collision_root, voices, {"conversations": []})
        self.assertEqual(coverage["summary"]["matched_files"], 2)
        self.assertEqual(coverage["summary"]["unmatched_files"], 0)

    def test_rr_test_voicelines_use_their_historical_hero_folder(self):
        legacy_root = self.root / "legacy-audio"
        (legacy_root / "kali").mkdir(parents=True)
        (legacy_root / "butcher").mkdir(parents=True)
        (legacy_root / "kali" / "rr_test_19_angry_01.mp3").write_bytes(b"vyper")
        (legacy_root / "butcher" / "rr_test_21_angry_01.mp3").write_bytes(b"butcher")
        (legacy_root / "butcher" / "rr_test_21_buy_armor_upgrade_01.mp3").write_bytes(
            b"legacy category"
        )

        messages: list[str] = []
        voices, unresolved = parse_voicelines(
            legacy_root,
            ASSETS / "character_mappings.json",
            ASSETS / "topic_mappings.json",
            ASSETS / "voiceline_groups.json",
            None,
            include_phantom=False,
            progress=messages.append,
        )

        self.assertFalse(unresolved)
        self.assertEqual(
            voices["vyper"]["Self"]["Emotions"]["Angry"][0]["filename"],
            "kali/rr_test_19_angry_01.mp3",
        )
        self.assertEqual(
            voices["butcher"]["Self"]["Emotions"]["Angry"][0]["filename"],
            "butcher/rr_test_21_angry_01.mp3",
        )
        self.assertIn("Captured 3 legacy rr_test voicelines from their hero folders.", messages)
        coverage = create_coverage(legacy_root, voices, {"conversations": []})
        self.assertEqual(coverage["summary"]["matched_files"], 3)
        self.assertEqual(coverage["summary"]["unmatched_files"], 0)

    def test_historical_matching_fallbacks_and_normalization(self):
        historical = self.root / "historical-audio" / "sounds" / "vo"
        filenames = [
            "t1_guardians/guardian_test_01/rr_guardian_test_01_congrats_akimbo.mp3",
            "book/oathkeeper/vn_geist_scene01_01.mp3",
            "book/oathkeeper/vn_geist_scene01_02.mp3",
            "neutral_gremlin/neutral_gremlin_attack_01.mp3",
            "announcer/female_patron/guide_controls_aim.mp3",
            "atlas/abrams_ally_grey_talon_killed_in_lane_01.mp3",
            "krill/krill_killed_by_astro_01.mp3",
            "haze/emote/haze_emote_pain_small_01.mp3",
            "haze/ping/haze_ping_gigawatt_old_missing_01.mp3",
            "haze/ping/haze_ping_attack_grey_talon.mp3",
            "haze/ping/haze_ping_defend_orange.mp3",
            "haze/ping/haze_ping_headed_to_orange.mp3",
            "haze/ping/haze_ping_idols_call_01.mp3",
            "haze/ping/haze_ping_nevermind_01.mp3",
            "haze/ping/haze_ping_take_core.mp3",
            "haze/ping/haze_ping_titan_under_attack.mp3",
            "haze/ping/haze_ping_back_01.mp3",
        ]
        for filename in filenames:
            path = historical / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(filename.encode("utf-8"))

        messages: list[str] = []
        voices, unresolved = parse_voicelines(
            historical,
            ASSETS / "character_mappings.json",
            ASSETS / "topic_mappings.json",
            ASSETS / "voiceline_groups.json",
            None,
            include_phantom=False,
            progress=messages.append,
        )

        self.assertFalse(unresolved)
        self.assertEqual(
            voices["guardian_test_01"]["akimbo"]["Emotions"]["Congrats"][0]["filename"],
            filenames[0],
        )
        self.assertEqual(len(voices["oathkeeper"]["Self"]["Scene01"]), 2)
        self.assertEqual(
            voices["neutral_gremlin"]["Self"]["Attack"][0]["filename"],
            filenames[3],
        )
        self.assertIn("Guide controls aim", voices["patron_female"]["Self"])
        self.assertIn(
            "Killed in lane (ally)",
            voices["abrams"]["grey talon"]["Combat"],
        )
        self.assertIn("Killed by", voices["mo&krill"]["holliday"])
        self.assertIn("Pain small", voices["haze"]["Self"]["Emotions"]["Pain"])

        pings = voices["haze"]
        self.assertIn("Missing", pings["seven"]["Pings"]["Enemy Information and Location"])
        self.assertIn("Attack", pings["grey talon"]["Pings"]["Tactical Communication"])
        self.assertIn("Defend orange", pings["Self"]["Pings"]["Objective Commands"])
        self.assertIn("Headed orange", pings["Self"]["Pings"]["Movement and Positioning"])
        self.assertIn("Jar call", pings["Self"]["Pings"]["Miscellaneous Status"])
        self.assertIn("Nevermind", pings["Self"]["Pings"]["General Communication / Social"])
        self.assertIn("Take core", pings["Self"]["Pings"]["Objective Commands"])
        self.assertIn("Titan under attack", pings["Self"]["Pings"]["Requests and Alerts"])
        self.assertIn("Right back", pings["Self"]["Pings"]["Movement and Positioning"])
        self.assertTrue(any("approved historical folder fallbacks" in item for item in messages))

        coverage = create_coverage(historical, voices, {"conversations": []})
        self.assertEqual(coverage["summary"]["matched_files"], len(filenames))
        self.assertEqual(coverage["summary"]["unmatched_files"], 0)

    def test_historical_icon_pack_maps_internal_and_canonical_names(self):
        extracted = self.root / "extracted-icons" / "panorama" / "images" / "heroes"
        extracted.mkdir(parents=True)
        (extracted / "chrono_mm_psd.png").write_bytes(b"chrono minimap")
        (extracted / "chrono_sm_psd.png").write_bytes(b"chrono normal")
        (extracted / "kali_mm_psd.png").write_bytes(b"kali minimap")
        (extracted / "bull_mm_psd.png").write_bytes(b"abrams minimap")
        (extracted / "bull_sm_psd.png").write_bytes(b"abrams normal")
        (extracted / "kali_card_psd.png").write_bytes(b"ignored card")
        scripts = self.root / "extracted-icons" / "scripts"
        scripts.mkdir(parents=True)
        (scripts / "heroes.vdata").write_text(
            "hero_atlas =\n"
            "{\n"
            '  m_strIconImageSmall = panorama:"file://{images}/heroes/bull_sm.psd"\n'
            '  m_strMinimapImage = panorama:"file://{images}/heroes/bull_mm.psd"\n'
            "}\n"
            "hero_chrono =\n"
            "{\n"
            '  m_strIconImageSmall = panorama:"file://{images}/heroes/chrono_sm.psd"\n'
            '  m_strMinimapImage = panorama:"file://{images}/heroes/chrono_mm.psd"\n'
            "}\n",
            encoding="utf-8",
        )
        destination = self.root / "IconPacks" / "default"

        count = _build_historical_icon_pack(
            self.root / "extracted-icons",
            destination,
            _validate_mapping(ASSETS / "character_mappings.json"),
        )

        self.assertEqual(count, 5)
        manifest = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["icons"]["minimap"]["chrono"], "minimap/chrono.png")
        self.assertEqual(manifest["icons"]["minimap"]["paradox"], "minimap/chrono.png")
        self.assertEqual(manifest["icons"]["minimap"]["kali"], "minimap/kali.png")
        self.assertEqual(manifest["icons"]["minimap"]["vyper"], "minimap/kali.png")
        self.assertEqual(manifest["icons"]["normal"]["paradox"], "normal/chrono.png")
        self.assertEqual(manifest["icons"]["normal"]["atlas"], "normal/bull.png")
        self.assertEqual(manifest["icons"]["normal"]["abrams"], "normal/bull.png")
        self.assertTrue((destination / "normal" / "chrono.png").is_file())
        self.assertFalse((destination / "normal" / "kali.png").exists())

    def test_unchanged_vpk_reuses_persistent_extraction(self):
        binary = self.root / "Source2Viewer-CLI.exe"
        vpk = self.root / "pak01_dir.vpk"
        binary.write_bytes(b"fake executable")
        vpk.write_bytes(b"fake vpk")
        settings = VpkPipelineSettings(
            source2viewer_binary=binary,
            vpk_path=vpk,
            data_dir=self.root / "data",
            transcript_repo=self.root / "transcripts",
            version_id="test-version",
            extract_localization=False,
            extract_icons=False,
        )

        def fake_extract(_binary, _vpk, output, _filter, _threads, _progress):
            destination = output / "sounds" / "vo"
            destination.mkdir(parents=True)
            (destination / "abrams_parry_01.mp3").write_bytes(b"parry")

        with patch(
            "HistoricalContent.historical_content.vpk_pipeline._run_source2viewer",
            side_effect=fake_extract,
        ) as extract:
            first = prepare_vpk_export(settings, progress=lambda _message: None)
            second = prepare_vpk_export(settings, progress=lambda _message: None)

        extract.assert_called_once()
        self.assertEqual(first.audio_dir, second.audio_dir)
        self.assertTrue((first.source_dir / "all_voicelines.json").is_file())
        self.assertTrue((first.source_dir / "all_conversations.json").is_file())
        self.assertTrue((first.source_dir / "coverage.json").is_file())
        self.assertEqual(first.transcription_vocabulary.name, "transcription-vocabulary.json")
        self.assertTrue(first.transcription_vocabulary.is_file())
        extracted_audio = list((settings.data_dir / "workspaces").rglob("*.mp3"))
        self.assertEqual(len(extracted_audio), 1)
        self.assertTrue(first.source_dir / "Audio" in extracted_audio[0].parents)
        self.assertTrue(
            (settings.transcript_repo / "config" / "deadlock" / "voiceline-groups.json").is_file()
        )


if __name__ == "__main__":
    unittest.main()
