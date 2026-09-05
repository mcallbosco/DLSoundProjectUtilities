"""Parse conversation audio and optional missing localization lines."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from .common import alias_index, effective_audio_path, read_json, validate_mapping
from .vdf import parse_quoted_kv_line


def conversation_key_from_name(
    filename: str,
    aliases: dict[str, str],
) -> (
    tuple[tuple[tuple[str, str], str] | tuple[tuple[str, str], str, str], int, int, str]
    | None
):
    with_topic = re.match(
        r"^(\w+)_match_start_(\w+)_(\w+)_(\w+)_convo(\d+)_(\d+)(?:_(?:alt_)?(\d+))?",
        filename,
    )
    topic: str | None
    if with_topic:
        starter, char1, char2, topic, convo, part, variation = with_topic.groups()
    else:
        plain = re.match(
            r"^(\w+)_match_start_(\w+)_(\w+)_convo(\d+)_(\d+)(?:_(?:alt_)?(\d+))?",
            filename,
        )
        if not plain:
            return None
        starter, char1, char2, convo, part, variation = plain.groups()
        topic = None
    variation = variation or "1"
    if "_alt_" in filename and variation.isdigit():
        variation = str(int(variation) + 1)

    def resolve(value: str) -> str:
        return aliases.get(value.casefold(), value)

    pair = tuple(sorted((resolve(char1), resolve(char2))))
    key = (pair, convo, topic) if topic else (pair, convo)
    return key, int(part), int(variation), resolve(starter)


def _conversation_completeness(
    files: list[dict[str, object]],
) -> tuple[bool, list[int]]:
    parts = sorted({int(item["part"]) for item in files if item.get("filename")})
    if not parts:
        return False, []
    missing = sorted(set(range(1, parts[-1] + 1)) - set(parts))
    return parts[0] == 1 and len(parts) > 1 and not missing, missing


def _load_conversation_vdf(
    vdf_path: Path | None,
    aliases: dict[str, str],
) -> dict[tuple, dict[tuple[int, int], dict[str, str]]]:
    if not vdf_path or not vdf_path.is_file():
        return {}
    result: dict[tuple, dict[tuple[int, int], dict[str, str]]] = {}
    for line in vdf_path.read_text(encoding="utf-8", errors="replace").splitlines():
        parsed_line = parse_quoted_kv_line(line)
        if not parsed_line:
            continue
        key_text, text = parsed_line
        parsed = conversation_key_from_name(key_text, aliases)
        if not parsed:
            continue
        key, part, variation, speaker = parsed
        result.setdefault(key, {})[(part, variation)] = {
            "text": text,
            "speaker": speaker,
        }
    return result


def parse_conversations(
    audio_dir: Path,
    character_mappings: Path,
    conversation_overrides: Path,
    vdf_path: Path | None,
    include_phantom: bool,
    audio_filename_overrides: dict[str, str | None] | None = None,
) -> dict[str, object]:
    mappings = validate_mapping(character_mappings)
    aliases = alias_index(mappings)
    filename_overrides = audio_filename_overrides or {}
    grouped: dict[tuple, list[dict[str, object]]] = {}
    for path in sorted(audio_dir.rglob("*.mp3")):
        relative_path = path.relative_to(audio_dir)
        effective_path = effective_audio_path(relative_path, filename_overrides)
        if effective_path is None:
            continue
        parsed = conversation_key_from_name(effective_path.name, aliases)
        if not parsed:
            continue
        key, part, variation, starter = parsed
        grouped.setdefault(key, []).append(
            {
                "filename": relative_path.as_posix(),
                "part": part,
                "variation": variation,
                "speaker": starter,
            }
        )

    vdf = _load_conversation_vdf(vdf_path, aliases)
    if include_phantom:
        for key, parts in vdf.items():
            files = grouped.setdefault(key, [])
            existing = {(int(item["part"]), int(item["variation"])) for item in files}
            for (part, variation), data in parts.items():
                if (part, variation) not in existing:
                    files.append(
                        {
                            "filename": "",
                            "part": part,
                            "variation": variation,
                            "speaker": data["speaker"],
                        }
                    )

    override_payload = read_json(conversation_overrides)
    complete_overrides = set()
    if isinstance(override_payload, dict) and isinstance(
        override_payload.get("complete_conversations"), list
    ):
        complete_overrides = {
            item
            for item in override_payload["complete_conversations"]
            if isinstance(item, str)
        }

    conversations: list[dict[str, object]] = []
    for key, files in sorted(grouped.items(), key=lambda item: str(item[0])):
        pair, convo_number, *topic_values = key
        topic = topic_values[0] if topic_values else None
        conversation_id = f"{pair[0]}_{pair[1]}_convo{convo_number}" + (
            f"_{topic}" if topic else ""
        )
        complete, missing = _conversation_completeness(files)
        if conversation_id in complete_overrides:
            complete, missing = True, []
        lines: list[dict[str, object]] = []
        for item in sorted(
            files,
            key=lambda value: (
                int(value["part"]),
                int(value["variation"]),
                str(value["filename"]),
            ),
        ):
            part = int(item["part"])
            variation = int(item["variation"])
            official = vdf.get(key, {}).get((part, variation))
            audio_key = (
                Path(str(item["filename"])).as_posix() if item["filename"] else ""
            )
            line: dict[str, object] = {
                "part": part,
                "variation": variation,
                "speaker": item["speaker"],
                "filename": audio_key,
                "transcription": official["text"] if official else "",
                "has_transcription": bool(official),
            }
            if official:
                line["officialtranscription"] = True
            lines.append(line)
        conversations.append(
            {
                "conversation_id": conversation_id,
                "status": [],
                "speakers": list(pair),
                "convo_id": convo_number,
                "topic": topic,
                "is_complete": complete,
                "missing_parts": missing,
                "starter": lines[0]["speaker"] if lines else "unknown",
                "lines": lines,
                "summary": "[Summary not generated]",
            }
        )
    return {
        "export_date": datetime.now().isoformat(),  # noqa: DTZ005 - preserve existing export format
        "total_conversations": len(conversations),
        "conversations": conversations,
    }
