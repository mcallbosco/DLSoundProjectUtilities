"""Classify voice audio across historical and current filename formats."""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from ..errors import VpkPipelineError
from .common import alias_index, effective_audio_path, read_json, validate_mapping
from .conversations import conversation_key_from_name
from .groups import classify_topic, load_group_config, sort_subject_topics
from .vdf import (
    ORDERED_KNOWN_SUFFIXES,
    find_vdf_key_for_filename,
    find_vdf_match_for_filename,
    load_vdf_key_text_map,
)

Progress = Callable[[str], None]
RR_TEST_RE = re.compile(r"^rr_test_\d+_(?P<line>.+)$", re.IGNORECASE)

# These are deliberately exact folder fallbacks. Most voicelines must continue
# to identify their speaker in the filename; only known historical layouts may
# use directory ownership when that parse fails.
SPECIFIC_VOICE_FOLDER_FALLBACKS: dict[str, tuple[str, tuple[str, ...]]] = {
    "book/oathkeeper": ("oathkeeper", ("vn_geist",)),
    "neutral_gremlin": ("neutral_gremlin", ("neutral_gremlin",)),
    "announcer/count_up": ("announcer_count_up", ()),
    "announcer/female_patron": ("patron_female", ("patron_female",)),
    "announcer/male_patron": ("patron_male", ("patron_male",)),
    "npc_reporter": ("newscaster", ("newscaster", "npc_reporter")),
    "shopkeeper": ("shopkeeper_hotdog", ("shopkeeper_hotdog",)),
    "dynamo": ("dynamo", ("dynamo", "prof")),
    "nano": ("calico", ("calico", "nano")),
}
GUARDIAN_FOLDER_RE = re.compile(
    r"^t1_guardians/(?P<speaker>guardian_test_0[1-4])$",
    re.IGNORECASE,
)


SELF_KEYWORDS = (
    "storm_cloud_kelvin_survives",
    "storm_cloud_last_standing",
    "no_allies_help_blackhole",
    "see_enemy_use_metal_skin",
    "die_trade_in_storm_cloud",
    "asleep_killstreak_start",
    "storm_cloud_1_survives",
    "asleep_killstreak_high",
    "boost_past_on_zipline",
    "storm_cloud_team_wipe",
    "asleep_killstreak_mid",
    "asleep_upgrade_power1",
    "asleep_upgrade_power2",
    "asleep_upgrade_power3",
    "asleep_upgrade_power4",
    "bespoke_ability_line",
    "see_enemy_metal_skin",
    "catch_team_blackhole",
    "bad_dome_rejuvinator",
    "massive_ground_pound",
    "asleep_kill_anyhereo",
    "monster_kill_anyhero",
    "use_power4_as_enemy",
    "kill_team_blackhole",
    "asleep_kill_anyhero",
    "die_mid_storm_cloud",
    "low_health_warning",
    "desperation_power1",
    "desperation_power2",
    "desperation_power3",
    "desperation_power4",
    "desperation_power5",
    "desperation_power6",
    "kill_high_networth",
    "nano_kills_turrets",
    "monster_killstreak",
    "last_one_standing",
    "allies_lasso_kill",
    "sticky_bomb_invis",
    "uppercut_to_titan",
    "asleep_use_power1",
    "asleep_use_power3",
    "asleep_use_power4",
    "monster_idol_drop",
    "killstreak_start",
    "enemy_gets_rejuv",
    "t1_shop_reminder",
    "t2_shop_reminder",
    "t3_shop_reminder",
    "t4_shop_reminder",
    "tower_got_denied",
    "repeat_blackhole",
    "allies_no_attack",
    "power2_resurface",
    "hook_gig_mid_ult",
    "emote_pain_small",
    "emote_pain_death",
    "killstreak_high",
    "solo_lasso_kill",
    "high_max_health",
    "dome_enemy_core",
    "ult_interrupted",
    "asleep_congrats",
    "lots_of_turrets",
    "killstreak_mid",
    "upgrade_power1",
    "upgrade_power2",
    "upgrade_power3",
    "upgrade_power4",
    "upgrade_power5",
    "upgrade_power6",
    "bad_dome_alone",
    "win_with_bebop",
    "uppercut_to_t1",
    "uppercut_to_t2",
    "ult_last_alive",
    "ult_total_miss",
    "emote_pain_big",
    "monster_power1",
    "monster_power2",
    "monster_power3",
    "out_of_stamina",
    "pick_up_rejuv",
    "dome_own_core",
    "melee_efforts",
    "upgrade_power",
    "leaving_area",
    "pick_up_gold",
    "revenge_kill",
    "kill_anyhero",
    "heal_grenade",
    "sell_upgrade",
    "low_networth",
    "outnumbered",
    "start_match",
    "ap_reminder",
    "dash_effort",
    "close_call",
    "leave_base",
    "melee_kill",
    "use_power1",
    "use_power2",
    "use_power3",
    "use_power4",
    "be_careful",
    "end_streak",
    "lose_early",
    "hook_lands",
    "multi_dash",
    "idol_score",
    "max_knives",
    "empty_heal",
    "concerned",
    "interrupt",
    "near_miss",
    "see_money",
    "hs_select",
    "lose_late",
    "win_early",
    "idol_drop",
    "idol_grab",
    "unselect",
    "congrats",
    "win_late",
    "respawn",
    "efforts",
    "select",
    "angry",
    "happy",
    "parry",
    "snarl",
    "hunt",
    "lose",
    "howl",
    "vote",
    "sad",
    "win",
)


