from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from HistoricalContent.historical_content.vpk_pipeline import (
    VpkPipelineSettings,
    _build_historical_icon_pack,
    _export_character_name_images,
    _export_character_select_backgrounds,
    _load_audio_filename_overrides,
    _normalize_shopkeeper_topics,
    _vpk_name_image_filters,
    _validate_mapping,
    create_coverage,
    extract_vpk_voice_audio,
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

    def test_identical_vdf_siblings_do_not_create_duplicate_phantoms(self):
        vdf = self.root / "citadel_generated_vo.txt"
        vdf.write_text(
            '"abrams_parry_01" "Nice parry."\n'
            '"abrams_parry_01_hero" "Nice parry."\n'
            '"abrams_parry_01_hero_3d" "A genuinely different missing variant."\n',
            encoding="utf-8",
        )

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
        parries = voices["abrams"]["Self"]["Combat"]["Parry"]
        self.assertEqual(len(parries), 2)
        self.assertEqual(parries[0]["filename"], "abrams_parry_01.mp3")
        self.assertEqual(parries[0]["transcription"], "Nice parry.")
        phantom = next(line for line in parries if line.get("is_phantom"))
        self.assertEqual(phantom["voiceline_id"], "abrams_parry_01_hero_3d")
        self.assertEqual(phantom["transcription"], "A genuinely different missing variant.")

    def test_coverage_uses_audio_in_place(self):
        voices = {"abrams": {"Self": {"Parry": [{"filename": "abrams_parry_01.mp3"}]}}}
        conversations = {"conversations": []}
        coverage = create_coverage(self.audio, voices, conversations)
        self.assertEqual(coverage["summary"]["total_files"], 3)
        self.assertEqual(coverage["summary"]["matched_files"], 1)
        self.assertEqual(coverage["summary"]["unmatched_files"], 2)

    def test_shopkeeper_topics_use_compact_ordered_hierarchy(self):
        result = {
            "shopkeeper_hotdog": {
                "Self": {
                    "Buy armor": ["armor"],
                    "Buy early": ["early"],
                    "Call out 10": ["call-out-ten"],
                    "Guide controls aim": ["aim"],
                    "Guide power shop": ["shop"],
                    "Hero training controls": ["controls"],
                    "Hero training intro alt": ["intro-alt"],
                    "Seasonal buy gun": ["seasonal-gun"],
                    "Shop System": {
                        "Call out": ["call-out"],
                        "Close shop": ["close"],
                    },
                    "Other": ["other"],
                }
            }
        }

        _normalize_shopkeeper_topics(result)

        topics = result["shopkeeper_hotdog"]["Self"]
        self.assertEqual(
            list(topics),
            ["Shop System", "Buy", "Guide", "Hero Training", "Seasonal", "Other"],
        )
        self.assertEqual(topics["Shop System"]["Call out"], ["call-out", "call-out-ten"])
        self.assertEqual(topics["Buy"], {"Armor": ["armor"], "Early": ["early"]})
        self.assertEqual(topics["Guide"], ["aim", "shop"])
        self.assertEqual(topics["Hero Training"], ["controls", "intro-alt"])
        self.assertEqual(topics["Seasonal"], {"Buy gun": ["seasonal-gun"]})

    def test_filename_overrides_change_parsing_but_preserve_public_filename(self):
        bad_conversation = "paradox_match_start_abrams_paradox_convo01_04.mp3"
        corrected_conversation = "paradox_match_start_abrams_paradox_convo01_03.mp3"
        bad_voice = "abrams_parry_broken.mp3"
        ignored_voice = "abrams_parry_imported.mp3"
        (self.audio / bad_conversation).write_bytes(b"three")
        (self.audio / bad_voice).write_bytes(b"corrected voice")
        (self.audio / ignored_voice).write_bytes(b"ignored voice")
        overrides = {
            bad_conversation.casefold(): corrected_conversation,
            bad_voice.casefold(): "abrams_parry_02.mp3",
            ignored_voice.casefold(): None,
        }

        conversations = parse_conversations(
            self.audio,
            ASSETS / "character_mappings.json",
            ASSETS / "conversation_overrides.json",
            None,
            include_phantom=False,
            audio_filename_overrides=overrides,
        )
        conversation = conversations["conversations"][0]
        self.assertTrue(conversation["is_complete"])
        self.assertEqual([line["part"] for line in conversation["lines"]], [1, 2, 3])
        self.assertEqual(conversation["lines"][2]["filename"], bad_conversation)

        voices, unresolved = parse_voicelines(
            self.audio,
            ASSETS / "character_mappings.json",
            ASSETS / "topic_mappings.json",
            ASSETS / "voiceline_groups.json",
            None,
            include_phantom=False,
            progress=lambda _message: None,
            audio_filename_overrides=overrides,
        )
        self.assertFalse(unresolved)
        parry_names = {
            line["filename"] for line in voices["abrams"]["Self"]["Combat"]["Parry"]
        }
        self.assertIn(bad_voice, parry_names)
        self.assertNotIn(ignored_voice, parry_names)

    def test_version_filename_override_config_loads_rules(self):
        config = self.root / "audio-filename-overrides.json"
        malformed = (
            "gigawatt/"
            "gigawatt_match_start_gigawatt_inferno_convo02_05"
            "gigawatt_match_start_gigawatt_inferno_convo02_05.mp3"
        )
        config.write_text(json.dumps({
            "schemaVersion": 1,
            "overrides": {
                malformed: {
                    "parseAs": "gigawatt/gigawatt_match_start_gigawatt_inferno_convo02_05.mp3"
                },
                "vampirebat/vampirebat_use_power1_01-imported.mp3": {"ignore": True},
            },
        }), encoding="utf-8")
        overrides = _load_audio_filename_overrides(config)
        self.assertEqual(
            overrides[malformed],
            "gigawatt/gigawatt_match_start_gigawatt_inferno_convo02_05.mp3",
        )
        self.assertIsNone(overrides["vampirebat/vampirebat_use_power1_01-imported.mp3"])

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
            "newscaster/guide_controls_abilities.mp3",
            "newscaster/guide_the_map_welcome_alt.mp3",
            "newscaster/news_reel_test.mp3",
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
        self.assertEqual(
            {
                line["filename"]
                for line in voices["newscaster"]["Self"]["Guide"]
            },
            {
                "newscaster/guide_controls_abilities.mp3",
                "newscaster/guide_the_map_welcome_alt.mp3",
            },
        )
        self.assertEqual(
            voices["newscaster"]["Self"]["News reel test"][0]["filename"],
            "newscaster/news_reel_test.mp3",
        )
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
        (extracted / "chrono_sm_psd.png").write_bytes(b"chrono minimap")
        (extracted / "chrono_card_psd.png").write_bytes(b"chrono normal")
        (extracted / "chrono_card_gloat_psd.png").write_bytes(b"chrono gloat")
        (extracted / "chrono_card_critical_psd.png").write_bytes(b"chrono critical")
        (extracted / "kali_sm_psd.png").write_bytes(b"kali minimap")
        (extracted / "bull_sm_psd.png").write_bytes(b"abrams minimap")
        (extracted / "bull_card_psd.png").write_bytes(b"abrams normal")
        (extracted / "werewolf_card_psd.png").write_bytes(b"silver human")
        (extracted / "werewolf_wolf_card_psd.png").write_bytes(b"silver wolf")
        (extracted / "hornet_sm_png.png").write_bytes(b"vindicta large icon")
        (extracted / "hornet_sm_psd_d09ce06e.png").write_bytes(b"non-canonical duplicate")
        (extracted / "kali_mm_psd.png").write_bytes(b"vyper low-res minimap")
        (extracted / "hornet_mm_psd.png").write_bytes(b"vindicta low-res minimap")
        npcs = self.root / "extracted-icons" / "panorama" / "images" / "npcs"
        npcs.mkdir(parents=True)
        (npcs / "patron_archmother_psd.png").write_bytes(b"female patron minimap")
        (npcs / "patron_hiddenking_psd.png").write_bytes(b"male patron minimap")
        (npcs / "patron_psd.png").write_bytes(b"unused generic patron")
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
            "}\n"
            "hero_hornet =\n"
            "{\n"
            '  m_strIconImageSmall = panorama:"file://{images}/heroes/hornet_sm.png"\n'
            '  m_strMinimapImage = panorama:"file://{images}/heroes/hornet_mm.psd"\n'
            "}\n",
            encoding="utf-8",
        )
        destination = self.root / "IconPacks" / "default"

        with patch(
            "HistoricalContent.historical_content.vpk_pipeline.read_image_dimensions",
            return_value=(128, 128),
        ):
            count = _build_historical_icon_pack(
                self.root / "extracted-icons",
                destination,
                _validate_mapping(ASSETS / "character_mappings.json"),
            )

        self.assertEqual(count, 14)
        manifest = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(
            manifest["icons"]["minimap"]["chrono"],
            manifest["icons"]["minimap"]["paradox"],
        )
        self.assertEqual(
            manifest["icons"]["minimap"]["kali"],
            manifest["icons"]["minimap"]["vyper"],
        )
        self.assertEqual(
            manifest["icons"]["minimap"]["hornet"],
            manifest["icons"]["minimap"]["vindicta"],
        )
        self.assertEqual(
            (destination / manifest["icons"]["minimap"]["vindicta"]).read_bytes(),
            b"vindicta large icon",
        )
        self.assertEqual(
            manifest["icons"]["minimap-low-res"]["kali"],
            manifest["icons"]["minimap-low-res"]["vyper"],
        )
        self.assertEqual(
            manifest["icons"]["minimap-low-res"]["hornet"],
            manifest["icons"]["minimap-low-res"]["vindicta"],
        )
        self.assertEqual(
            (destination / manifest["icons"]["minimap-low-res"]["vindicta"]).read_bytes(),
            b"vindicta low-res minimap",
        )
        self.assertEqual(
            manifest["icons"]["normal"]["atlas"],
            manifest["icons"]["normal"]["abrams"],
        )
        self.assertTrue(manifest["icons"]["normal"]["paradox"].startswith("normal/chrono."))
        self.assertTrue(manifest["icons"]["gloat"]["paradox"].startswith("gloat/chrono."))
        self.assertTrue(manifest["icons"]["critical"]["paradox"].startswith("critical/chrono."))
        self.assertTrue((destination / manifest["icons"]["normal"]["paradox"]).is_file())
        self.assertNotEqual(
            manifest["icons"]["normal"]["werewolf"],
            manifest["icons"]["normal"]["werewolf_wolf"],
        )
        self.assertEqual(
            manifest["icons"]["normal"]["silver"],
            manifest["icons"]["normal"]["werewolf"],
        )
        self.assertEqual(
            manifest["icons"]["minimap"]["patron_female"],
            manifest["icons"]["minimap"]["archmother"],
        )
        self.assertEqual(
            manifest["icons"]["minimap"]["patron_male"],
            manifest["icons"]["minimap"]["hidden_king"],
        )
        self.assertTrue(
            manifest["icons"]["minimap"]["patron_female"].startswith(
                "minimap/patron_female."
            )
        )
        self.assertNotIn("patron_female", manifest["icons"]["normal"])
        self.assertNotIn("patron_male", manifest["icons"]["normal"])
        referenced_paths = {
            relative
            for entries in manifest["icons"].values()
            for relative in entries.values()
        }
        written_images = {
            path.relative_to(destination).as_posix()
            for path in destination.rglob("*")
            if path.is_file() and path.name != "manifest.json"
        }
        self.assertEqual(written_images, referenced_paths)
        self.assertFalse(any(path.name.startswith("patron_psd.") for path in destination.rglob("*")))

    def test_historical_icon_pack_can_limit_backfill_to_minimap_and_normal(self):
        extracted = self.root / "limited-icons"
        extracted.mkdir()
        (extracted / "chrono_sm_psd.png").write_bytes(b"minimap")
        (extracted / "chrono_mm_psd.png").write_bytes(b"low-res minimap")
        (extracted / "chrono_card_psd.png").write_bytes(b"normal")
        (extracted / "chrono_card_gloat_psd.png").write_bytes(b"gloat")
        (extracted / "chrono_card_critical_psd.png").write_bytes(b"critical")
        destination = self.root / "limited-pack"

        with patch(
            "HistoricalContent.historical_content.vpk_pipeline.read_image_dimensions",
            return_value=(128, 128),
        ):
            count = _build_historical_icon_pack(
                extracted,
                destination,
                _validate_mapping(ASSETS / "character_mappings.json"),
                include_highlight_variants=False,
            )

        self.assertEqual(count, 3)
        manifest = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(
            set(manifest["icons"]),
            {"minimap", "minimap-low-res", "normal"},
        )

    def test_character_name_images_are_optional_and_manifest_maps_aliases(self):
        binary = self.root / "Source2Viewer-CLI.exe"
        main_vpk = self.root / "build" / "game" / "citadel" / "pak01_dir.vpk"
        russian_vpk = self.root / "build" / "game" / "citadel_russian" / "pak01_dir.vpk"
        for path in (binary, main_vpk, russian_vpk):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"fixture")
        source = self.root / "workspace" / "source"
        source.mkdir(parents=True)
        settings = VpkPipelineSettings(
            source2viewer_binary=binary,
            vpk_path=main_vpk,
            data_dir=self.root / "data",
            transcript_repo=self.root / "transcripts",
            version_id="test-version",
            name_image_max_height=512,
        )

        def fake_convert(_extracted, destination, _height):
            destination.mkdir(parents=True, exist_ok=True)
            if destination.name == "english":
                values = {
                    "abrams": "abrams_localized.hash.webp",
                    "patron_female": "team2.hash.webp",
                }
            else:
                values = {"abrams": "abrams_localized.russian.webp"}
            result = {}
            for key, filename in values.items():
                (destination / filename).write_bytes(b"webp")
                result[key] = {"file": filename, "width": 640, "height": 512}
            warnings = ["broken_localized.svg: invalid SVG"] if destination.name == "english" else []
            return result, warnings

        progress_messages: list[str] = []

        with (
            patch(
                "HistoricalContent.historical_content.vpk_pipeline._vpk_name_image_filters",
                return_value=("panorama/images/heroes/hero_names",),
            ),
            patch("HistoricalContent.historical_content.vpk_pipeline._run_source2viewer"),
            patch(
                "HistoricalContent.historical_content.vpk_pipeline._run_name_image_converter",
                side_effect=fake_convert,
            ),
        ):
            count, availability = _export_character_name_images(
                settings,
                source,
                self.root / "build",
                ASSETS / "character_mappings.json",
                progress=progress_messages.append,
            )

        self.assertEqual(count, 3)
        self.assertTrue(availability["available"])
        self.assertTrue(any("skipped malformed asset" in item for item in progress_messages))
        manifest = json.loads(
            (source / "CharacterNameImages" / "manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["maxHeight"], 512)
        self.assertEqual(
            manifest["languages"]["english"]["abrams"]["path"],
            "english/abrams_localized.hash.webp",
        )
        self.assertEqual(
            manifest["languages"]["english"]["archmother"]["path"],
            "english/team2.hash.webp",
        )
        self.assertEqual(
            manifest["languages"]["russian"]["abrams"]["path"],
            "russian/abrams_localized.russian.webp",
        )

    def test_character_select_backgrounds_crop_and_map_portrait_aliases(self):
        binary = self.root / "Source2Viewer-CLI.exe"
        vpk = self.root / "pak01_dir.vpk"
        binary.write_bytes(b"fixture")
        vpk.write_bytes(b"fixture")
        source = self.root / "workspace" / "source"
        source.mkdir(parents=True)
        settings = VpkPipelineSettings(
            source2viewer_binary=binary,
            vpk_path=vpk,
            data_dir=self.root / "data",
            transcript_repo=self.root / "transcripts",
            version_id="test",
        )

        def fake_converter(_extracted, destination, _width=1024):
            destination.mkdir(parents=True, exist_ok=True)
            (destination / "familiar.hash.webp").write_bytes(b"webp")
            (destination / "patience.hash.webp").write_bytes(b"webp")
            return ({
                "familiar": {
                    "file": "familiar.hash.webp",
                    "width": 1024,
                    "height": 1024,
                    "accentColor": "#284b3a",
                },
                "patience": {
                    "file": "patience.hash.webp",
                    "width": 1024,
                    "height": 1024,
                    "accentColor": "#315761",
                }
            }, [])

        with patch("HistoricalContent.historical_content.vpk_pipeline._run_source2viewer"), patch(
            "HistoricalContent.historical_content.vpk_pipeline._run_character_select_background_converter",
            side_effect=fake_converter,
        ):
            count, availability = _export_character_select_backgrounds(
                settings,
                source,
                ASSETS / "character_mappings.json",
                progress=lambda _message: None,
            )

        self.assertEqual(count, 2)
        self.assertTrue(availability["available"])
        manifest = json.loads(
            (source / "CharacterSelectBackgrounds" / "manifest.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(manifest["crop"], "right-half")
        self.assertEqual(manifest["backgrounds"]["rem"]["accentColor"], "#284b3a")
        self.assertEqual(
            manifest["backgrounds"]["rem"],
            manifest["backgrounds"]["familiar"],
        )
        self.assertEqual(
            manifest["backgrounds"]["paige"],
            manifest["backgrounds"]["patience"],
        )

    def test_missing_character_name_images_do_not_fail_the_pipeline_stage(self):
        binary = self.root / "Source2Viewer-CLI.exe"
        vpk = self.root / "pak01_dir.vpk"
        binary.write_bytes(b"fixture")
        vpk.write_bytes(b"fixture")
        source = self.root / "workspace" / "source"
        source.mkdir(parents=True)
        settings = VpkPipelineSettings(
            source2viewer_binary=binary,
            vpk_path=vpk,
            data_dir=self.root / "data",
            transcript_repo=self.root / "transcripts",
            version_id="test-version",
        )

        with patch(
            "HistoricalContent.historical_content.vpk_pipeline._vpk_name_image_filters",
            return_value=(),
        ):
            count, availability = _export_character_name_images(
                settings,
                source,
                None,
                ASSETS / "character_mappings.json",
                progress=lambda _message: None,
            )

        self.assertEqual(count, 0)
        self.assertFalse(availability["available"])
        self.assertFalse((source / "CharacterNameImages").exists())

    def test_name_image_discovery_checks_each_source2viewer_filter(self):
        outputs = [
            "panorama/images/heroes/hero_names/abrams_localized.vsvg_c",
            "No files matched.",
            "panorama/images/hud/objectives/team2_patron_logo_psd.vtex_c",
        ]
        completed = [
            subprocess.CompletedProcess([], 0, stdout=output)
            for output in outputs
        ]
        with patch("subprocess.run", side_effect=completed) as run:
            filters = _vpk_name_image_filters(
                self.root / "Source2Viewer-CLI.exe",
                self.root / "pak01_dir.vpk",
            )

        self.assertEqual(
            filters,
            (
                "panorama/images/heroes/hero_names",
                "panorama/images/hud/objectives/team2_patron_logo_psd",
            ),
        )
        self.assertEqual(run.call_count, 3)

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
            extract_name_images=False,
            extract_character_select_backgrounds=False,
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
        self.assertEqual(first.audio_filename_overrides.name, "audio-filename-overrides.json")
        self.assertTrue(first.audio_filename_overrides.is_file())
        self.assertEqual(
            first.audio_filename_overrides,
            settings.transcript_repo
            / "config"
            / "deadlock"
            / "versions"
            / "test-version"
            / "audio-filename-overrides.json",
        )
        extracted_audio = list((settings.data_dir / "workspaces").rglob("*.mp3"))
        self.assertEqual(len(extracted_audio), 1)
        self.assertTrue(first.source_dir / "Audio" in extracted_audio[0].parents)
        self.assertTrue(
            (settings.transcript_repo / "config" / "deadlock" / "voiceline-groups.json").is_file()
        )

    def test_custom_voice_vpk_extraction_is_isolated_and_reusable(self):
        binary = self.root / "Source2Viewer-CLI.exe"
        vpk = self.root / "russian_voice_dir.vpk"
        workspace = self.root / "workspaces" / "deadlock" / "custom" / "custom-voice-mod-vpk"
        binary.write_bytes(b"fake executable")
        vpk.write_bytes(b"fake vpk")

        def fake_extract(_binary, _vpk, output, file_filter, _threads, _progress):
            self.assertEqual(file_filter, "sounds/vo")
            destination = output / "sounds" / "vo" / "hero"
            destination.mkdir(parents=True)
            (destination / "line.mp3").write_bytes(b"russian voice")

        with patch(
            "HistoricalContent.historical_content.vpk_pipeline._run_source2viewer",
            side_effect=fake_extract,
        ) as extract:
            first = extract_vpk_voice_audio(
                source2viewer_binary=binary,
                vpk_path=vpk,
                workspace=workspace,
                progress=lambda _message: None,
            )
            second = extract_vpk_voice_audio(
                source2viewer_binary=binary,
                vpk_path=vpk,
                workspace=workspace,
                progress=lambda _message: None,
            )

        extract.assert_called_once()
        self.assertFalse(first.reused)
        self.assertTrue(second.reused)
        self.assertEqual(first.audio_dir, second.audio_dir)
        self.assertEqual(first.audio_count, 1)
        self.assertTrue(first.state_path.is_file())
        self.assertTrue(first.workspace / "Audio" in (first.audio_dir / "hero" / "line.mp3").parents)


if __name__ == "__main__":
    unittest.main()
