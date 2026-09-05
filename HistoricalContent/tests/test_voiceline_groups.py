from __future__ import annotations

import copy
import unittest
from pathlib import Path

from historical_content.parsing.groups import (
    classify_topic,
    configured_group_labels,
    load_group_config,
    sort_subject_topics,
    validate_group_config,
)
from historical_content.parsing.voicelines import VoicelineParser


class VoicelineGroupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = load_group_config()

    def test_all_legacy_groups_are_in_json(self):
        voice = configured_group_labels(self.config, "voice")
        pings = configured_group_labels(self.config, "ping")
        self.assertEqual(len(voice), 25)
        self.assertEqual(len(pings), 9)
        self.assertIn("Hero Selection", voice)
        self.assertIn("Street Brawl Mode", voice)
        self.assertIn("Objective Commands", pings)
        self.assertIn("Miscellaneous Status", pings)

    def test_exact_prefix_subgroup_and_ping_routing(self):
        self.assertEqual(classify_topic(self.config, "voice", "parry"), ("Combat",))
        self.assertEqual(
            classify_topic(self.config, "voice", "use_healing_rite"),
            ("Item Usage",),
        )
        self.assertEqual(
            classify_topic(self.config, "voice", "use_power1"),
            ("Use Power",),
        )
        self.assertEqual(
            classify_topic(self.config, "voice", "pain_big"),
            ("Emotions", "Pain"),
        )
        self.assertEqual(
            classify_topic(self.config, "voice", "effort_dash"),
            ("Emotions", "Effort"),
        )
        self.assertEqual(
            classify_topic(self.config, "ping", "attack_enemy"),
            ("Objective Commands",),
        )
        self.assertEqual(
            classify_topic(self.config, "voice", "start"), ("Match Status",)
        )
        self.assertEqual(
            classify_topic(self.config, "voice", "start_match"), ("Match Status",)
        )
        self.assertEqual(
            classify_topic(self.config, "voice", "unkillable_(ally)"),
            ("Ally Actions",),
        )
        self.assertEqual(
            classify_topic(self.config, "voice", "unkillable_(enemy)"),
            ("Enemy Actions",),
        )
        self.assertIsNone(classify_topic(self.config, "voice", "new_unknown_topic"))

    def test_duplicate_topic_assignment_is_rejected(self):
        payload = copy.deepcopy(self.config)
        payload["groups"][0]["match"]["topics"].append("parry")
        errors = validate_group_config(payload)
        self.assertTrue(any("assigned to both" in error for error in errors))

    def test_config_controls_root_and_group_order(self):
        topics = {
            "Combat": {},
            "Post game": [],
            "Alpha": [],
            "Hero Selection": {},
            "Pings": {},
            "Item Usage": {},
        }
        ordered = sort_subject_topics(self.config, topics)
        self.assertEqual(
            list(ordered),
            ["Post game", "Alpha", "Item Usage", "Hero Selection", "Combat", "Pings"],
        )

    def test_parser_uses_configured_paths(self):
        parser = VoicelineParser(Path("."), self.config, lambda _message: None)
        result = {}
        parser.place_in_result(
            result,
            ("holliday", "self", "Pain big", None, "pain.mp3", False),
            "pain.mp3",
        )
        parser.place_in_result(
            result,
            ("holliday", "self", "Attack enemy", None, "ping.mp3", True),
            "ping.mp3",
        )
        parser.place_in_result(
            result,
            ("holliday", "abrams", "Unkillable (ally)", "ally", "ally.mp3", False),
            "ally.mp3",
        )
        parser.place_in_result(
            result,
            ("holliday", "abrams", "Unkillable (enemy)", "enemy", "enemy.mp3", False),
            "enemy.mp3",
        )
        self.assertEqual(
            result["holliday"]["Self"]["Emotions"]["Pain"]["Pain big"],
            ["pain.mp3"],
        )
        self.assertEqual(
            result["holliday"]["Self"]["Pings"]["Objective Commands"]["Attack enemy"],
            ["ping.mp3"],
        )
        self.assertEqual(
            result["holliday"]["abrams"]["Ally Actions"]["Unkillable (ally)"],
            ["ally.mp3"],
        )
        self.assertEqual(
            result["holliday"]["abrams"]["Enemy Actions"]["Unkillable (enemy)"],
            ["enemy.mp3"],
        )


if __name__ == "__main__":
    unittest.main()