def _strip_topic_variations(value: str, *, compact_alt: bool = False) -> str:
    """Strip repeated suffixes; compact alt01 is accepted only by subject topics."""
    pattern = r"_alt_\d+$|_\d+_alt$|_\d+$"
    if compact_alt:
        pattern += r"|_alt\d+$"
    while match := re.search(pattern, value):
        value = value[: match.start()]
    return value


def _clean_ping_subject(value: str) -> str:
    for pattern in (r"_alt(_\d+)?$", r"_alt\d+$", r"_\d+_alt$", r"_\d+$"):
        value = re.sub(pattern, "", value)
    return re.sub(r"_old$", "", value, flags=re.IGNORECASE)


def _clean_ping_topic(value: str) -> str:
    for pattern in (r"_alt(_\d+)?$", r"_alt\d+$", r"_\d+$"):
        value = re.sub(pattern, "", value)
    return value


def _is_game_ping(value: str) -> bool:
    parts = value.split("_")
    return (
        len(parts) == 3
        and parts[0] in {"pre", "post"}
        and parts[1] == "game"
        and parts[2].isdigit()
    )


def _parse_ping(value: str, valid_speakers: set[str]) -> tuple[str, str]:
    parts = value.split("_")
    # SEE places the subject after the event; other pings prefer a leading alias.
    if len(parts) >= 2 and parts[0] == "see":
        for end in range(len(parts), 1, -1):
            subject = re.sub(r"_old$", "", "_".join(parts[1:end]), flags=re.IGNORECASE)
            if subject.lower() in valid_speakers:
                suffix = "_".join(parts[end:])
                topic = "see" + (f"_{suffix}" if suffix else "")
                return _clean_ping_topic(subject), _clean_ping_topic(topic)
    for end in range(len(parts), 0, -1):
        subject = _clean_ping_subject("_".join(parts[:end]))
        if subject.lower() in valid_speakers:
            return _clean_ping_topic(subject), _clean_ping_topic("_".join(parts[end:]))
    for start in range(1, len(parts)):
        subject = _clean_ping_subject("_".join(parts[start:]))
        if subject.lower() in valid_speakers:
            return _clean_ping_topic(subject), _clean_ping_topic(
                "_".join(parts[:start])
            )
    return "self", _clean_ping_topic(value)


