"""Strict JSON loading and atomic, deterministic JSON persistence."""

from __future__ import annotations

import json
import os
from pathlib import Path

from .errors import BaselineError


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON object key: {key}")
        result[key] = value
    return result


def load_json(path: Path) -> object:
    try:
        return json.loads(
            path.read_text(encoding="utf-8-sig"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except Exception as exc:
        raise BaselineError(f"Invalid JSON in {path}: {exc}") from exc


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(serialize_json(value), encoding="utf-8")
    os.replace(temporary, path)


def serialize_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def write_json_if_changed(path: Path, value: object) -> bool:
    serialized = serialize_json(value)
    if path.is_file():
        try:
            if path.read_text(encoding="utf-8-sig") == serialized:
                return False
        except OSError:
            pass
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(serialized, encoding="utf-8")
    os.replace(temporary, path)
    return True

