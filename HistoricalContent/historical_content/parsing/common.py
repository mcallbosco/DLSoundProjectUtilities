"""Configuration and path rules shared by content parsers."""

from __future__ import annotations

import json
from pathlib import Path, PurePosixPath

from ..errors import VpkPipelineError


def read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise VpkPipelineError(f"Invalid JSON in {path}: {exc}") from exc


def effective_audio_path(
    relative_path: Path,
    overrides: dict[str, str | None],
) -> Path | None:
    original = relative_path.as_posix()
    replacement = overrides.get(original.casefold(), original)
    return Path(*PurePosixPath(replacement).parts) if replacement is not None else None


def validate_mapping(path: Path) -> dict[str, list[str]]:
    payload = read_json(path)
    if not isinstance(payload, dict):
        raise VpkPipelineError(f"Character mappings must contain a JSON object: {path}")
    result: dict[str, list[str]] = {}
    aliases_seen: dict[str, str] = {}
    for canonical, aliases in payload.items():
        if (
            not isinstance(canonical, str)
            or not canonical.strip()
            or not isinstance(aliases, list)
        ):
            raise VpkPipelineError(
                f"Character mappings must use canonical-name to string-array entries: {path}"
            )
        cleaned = [
            item.strip() for item in aliases if isinstance(item, str) and item.strip()
        ]
        if canonical.strip() not in cleaned:
            cleaned.append(canonical.strip())
        result[canonical.strip()] = cleaned
        for alias in cleaned:
            key = alias.casefold()
            previous = aliases_seen.get(key)
            if previous and previous != canonical.strip():
                raise VpkPipelineError(
                    f"Character alias {alias!r} belongs to both {previous!r} and {canonical!r}."
                )
            aliases_seen[key] = canonical.strip()
    return result


def alias_index(mappings: dict[str, list[str]]) -> dict[str, str]:
    return {
        alias.casefold(): canonical
        for canonical, aliases in mappings.items()
        for alias in aliases
    }