class VoicelineParser:
    """Classify filenames and place their records into configured display groups."""

    def __init__(
        self, audio_root: Path, group_config: dict, report: Callable[[str], None]
    ):
        self.audio_root = audio_root
        self.group_config = group_config
        self.report = report
        self.unresolved_aliases: set[str] = set()

    def parse_file(
        self,
        file_path: str,
        alias_data: dict[str, list[str]],
        topic_alias_data: dict,
        valid_speakers: set[str],
    ) -> tuple[str, str, str, str | None, str, bool] | None:
        try:
            filename = os.path.basename(file_path)
            filename_without_ext = os.path.splitext(filename)[0]
            rel_path = os.path.relpath(file_path, self.audio_root)

            # Special handling for spirit_jar
            if filename_without_ext.startswith("spirit_jar_"):
                parsed = self._parse_spirit_jar(
                    filename_without_ext, alias_data, valid_speakers, rel_path
                )
                if parsed is not None:
                    return parsed

            # Special handling for newscaster
            if filename_without_ext.startswith("newscaster_"):
                parsed = self._parse_newscaster(
                    filename_without_ext, alias_data, valid_speakers, rel_path
                )
                if parsed is not None:
                    return parsed

            # Special handling for shopkeeper_hotdog
            if filename_without_ext.startswith("shopkeeper_hotdog_"):
                parsed = self._parse_shopkeeper(
                    filename_without_ext, alias_data, valid_speakers, rel_path
                )
                if parsed is not None:
                    return parsed

            # Special handling for patron_female and patron_male
            if filename_without_ext.startswith(("patron_female_", "patron_male_")):
                parsed = self._parse_patron(
                    filename_without_ext, alias_data, valid_speakers, rel_path
                )
                if parsed is not None:
                    return parsed

            return self._parse_standard(
                filename_without_ext,
                alias_data,
                topic_alias_data,
                valid_speakers,
                rel_path,
            )

        except (IndexError, ValueError, TypeError, OSError) as exc:
            self.report(f"Could not parse {file_path}: {exc}")
        return None

    def _parse_standard(
        self,
        filename_without_ext,
        alias_data,
        topic_alias_data,
        valid_speakers,
        rel_path,
    ):

        # Extract speaker - try multi-part names first (e.g., "magician_henry")
        parts_initial = filename_without_ext.split("_")
        speaker = parts_initial[0] if len(parts_initial) > 1 else filename_without_ext

        # Try to match longer speaker names (e.g., "magician_henry" instead of just "magician")
        if len(parts_initial) >= 2:
            candidate = f"{parts_initial[0]}_{parts_initial[1]}"
            if candidate.lower() in valid_speakers:
                speaker = candidate

        is_ping = False
        is_self = False
        fallback_used = False
        joined = ""
        matched_self_keyword = None
        rest = None
        # Determine how many parts were used for the speaker name
        speaker_parts_count = len(speaker.split("_"))
        if len(parts_initial) > speaker_parts_count:
            joined = "_".join(parts_initial[speaker_parts_count:])
            for kw in SELF_KEYWORDS:
                if joined == kw:
                    matched_self_keyword = kw
                    break
                if joined.startswith(kw + "_"):
                    suffix = joined[len(kw) + 1 :]
                    # Accept sequences of digits, alt(_digits), short, or single letter (a-z), including combos like 02_a or 13_alt_01
                    if re.fullmatch(
                        r"(?:\d+|alt(?:_\d+)?|short|[a-z])(?:_(?:\d+|alt(?:_\d+)?|short|[a-z]))*",
                        suffix,
                    ):
                        matched_self_keyword = kw
                        break
        if matched_self_keyword:
            # Handle self voiceline: [speaker]_[keyword][_variation]
            relationship = None
            rest = joined
            is_self = True
        # Subject-at-end historical patterns. Keep these explicit instead
        # of treating every unknown remainder as a Self topic.
        elif joined.startswith(("killed_by_", "assisted_by_")):
            event = "killed_by" if joined.startswith("killed_by_") else "assisted_by"
            subject_alias = joined[len(event) + 1 :]
            subject_alias = re.sub(r"_alt_\d+$", "", subject_alias)
            subject_alias = re.sub(r"_(\d+)_alt$", "", subject_alias)
            subject_alias = re.sub(r"_(\d+)$", "", subject_alias)
            if subject_alias.lower() in valid_speakers:
                relationship = None
                rest = f"{subject_alias}_{event}"
            else:
                relationship = None
                rest = joined
        # Prefix-based self voiceline detection
        elif len(parts_initial) > 1 and joined.startswith(("use_", "effort_", "pain_")):
            relationship = None
            rest = joined
            is_self = True
        # Special pattern: sleepy_use_power_{character} -> treat as enemy voiceline
        elif joined.startswith("sleepy_use_power_") and len(joined.split("_")) >= 4:
            # Extract character name (last part before any variations)
            sleepy_parts = joined.split("_")
            # Character name starts at index 3 (after "sleepy_use_power")
            char_and_rest = "_".join(sleepy_parts[3:])
            # Remove variations to find character
            char_clean = re.sub(r"_alt_\d+$", "", char_and_rest)
            char_clean = re.sub(r"_alt$", "", char_clean)
            char_clean = re.sub(r"_(\d+)$", "", char_clean)
            if char_clean.lower() in valid_speakers:
                # Reformat as enemy pattern
                relationship = "enemy"
                rest = f"{char_clean}_sleepy_use_power"
                # Add back variations if they existed
                if char_and_rest != char_clean:
                    variation_part = char_and_rest[len(char_clean) :]
                    rest += variation_part
            else:
                # If not a valid character, treat as self voiceline
                relationship = None
                rest = joined
                is_self = True
        # Special pattern: asleep_ping_ or sleepy_ping_ -> reformat to standard ping
        elif joined.startswith(("asleep_ping_", "sleepy_ping_")):
            # Strip the state prefix to get standard ping format
            if joined.startswith("asleep_ping_"):
                rest = joined[len("asleep_ping_") :]
            else:  # sleepy_ping_
                rest = joined[len("sleepy_ping_") :]
            relationship = None
            is_self = _is_game_ping(rest)
            is_ping = not is_self
        # Ping topics can contain "enemy"; only split _ping_ after a valid speaker.
        # This also keeps enemy topics such as ghost_ping_with_swap intact.
        elif (
            "_ping_" in filename_without_ext
            and filename_without_ext.split("_ping_", 1)[0].lower() in valid_speakers
        ):
            # Handle ping pattern: [speaker]_ping_[topic][_subject][_variation]
            parts = filename_without_ext.split("_ping_", 1)
            relationship = None
            speaker = parts[0]
            rest = parts[1]
            is_self = _is_game_ping(rest)
            is_ping = not is_self
        elif "_ally_" in filename_without_ext:
            relationship = "ally"
            parts = filename_without_ext.split("_ally_", 1)
            speaker = parts[0]
            rest = parts[1]
        elif "_enemy_" in filename_without_ext:
            relationship = "enemy"
            parts = filename_without_ext.split("_enemy_", 1)
            speaker = parts[0]
            rest = parts[1]
        elif "_bespoke_" in filename_without_ext:
            relationship = None
            parts = filename_without_ext.split("_bespoke_", 1)
            speaker = parts[0] + "_bespoke"
            rest = parts[1]
        else:
            fallback_parts = parts_initial
            if len(fallback_parts) >= 4:
                speaker = fallback_parts[0]

                def _strip_variation(tokens):
                    """
                    Remove trailing variation tokens – digits, 'short', 'alt', 'alt##', etc.
                    and return the cleaned token list.
                    """
                    while tokens and (
                        re.fullmatch(r"\d+", tokens[-1])
                        or tokens[-1] == "short"
                        or tokens[-1].startswith("alt")
                    ):
                        tokens = tokens[:-1]
                    return tokens

                # Case 1: filename contains explicit '_on_' separator
                if "on" in fallback_parts[1:]:
                    on_idx = fallback_parts.index("on", 1)
                    topic_raw = (
                        "_".join(fallback_parts[1:on_idx])
                        if on_idx > 1
                        else fallback_parts[1]
                    )

                    subject_tokens = _strip_variation(fallback_parts[on_idx + 1 :])
                    subject = "_".join(subject_tokens) if subject_tokens else "self"

                    # Prefer the longest candidate that matches a known hero alias
                    for i in range(len(subject_tokens), 0, -1):
                        candidate = "_".join(subject_tokens[:i])
                        if candidate.lower() in valid_speakers:
                            subject = candidate
                            break

                # Case 2: classic speaker_topic_subject pattern
                else:
                    topic_raw = fallback_parts[1]
                    subject_tokens = _strip_variation(fallback_parts[2:])
                    subject = (
                        "_".join(subject_tokens)
                        if subject_tokens
                        else fallback_parts[2]
                    )

                    for i in range(len(subject_tokens), 0, -1):
                        candidate = "_".join(subject_tokens[:i])
                        if candidate.lower() in valid_speakers:
                            subject = candidate
                            break

                relationship = None
                is_ping = False
                is_self = False
                rest = "_".join(
                    fallback_parts[1:]
                )  # keeps any trailing _short / _01 etc.
                fallback_used = True
            else:
                return None

        # Check if speaker is valid
        if speaker.lower() not in valid_speakers:
            self.unresolved_aliases.add(speaker.capitalize())
            return None

        # Preserve precedence for alt, double-number, and single-number endings.
        variation = re.search(r"_alt_\d+$|_\d+_alt$|_\d+_\d+$|_\d+$", rest)
        rest_without_variation = rest[: variation.start()] if variation else rest

        # For bespoke lines, the pattern is topic_subject
        if "_bespoke" in speaker:
            bespoke_parts = rest_without_variation.split("_")
            if len(bespoke_parts) >= 2:
                topic_raw = "_".join(bespoke_parts[:-1])
                subject = bespoke_parts[-1]
            else:
                return None
        elif is_ping:
            subject, topic_raw = _parse_ping(rest_without_variation, valid_speakers)
        elif is_self:
            topic_raw = _strip_topic_variations(rest)
            subject = "self"
        elif not fallback_used:
            # Resolve the longest known leading subject. This keeps
            # multi-part aliases such as grey_talon and the_boss intact.
            subject_tokens = rest_without_variation.split("_")
            subject = None
            topic_candidate = None
            for index in range(len(subject_tokens) - 1, 0, -1):
                candidate = "_".join(subject_tokens[:index])
                if candidate.lower() in valid_speakers:
                    subject = candidate
                    topic_candidate = "_".join(subject_tokens[index:])
                    break
            if subject is None or not topic_candidate:
                return None
            topic_raw = _strip_topic_variations(topic_candidate, compact_alt=True)

        # Check if subject is a valid hero name, except for "self"
        if subject != "self" and subject.lower() not in valid_speakers:
            self.unresolved_aliases.add(subject.capitalize())
            return None

        speaker_proper = self._get_proper_name(speaker, alias_data)
        subject_proper = self._get_proper_name(subject, alias_data)

        topic_proper = self.format_topic(topic_raw, topic_alias_data)
        # Replace underscores with spaces and capitalize first character
        topic_proper = topic_proper.replace("_", " ").capitalize()
        if relationship in ("ally", "enemy"):
            topic_proper = f"{topic_proper} ({relationship})"

        return (
            speaker_proper,
            subject_proper,
            topic_proper,
            relationship,
            rel_path,
            is_ping,
        )

    @staticmethod
    def _get_proper_name(alias: str, alias_data: dict[str, list[str]]) -> str:
        for proper_name, aliases in alias_data.items():
            if isinstance(aliases, list) and any(
                alias.lower() == item.lower() for item in aliases
            ):
                return proper_name
        return alias.capitalize()

    @staticmethod
    def format_topic(topic_raw: str, topic_alias_data: dict) -> str:
        if topic_raw.startswith("ping"):
            return f"ping_{topic_raw.replace('ping', '')}"

        for proper_topic, aliases in topic_alias_data.items():
            if isinstance(aliases, list) and any(
                topic_raw.lower() == item.lower() for item in aliases
            ):
                return proper_topic

        return topic_raw.capitalize()

    @staticmethod
    def _item_filename(item):
        if isinstance(item, dict):
            return str(item.get("filename") or "")
        return os.path.basename(str(item))

    @staticmethod
    def _append_grouped(container, path, topic, item):
        target = container
        for label in path:
            target = target.setdefault(label, {})
        target.setdefault(topic, []).append(item)

    def _place_topic(self, container, scope, topic_key, topic, item):
        path = classify_topic(
            self.group_config,
            scope,
            topic_key,
            self._item_filename(item),
        )
        if path:
            self._append_grouped(container, path, topic, item)
        else:
            container.setdefault(topic, []).append(item)

    def place_in_result(self, result_data, result, item):
        speaker, subject, topic, _relationship, _rel_path, is_ping = result

        if speaker not in result_data:
            result_data[speaker] = {}

        subject_key = subject.capitalize() if subject.lower() == "self" else subject
        if subject_key not in result_data[speaker]:
            result_data[speaker][subject_key] = {}

        if is_ping:
            ping_root = self.group_config["pingRoot"]
            topic_key = topic.replace(" ", "_").lower()
            pings = result_data[speaker][subject_key].setdefault(ping_root, {})
            self._place_topic(pings, "ping", topic_key, topic, item)

            # Duplicate a self-addressed ping under Self for compatibility.
            if subject_key.lower() != "self" and subject_key.lower() == speaker.lower():
                self_topics = result_data[speaker].setdefault("Self", {})
                self_pings = self_topics.setdefault(ping_root, {})
                self._place_topic(self_pings, "ping", topic_key, topic, item)
            return

        # Relationship suffixes are part of the classification key. Ally and
        # enemy reactions can intentionally route to separate display groups.
        topic_key = topic.replace(" ", "_").lower()
        self._place_topic(
            result_data[speaker][subject_key],
            "voice",
            topic_key,
            topic,
            item,
        )

    def _parse_spirit_jar(
        self, filename_without_ext, alias_data, valid_speakers, rel_path
    ):
        speaker = "spirit_jar"
        # Everything after "spirit_jar_"
        base = filename_without_ext[len("spirit_jar_") :]
        # Remove trailing _alt_<number> or _<number>
        base_clean = re.sub(r"_alt_\d+$", "", base)
        base_clean = re.sub(r"_(\d+)$", "", base_clean)

        # Character-addressed urn lines use the form
        # spirit_jar_holder_stalls_<character>_<variation>.
        holder_stalls_prefix = "holder_stalls_"
        if base_clean.startswith(holder_stalls_prefix):
            subject_alias = base_clean[len(holder_stalls_prefix) :]
            if subject_alias.lower() in valid_speakers:
                subject = self._get_proper_name(subject_alias, alias_data)
                topic_proper = "Holder stalls"
                return (speaker, subject, topic_proper, None, rel_path, False)

        # Replace underscores with spaces and capitalize first letter
        subject = base_clean.replace("_", " ").capitalize()
        topic_proper = subject
        return (speaker, "self", topic_proper, None, rel_path, False)

    def _parse_newscaster(
        self, filename_without_ext, alias_data, valid_speakers, rel_path
    ):
        base = filename_without_ext[len("newscaster_") :]
        # Remove trailing _alt_<number> or _<number>
        base_clean = re.sub(r"_alt_\d+$", "", base)
        base_clean = re.sub(r"_(\d+)$", "", base_clean)
        parts = base_clean.split("_")
        speaker = "newscaster"
        # newscaster_headline_01 or newscaster_headline_01_alt_01
        if parts[0] == "headline":
            return (speaker, "self", "Headline", None, rel_path, False)
        # newscaster_seasonal_headline_05 or newscaster_seasonal_headline_06_alt_01
        if parts[0] == "seasonal" and parts[1] == "headline":
            return (speaker, "self", "Seasonal headline", None, rel_path, False)
        # newscaster_seasonal_{character}_unlock_01
        if parts[0] == "seasonal" and len(parts) >= 3 and parts[2] == "unlock":
            subject = self._get_proper_name(parts[1], alias_data)
            topic_proper = "Seasonal unlock"
            return (speaker, subject, topic_proper, None, rel_path, False)

    def _parse_shopkeeper(
        self, filename_without_ext, alias_data, valid_speakers, rel_path
    ):
        base = filename_without_ext[len("shopkeeper_hotdog_") :]
        # Remove trailing _alt_<number> or _<number>
        base_clean = re.sub(r"_alt_\d+$", "", base)
        base_clean = re.sub(r"_(\d+)$", "", base_clean)
        parts = base_clean.split("_")
        speaker = "shopkeeper_hotdog"
        # seasonal t4 lines: shopkeeper_hotdog_seasonal_t4_{character}_...
        if parts[0] == "seasonal" and len(parts) >= 3 and parts[1] == "t4":
            # Try to match multi-part character names (e.g., magician_henry)
            subject = None
            character_parts_count = 1
            if len(parts) >= 4:
                # Try two-part character name first
                two_part = f"{parts[2]}_{parts[3]}".lower()
                if two_part in valid_speakers:
                    subject = self._get_proper_name(two_part, alias_data)
                    character_parts_count = 2
            if subject is None:
                # Fall back to single-part character name
                subject = self._get_proper_name(parts[2], alias_data)
            topic_proper = "Seasonal"
            return (speaker, subject, topic_proper, None, rel_path, False)
        # t4 lines: shopkeeper_hotdog_t4_{character}_...
        if parts[0] == "t4" and len(parts) >= 2:
            # Try to match multi-part character names (e.g., magician_henry)
            subject = None
            character_parts_count = 1
            if len(parts) >= 3:
                # Try two-part character name first
                two_part = f"{parts[1]}_{parts[2]}".lower()
                if two_part in valid_speakers:
                    subject = self._get_proper_name(two_part, alias_data)
                    character_parts_count = 2
            if subject is None:
                # Fall back to single-part character name
                subject = self._get_proper_name(parts[1], alias_data)
            topic = "t4"
            # The rest after character
            remaining_start = 1 + character_parts_count
            if len(parts) > remaining_start:
                topic_rest = "_".join(parts[remaining_start:])
                topic_proper = topic_rest.replace("_", " ").capitalize()
            else:
                topic_proper = ""
            return (
                speaker,
                subject,
                f"{topic} {topic_proper}".strip(),
                None,
                rel_path,
                False,
            )
        # buy lines: shopkeeper_hotdog_buy_...
        if parts[0] == "buy":
            subject = "self"
            if len(parts) > 1:
                topic_proper = "Buy " + " ".join(parts[1:]).replace("_", " ")
            else:
                topic_proper = "Buy"
            return (speaker, subject, topic_proper, None, rel_path, False)
        # call_out, close_shop, open_spirit, etc.
        subject = "self"
        topic_proper = " ".join(parts).replace("_", " ").capitalize()
        return (speaker, subject, topic_proper, None, rel_path, False)

    def _parse_patron(self, filename_without_ext, alias_data, valid_speakers, rel_path):
        speaker = (
            "patron_female"
            if filename_without_ext.startswith("patron_female_")
            else "patron_male"
        )
        base = filename_without_ext[
            len(speaker) + 1 :
        ]  # Remove "patron_female_" or "patron_male_"

        # Remove trailing variations
        base_clean = re.sub(r"_alt_\d+$", "", base)
        base_clean = re.sub(r"_(\d+)_alt$", "", base_clean)
        base_clean = re.sub(r"_alt$", "", base_clean)  # Handle _alt without number
        base_clean = re.sub(r"_(\d+)$", "", base_clean)

        parts = base_clean.split("_")

        # Character-based patterns: {topic}_by_{character}. Match the
        # longest suffix so multi-part aliases such as the_boss and
        # grey_talon remain one subject.
        if "by" in parts[1:]:
            by_index = len(parts) - 1 - parts[::-1].index("by")
            subject_alias = "_".join(parts[by_index + 1 :])
            if subject_alias.lower() in valid_speakers:
                subject = self._get_proper_name(subject_alias, alias_data)
                topic_proper = " ".join(parts[:by_index]).replace("_", " ").capitalize()
                return (speaker, subject, topic_proper, None, rel_path, False)

        # help_out_{character}
        if len(parts) >= 3 and parts[0] == "help" and parts[1] == "out":
            subject = self._get_proper_name(parts[2], alias_data)
            topic_proper = "Help out"
            return (speaker, subject, topic_proper, None, rel_path, False)

        # praise_{character}
        if len(parts) >= 2 and parts[0] == "praise":
            subject_alias = "_".join(parts[1:])
            subject = self._get_proper_name(subject_alias, alias_data)
            topic_proper = "Praise"
            return (speaker, subject, topic_proper, None, rel_path, False)

        # For ally/enemy patterns
        if parts[0] in ["ally", "enemy"]:
            # Try to find a character name starting at index 1
            # Check from longest possible name down to 1 word (to handle multi-word names like "grey talon")
            for i in range(len(parts), 1, -1):
                candidate_parts = parts[1:i]
                candidate_name_spaces = " ".join(candidate_parts)

                if candidate_name_spaces in valid_speakers:
                    subject = self._get_proper_name(candidate_name_spaces, alias_data)

                    # Topic is the rest
                    topic_parts = parts[i:]
                    if topic_parts:
                        topic_proper = (
                            " ".join(topic_parts).replace("_", " ").capitalize()
                        )
                    else:
                        # Just "Ally" or "Enemy" (unlikely alone but safe fallback)
                        topic_proper = parts[0].capitalize()

                    return (speaker, subject, topic_proper, None, rel_path, False)

            # If no character found, treat as self with full topic
            # patron_female_ally_blue_guardian_destroyed_01 -> self / "Ally blue guardian destroyed"
            # patron_female_enemy_core_exposed_01 -> self / "Enemy core exposed"
            subject = "self"
            topic_proper = " ".join(parts).replace("_", " ").capitalize()
            return (speaker, subject, topic_proper, None, rel_path, False)

        # bespoke_ally_{character} or bespoke_ally_{topic}_{character}
        if len(parts) >= 3 and parts[0] == "bespoke" and parts[1] == "ally":
            # Check if the last part is a valid character (prefer that as subject)
            if parts[-1].lower() in valid_speakers:
                subject = self._get_proper_name(parts[-1], alias_data)
                if len(parts) > 3:
                    topic_proper = " ".join(parts[2:-1]).replace("_", " ").capitalize()
                else:
                    topic_proper = "Bespoke ally"
            else:
                # Fallback to old behavior if last part isn't a character
                subject = self._get_proper_name(parts[2], alias_data)
                topic_proper = "Bespoke ally " + " ".join(parts[3:]).replace("_", " ")
                topic_proper = topic_proper.strip().capitalize()
            return (speaker, subject, topic_proper, None, rel_path, False)

        # bespoke_enemy_{character} or bespoke_enemy_{topic}_{character}
        if (
            len(parts) >= 3
            and parts[0] == "bespoke"
            and parts[1] == "enemy"
            and parts[-1].lower() in valid_speakers
        ):
            subject = self._get_proper_name(parts[-1], alias_data)
            if len(parts) > 3:
                topic_proper = " ".join(parts[2:-1]).replace("_", " ").capitalize()
            else:
                topic_proper = "Bespoke enemy"
            return (speaker, subject, topic_proper, None, rel_path, False)

        # bespoke_for_{character}
        if len(parts) >= 3 and parts[0] == "bespoke" and parts[1] == "for":
            candidate = "_".join(parts[2:])
            if candidate.lower() in valid_speakers:
                subject = self._get_proper_name(candidate, alias_data)
                topic_proper = "Bespoke for"
                return (speaker, subject, topic_proper, None, rel_path, False)

        # All other patron voicelines are self voicelines
        subject = "self"
        topic_proper = " ".join(parts).replace("_", " ").capitalize()
        return (speaker, subject, topic_proper, None, rel_path, False)


