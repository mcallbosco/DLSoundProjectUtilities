"""Load and apply data-driven voiceline display groups."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


DEFAULT_GROUP_CONFIG_PATH = (
    Path(__file__).resolve().parents[2] / "Assets" / "voiceline_groups.json"
)


class GroupConfigError(ValueError):
    """Raised when the voiceline group configuration is invalid."""


class _LoadedGroupConfig(dict):
    """Dictionary-compatible config with a non-serialized rule cache."""


def normalize_topic(value: str) -> str:
    return value.strip().replace(" ", "_").lower()


def _string_list(value: object, field: str, errors: list[str]) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        errors.append(f"{field} must be an array of non-empty strings.")
        return []
    return value


def _match_block(value: object, field: str, errors: list[str]) -> dict[str, list[str]]:
    if not isinstance(value, dict):
        errors.append(f"{field} must be an object.")
        value = {}
    return {
        "topics": _string_list(value.get("topics", []), f"{field}.topics", errors),
        "prefixes": _string_list(value.get("prefixes", []), f"{field}.prefixes", errors),
        "excludePrefixes": _string_list(
            value.get("excludePrefixes", []), f"{field}.excludePrefixes", errors
        ),
    }


def validate_group_config(payload: object) -> list[str]:
    errors: list[str] = []
    if not isinstance(payload, dict):
        return ["The group configuration must contain a JSON object."]
    if payload.get("schemaVersion") != 1:
        errors.append("schemaVersion must be 1.")
    unmatched = payload.get("unmatched")
    if not isinstance(unmatched, dict):
        errors.append("unmatched must be an object.")
    else:
        if unmatched.get("voice") != "keep-topic-at-root":
            errors.append("unmatched.voice must be keep-topic-at-root.")
        if unmatched.get("ping") != "keep-topic-at-pings-root":
            errors.append("unmatched.ping must be keep-topic-at-pings-root.")
    if not isinstance(payload.get("pingRoot"), str) or not payload.get("pingRoot"):
        errors.append("pingRoot must be a non-empty string.")
    _string_list(payload.get("rootTopicOrder", []), "rootTopicOrder", errors)

    groups = payload.get("groups")
    if not isinstance(groups, list):
        errors.append("groups must be an array.")
        return errors

    group_ids: set[str] = set()
    group_labels: set[tuple[str, str]] = set()
    subgroup_ids: dict[str, set[str]] = {}
    exact_assignments: dict[tuple[str, str], str] = {}
    prefix_assignments: dict[tuple[str, str], str] = {}

    for index, group in enumerate(groups):
        field = f"groups[{index}]"
        if not isinstance(group, dict):
            errors.append(f"{field} must be an object.")
            continue
        group_id = group.get("id")
        label = group.get("label")
        scope = group.get("scope")
        sort_section = group.get("sortSection")
        if not isinstance(group_id, str) or not group_id:
            errors.append(f"{field}.id must be a non-empty string.")
            group_id = f"invalid-{index}"
        elif group_id in group_ids:
            errors.append(f"Duplicate group ID: {group_id}")
        group_ids.add(group_id)
        if not isinstance(label, str) or not label:
            errors.append(f"{field}.label must be a non-empty string.")
            label = group_id
        if scope not in {"voice", "ping"}:
            errors.append(f"{field}.scope must be voice or ping.")
            scope = "voice"
        if (scope, label.casefold()) in group_labels:
            errors.append(f"Duplicate {scope} group label: {label}")
        group_labels.add((scope, label.casefold()))
        if sort_section not in {"root", "groups"}:
            errors.append(f"{field}.sortSection must be root or groups.")

        match = _match_block(group.get("match"), f"{field}.match", errors)
        target = str(label)
        for topic in match["topics"]:
            key = (scope, normalize_topic(topic))
            previous = exact_assignments.get(key)
            if previous:
                errors.append(f"Topic {topic!r} is assigned to both {previous!r} and {target!r}.")
            exact_assignments[key] = target
        for prefix in match["prefixes"]:
            key = (scope, normalize_topic(prefix))
            previous = prefix_assignments.get(key)
            if previous:
                errors.append(f"Prefix {prefix!r} is assigned to both {previous!r} and {target!r}.")
            prefix_assignments[key] = target

        subgroups = group.get("subgroups")
        if not isinstance(subgroups, list):
            errors.append(f"{field}.subgroups must be an array.")
            continue
        subgroup_ids[group_id] = set()
        for sub_index, subgroup in enumerate(subgroups):
            sub_field = f"{field}.subgroups[{sub_index}]"
            if not isinstance(subgroup, dict):
                errors.append(f"{sub_field} must be an object.")
                continue
            sub_id = subgroup.get("id")
            sub_label = subgroup.get("label")
            if not isinstance(sub_id, str) or not sub_id:
                errors.append(f"{sub_field}.id must be a non-empty string.")
                sub_id = f"invalid-{sub_index}"
            elif sub_id in subgroup_ids[group_id]:
                errors.append(f"Duplicate subgroup ID {sub_id!r} in group {group_id!r}.")
            subgroup_ids[group_id].add(sub_id)
            if not isinstance(sub_label, str) or not sub_label:
                errors.append(f"{sub_field}.label must be a non-empty string.")
                sub_label = sub_id
            sub_match = _match_block(subgroup.get("match"), f"{sub_field}.match", errors)
            sub_target = f"{label}/{sub_label}"
            for topic in sub_match["topics"]:
                key = (scope, normalize_topic(topic))
                previous = exact_assignments.get(key)
                if previous:
                    errors.append(
                        f"Topic {topic!r} is assigned to both {previous!r} and {sub_target!r}."
                    )
                exact_assignments[key] = sub_target
            for prefix in sub_match["prefixes"]:
                key = (scope, normalize_topic(prefix))
                previous = prefix_assignments.get(key)
                if previous:
                    errors.append(
                        f"Prefix {prefix!r} is assigned to both {previous!r} and {sub_target!r}."
                    )
                prefix_assignments[key] = sub_target

    overrides = payload.get("overrides")
    if not isinstance(overrides, list):
        errors.append("overrides must be an array.")
    else:
        for index, override in enumerate(overrides):
            field = f"overrides[{index}]"
            if not isinstance(override, dict):
                errors.append(f"{field} must be an object.")
                continue
            if not isinstance(override.get("filename"), str) or not override.get("filename"):
                errors.append(f"{field}.filename must be a non-empty string.")
            if override.get("scope") not in {"voice", "ping"}:
                errors.append(f"{field}.scope must be voice or ping.")
            target_group = override.get("group")
            if target_group not in group_ids:
                errors.append(f"{field}.group refers to unknown group {target_group!r}.")
            target_subgroup = override.get("subgroup")
            if target_subgroup is not None and target_subgroup not in subgroup_ids.get(
                str(target_group), set()
            ):
                errors.append(
                    f"{field}.subgroup refers to unknown subgroup {target_subgroup!r}."
                )
    return errors


def load_group_config(path: str | Path = DEFAULT_GROUP_CONFIG_PATH) -> dict[str, Any]:
    config_path = Path(path).expanduser().resolve()
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GroupConfigError(f"Could not load voiceline groups from {config_path}: {exc}") from exc
    errors = validate_group_config(payload)
    if errors:
        raise GroupConfigError("Invalid voiceline group configuration:\n- " + "\n- ".join(errors))
    loaded = _LoadedGroupConfig(payload)
    loaded._compiled_rules = _compile_group_rules(loaded)
    return loaded


def _compile_group_rules(config: dict[str, Any]) -> dict[str, Any]:
    """Build lookup tables once so classification stays fast for large exports."""
    exact: dict[str, dict[str, tuple[tuple[str, ...], tuple[str, ...]]]] = {
        "voice": {},
        "ping": {},
    }
    prefixes: dict[
        str, list[tuple[str, tuple[str, ...], tuple[str, ...]]]
    ] = {"voice": [], "ping": []}
    overrides: dict[tuple[str, str], tuple[str, ...]] = {}
    groups_by_id = {group["id"]: group for group in config["groups"]}

    def add_match(scope: str, path: tuple[str, ...], match: dict[str, Any]) -> None:
        exclusions = tuple(normalize_topic(item) for item in match["excludePrefixes"])
        for topic in match["topics"]:
            exact[scope][normalize_topic(topic)] = (path, exclusions)
        for prefix in match["prefixes"]:
            prefixes[scope].append((normalize_topic(prefix), exclusions, path))

    for group in config["groups"]:
        scope = group["scope"]
        group_path = (group["label"],)
        for subgroup in group["subgroups"]:
            add_match(scope, group_path + (subgroup["label"],), subgroup["match"])
        add_match(scope, group_path, group["match"])

    for scope in prefixes:
        prefixes[scope].sort(key=lambda rule: len(rule[0]), reverse=True)

    for override in config.get("overrides", []):
        group = groups_by_id[override["group"]]
        path = (group["label"],)
        if override.get("subgroup") is not None:
            subgroup = next(
                item for item in group["subgroups"] if item["id"] == override["subgroup"]
            )
            path += (subgroup["label"],)
        key = (override["scope"], Path(override["filename"]).name.casefold())
        overrides.setdefault(key, path)

    return {"exact": exact, "prefixes": prefixes, "overrides": overrides}


def _compiled_group_rules(config: dict[str, Any]) -> dict[str, Any]:
    compiled = getattr(config, "_compiled_rules", None)
    if compiled is None:
        compiled = _compile_group_rules(config)
        if isinstance(config, _LoadedGroupConfig):
            config._compiled_rules = compiled
    return compiled


def _is_allowed(exclusions: tuple[str, ...], topic_key: str) -> bool:
    return not any(topic_key.startswith(prefix) for prefix in exclusions)


def classify_topic(
    config: dict[str, Any],
    scope: str,
    topic: str,
    filename: str | None = None,
) -> tuple[str, ...] | None:
    """Return the configured display path for a topic, or None when unmatched."""
    topic_key = normalize_topic(topic)
    filename_key = Path(filename).name.casefold() if filename else ""
    compiled = _compiled_group_rules(config)

    if filename_key:
        override_path = compiled["overrides"].get((scope, filename_key))
        if override_path:
            return override_path

    exact_rule = compiled["exact"].get(scope, {}).get(topic_key)
    if exact_rule and _is_allowed(exact_rule[1], topic_key):
        return exact_rule[0]

    for prefix, exclusions, path in compiled["prefixes"].get(scope, []):
        if topic_key.startswith(prefix) and _is_allowed(exclusions, topic_key):
            return path
    return None


def configured_group_labels(
    config: dict[str, Any], scope: str, sort_section: str | None = None
) -> list[str]:
    return [
        group["label"]
        for group in config["groups"]
        if group["scope"] == scope
        and (sort_section is None or group["sortSection"] == sort_section)
    ]


def configured_topic_keys(config: dict[str, Any], scope: str) -> set[str]:
    result: set[str] = set()
    for group in config["groups"]:
        if group["scope"] != scope:
            continue
        result.update(normalize_topic(item) for item in group["match"]["topics"])
        for subgroup in group["subgroups"]:
            result.update(normalize_topic(item) for item in subgroup["match"]["topics"])
    return result


def sort_subject_topics(config: dict[str, Any], topics: dict[str, Any]) -> dict[str, Any]:
    """Apply the configured root-topic and display-group ordering."""
    priority = config["rootTopicOrder"]
    priority_index = {label: index for index, label in enumerate(priority)}
    group_labels = configured_group_labels(config, "voice", "groups")
    ping_root = config["pingRoot"]
    excluded = set(group_labels) | {ping_root}
    root_items = {key: value for key, value in topics.items() if key not in excluded}
    ordered = dict(
        sorted(root_items.items(), key=lambda item: (priority_index.get(item[0], len(priority)), item[0]))
    )
    for label in group_labels:
        if label in topics:
            ordered[label] = topics[label]
    if ping_root in topics:
        ordered[ping_root] = topics[ping_root]
    return ordered