def _strip_historical_variation(value: str) -> str:
    result = value
    while True:
        updated = re.sub(r"_alt_\d+$", "", result, flags=re.IGNORECASE)
        updated = re.sub(r"_\d+_alt$", "", updated, flags=re.IGNORECASE)
        updated = re.sub(r"_\d+$", "", updated)
        if updated == result:
            return result
        result = updated


def _specific_folder_voiceline_fallback(
    relative_path: Path,
    parser: VoicelineParser,
    alias_lookup: dict[str, str],
    topic_aliases: dict[str, object],
) -> tuple[str, str, str, None, str, bool] | None:
    """Parse only explicitly approved historical folders after normal parsing fails."""
    folder = relative_path.parent.as_posix().casefold()
    # parse_voicelines normally receives the extracted sounds/vo directory.
    # Accept an Audio-root-relative path too so direct callers and fixtures
    # use the same exact whitelist.
    folder = folder.removeprefix("sounds/vo/")
    if folder == "newscaster":
        stem = relative_path.stem.casefold()
        if stem.startswith("guide_"):
            topic = "Guide"
        elif stem == "news_reel_test":
            topic = "News reel test"
        else:
            return None
        return "newscaster", "self", topic, None, relative_path.as_posix(), False
    guardian_match = GUARDIAN_FOLDER_RE.fullmatch(folder)
    if guardian_match:
        speaker = guardian_match.group("speaker").casefold()
        prefix = f"rr_{speaker}_"
        body = relative_path.stem
        if body.casefold().startswith(prefix):
            body = body[len(prefix) :]
        body = _strip_historical_variation(body)
        tokens = body.split("_") if body else []
        subject = "self"
        topic_raw = body
        for index in range(len(tokens)):
            candidate = "_".join(tokens[index:]).casefold()
            canonical = alias_lookup.get(candidate)
            if canonical:
                subject = canonical
                topic_raw = "_".join(tokens[:index]) or "general"
                break
        topic = (
            parser.format_topic(topic_raw, topic_aliases).replace("_", " ").capitalize()
        )
        return speaker, subject, topic, None, relative_path.as_posix(), False

    fallback = SPECIFIC_VOICE_FOLDER_FALLBACKS.get(folder)
    if not fallback:
        return None
    speaker, removable_prefixes = fallback
    body = relative_path.stem
    for prefix in removable_prefixes:
        marker = prefix + "_"
        if body.casefold().startswith(marker.casefold()):
            body = body[len(marker) :]
            break
    topic_raw = _strip_historical_variation(body) or "general"
    topic = parser.format_topic(topic_raw, topic_aliases).replace("_", " ").capitalize()
    return speaker, "self", topic, None, relative_path.as_posix(), False


def _load_vdf(path: Path | None) -> dict[str, str]:

    return load_vdf_key_text_map(str(path)) if path else {}


def _materialize_voicelines(
    node: object,
    audio_dir: Path,
    vdf: dict[str, str],
    audio_filename_overrides: dict[str, str | None],
) -> object:

    if isinstance(node, dict):
        # Phantom lines are already materialized records.  Do not interpret
        # their transcript and ID strings as filesystem paths.
        if isinstance(node.get("filename"), str):
            return dict(node)
        return {
            key: _materialize_voicelines(
                value, audio_dir, vdf, audio_filename_overrides
            )
            for key, value in node.items()
        }
    if isinstance(node, list):
        return [
            _materialize_voicelines(value, audio_dir, vdf, audio_filename_overrides)
            for value in node
        ]
    if not isinstance(node, str):
        return node
    relative = Path(node)
    audio_path = audio_dir.joinpath(*relative.parts)
    audio_key = relative.as_posix()
    filename = relative.name
    effective = effective_audio_path(relative, audio_filename_overrides)
    parse_filename = effective.name if effective is not None else filename
    try:
        # Historical exports use the operator's local calendar date.
        date = datetime.fromtimestamp(audio_path.stat().st_mtime).strftime("%Y-%m-%d")  # noqa: DTZ006
    except OSError:
        date = None
    _vdf_key, official_text = find_vdf_match_for_filename(parse_filename, vdf)
    entry: dict[str, object] = {
        # filename is the key relative to audioBaseUrl.  Folder components are
        # required because Source 2 can contain different recordings with the
        # same basename in different voice folders.
        "filename": audio_key,
        "date": date,
        "voiceline_id": Path(filename).stem,
        "transcription": official_text or "",
    }
    if official_text:
        entry["officialtranscription"] = True
    return entry


def _normalize_shopkeeper_topics(result: dict[str, object]) -> None:
    """Build the compact display hierarchy used by the shopkeeper archive."""
    speaker = result.get("shopkeeper_hotdog")
    if not isinstance(speaker, dict):
        return
    topics = speaker.get("Self")
    if not isinstance(topics, dict):
        return

    shop_system = topics.pop("Shop System", {})
    if not isinstance(shop_system, dict):
        shop_system = {}
    call_out_ten = topics.pop("Call out 10", [])
    if isinstance(call_out_ten, list):
        shop_system.setdefault("Call out", []).extend(call_out_ten)

    buy: dict[str, object] = {}
    seasonal: dict[str, object] = {}
    guide: list[object] = []
    hero_training: list[object] = []
    remaining: dict[str, object] = {}
    for label, value in topics.items():
        if label.startswith("Buy "):
            buy[label[len("Buy ") :].capitalize()] = value
        elif label.startswith("Seasonal "):
            seasonal[label[len("Seasonal ") :].capitalize()] = value
        elif label == "Guide" or label.startswith("Guide "):
            if isinstance(value, list):
                guide.extend(value)
        elif label == "Hero training" or label.startswith("Hero training "):
            if isinstance(value, list):
                hero_training.extend(value)
        else:
            remaining[label] = value

    ordered: dict[str, object] = {}
    if shop_system:
        ordered["Shop System"] = shop_system
    if buy:
        ordered["Buy"] = buy
    if guide:
        ordered["Guide"] = guide
    if hero_training:
        ordered["Hero Training"] = hero_training
    if seasonal:
        ordered["Seasonal"] = seasonal
    ordered.update(remaining)
    speaker["Self"] = ordered


def parse_voicelines(
    audio_dir: Path,
    character_mappings: Path,
    topic_aliases: Path,
    voiceline_groups: Path,
    vdf_path: Path | None,
    include_phantom: bool,
    progress: Progress,
    audio_filename_overrides: dict[str, str | None] | None = None,
) -> tuple[dict[str, object], set[str]]:

    alias_data = validate_mapping(character_mappings)
    topic_data = read_json(topic_aliases)
    if not isinstance(topic_data, dict):
        raise VpkPipelineError(
            f"Topic aliases must contain a JSON object: {topic_aliases}"
        )
    group_config = load_group_config(voiceline_groups)
    valid_speakers = {
        alias.casefold() for aliases in alias_data.values() for alias in aliases
    }
    alias_lookup = alias_index(alias_data)
    filename_overrides = audio_filename_overrides or {}
    parser = VoicelineParser(audio_dir, group_config, progress)

    audio_files: list[tuple[Path, Path, Path]] = []
    for path in sorted(audio_dir.rglob("*")):
        if not path.is_file() or path.suffix.casefold() != ".mp3":
            continue
        relative_path = path.relative_to(audio_dir)
        effective_path = effective_audio_path(relative_path, filename_overrides)
        if (
            effective_path is None
            or conversation_key_from_name(effective_path.name, {}) is not None
        ):
            continue
        audio_files.append((path, relative_path, effective_path))
    result: dict[str, object] = {}
    vdf = _load_vdf(vdf_path)
    used_vdf: set[str] = set()
    legacy_count = 0
    folder_fallback_count = 0
    for index, (path, relative_path, effective_path) in enumerate(audio_files, start=1):
        parse_path = audio_dir.joinpath(*effective_path.parts)
        legacy_match = RR_TEST_RE.fullmatch(effective_path.stem)
        legacy_speaker: str | None = None
        if legacy_match and len(relative_path.parts) > 1:
            # Very old builds used a numeric rr_test prefix instead of putting
            # the speaker in the filename. The first voice-folder component is
            # the stable speaker alias (for example kali -> vyper).
            speaker_alias = relative_path.parts[0].casefold()
            if speaker_alias not in alias_lookup:
                # Preserve an unknown historical character under its readable
                # folder name. The operator can rename it later in the per-game
                # character mapping JSON without losing the recording.
                alias_data[speaker_alias] = [speaker_alias]
                alias_lookup[speaker_alias] = speaker_alias
                valid_speakers.add(speaker_alias)
            legacy_speaker = alias_lookup[speaker_alias]
            parse_path = path.with_name(
                f"{speaker_alias}_{legacy_match.group('line')}{path.suffix}"
            )

        unresolved_before = set(parser.unresolved_aliases)
        parsed = parser.parse_file(
            str(parse_path), alias_data, topic_data, valid_speakers
        )
        if legacy_match and legacy_speaker and parsed is None:
            # Some rr_test event names predate the current speaker/subject
            # grammar. Keep them as readable Self topics instead of dropping
            # historical audio merely because its old category is unknown.
            parser.unresolved_aliases = unresolved_before
            topic_raw = legacy_match.group("line")
            topic_raw = re.sub(r"_alt_\d+$", "", topic_raw, flags=re.IGNORECASE)
            topic_raw = re.sub(r"_\d+_alt$", "", topic_raw, flags=re.IGNORECASE)
            topic_raw = re.sub(r"_\d+$", "", topic_raw)
            topic = (
                parser.format_topic(topic_raw, topic_data)
                .replace("_", " ")
                .capitalize()
            )
            parsed = (
                legacy_speaker,
                "self",
                topic,
                None,
                relative_path.as_posix(),
                topic_raw.casefold().startswith("ping"),
            )
        if parsed is None:
            folder_parsed = _specific_folder_voiceline_fallback(
                effective_path,
                parser,
                alias_lookup,
                topic_data,
            )
            if folder_parsed:
                parser.unresolved_aliases = unresolved_before
                parsed = folder_parsed
                folder_fallback_count += 1
        if parsed is not None:
            matched = find_vdf_key_for_filename(effective_path.name, vdf)
            if matched:
                matched_text = vdf[matched]
                stem = effective_path.stem.casefold()
                # A single audio resource can have several localization keys
                # distinguished only by a playback-context suffix. Identical
                # siblings describe the same recording and must not be emitted
                # again as filename-less phantom lines. Different-text siblings
                # remain unused so they can still represent genuinely missing
                # variants.
                for candidate in (
                    stem,
                    *(stem + suffix for suffix in ORDERED_KNOWN_SUFFIXES),
                ):
                    if vdf.get(candidate) == matched_text:
                        used_vdf.add(candidate)
            if legacy_match and len(relative_path.parts) > 1:
                legacy_count += 1
            # Parser overrides affect classification only. The public and
            # transcript identity always remains the extracted relative path.
            parsed = (*parsed[:4], relative_path.as_posix(), parsed[5])
            parser.place_in_result(result, parsed, parsed[4])
        if index % 1000 == 0:
            progress(f"Parsed {index:,}/{len(audio_files):,} voiceline audio files...")

    if legacy_count:
        progress(
            f"Captured {legacy_count:,} legacy rr_test voicelines from their hero folders."
        )
    if folder_fallback_count:
        progress(
            f"Captured {folder_fallback_count:,} voicelines from approved historical folder fallbacks."
        )

    if include_phantom and vdf:
        for key in sorted(set(vdf) - used_vdf):
            suffix = next(
                (item for item in ORDERED_KNOWN_SUFFIXES if key.endswith(item)), None
            )
            if not suffix:
                continue
            fake_name = key[: -len(suffix)] + ".mp3"
            if conversation_key_from_name(Path(fake_name).name, {}) is not None:
                continue
            # Phantom records still use the configured audio root for relative paths.
            fake_path = audio_dir / fake_name
            parsed = parser.parse_file(
                str(fake_path), alias_data, topic_data, valid_speakers
            )
            if parsed is not None:
                parser.place_in_result(
                    result,
                    parsed,
                    {
                        "filename": "",
                        "is_phantom": True,
                        "transcription": vdf[key],
                        "officialtranscription": True,
                        "voiceline_id": key,
                    },
                )

    for speaker_topics in result.values():
        if isinstance(speaker_topics, dict) and isinstance(
            speaker_topics.get("Self"), dict
        ):
            speaker_topics["Self"] = sort_subject_topics(
                group_config, speaker_topics["Self"]
            )
    _normalize_shopkeeper_topics(result)
    materialized = _materialize_voicelines(result, audio_dir, vdf, filename_overrides)
    assert isinstance(materialized, dict)
    return materialized, set(parser.unresolved_aliases)
