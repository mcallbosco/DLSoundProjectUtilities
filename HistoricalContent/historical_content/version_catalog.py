"""Persistent local version ordering and adjacent-version voiceline comparisons."""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Callable


Progress = Callable[[str], None]
CATALOG_SCHEMA_VERSION = 1
PREVIEW_PREFIX = "preview-"
SHA_AUDIO_KEY_RE = re.compile(r"^sha256/[0-9a-f]{2}/([0-9a-f]{64})\.mp3$")


def _read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def catalog_path(data_dir: Path, game: str) -> Path:
    return data_dir.expanduser().resolve() / "catalogs" / f"{game}.json"


def _canonical_version_id(value: str) -> str:
    return value[len(PREVIEW_PREFIX):] if value.startswith(PREVIEW_PREFIX) else value


def _preview_version_id(value: str) -> str:
    return f"{PREVIEW_PREFIX}{_canonical_version_id(value)}"


def _available_version_ids(data_dir: Path, game: str) -> set[str]:
    ids: set[str] = set()
    preview_versions = data_dir / "preview-content" / game / "versions"
    if preview_versions.is_dir():
        ids.update(
            _canonical_version_id(path.name)
            for path in preview_versions.iterdir()
            if path.is_dir()
        )
    return ids


def _catalog_from_preview_manifest(data_dir: Path, game: str) -> dict[str, object]:
    manifest_path = data_dir / "preview-content" / game / "manifest.json"
    versions: list[dict[str, object]] = []
    latest = ""
    if manifest_path.is_file():
        manifest = _read_json(manifest_path)
        if isinstance(manifest, dict):
            latest = _canonical_version_id(str(manifest.get("latestVersion") or ""))
            values = manifest.get("versions", [])
            if isinstance(values, list):
                for value in values:
                    if not isinstance(value, dict) or not value.get("id"):
                        continue
                    label = str(value.get("label") or value["id"])
                    if label.startswith("Preview: "):
                        label = label[len("Preview: "):]
                    versions.append({
                        "id": _canonical_version_id(str(value["id"])),
                        "label": label,
                        "hidden": value.get("hidden") is True,
                    })
    return {
        "schemaVersion": CATALOG_SCHEMA_VERSION,
        "game": game,
        "latestVersion": latest,
        "versions": versions,
    }


def load_local_catalog(
    data_dir: Path,
    game: str,
    *,
    include_missing: bool = False,
) -> dict[str, object]:
    """Load the durable canonical-ID catalog, migrating the preview manifest once."""
    data_dir = data_dir.expanduser().resolve()
    path = catalog_path(data_dir, game)
    payload = _read_json(path) if path.is_file() else _catalog_from_preview_manifest(data_dir, game)
    if not isinstance(payload, dict) or not isinstance(payload.get("versions"), list):
        raise ValueError(f"Invalid local version catalog: {path}")
    available = _available_version_ids(data_dir, game)
    seen: set[str] = set()
    versions: list[dict[str, object]] = []
    for raw in payload["versions"]:
        if not isinstance(raw, dict):
            continue
        version_id = _canonical_version_id(str(raw.get("id") or "").strip())
        if not version_id or version_id in seen:
            continue
        if not include_missing and version_id not in available:
            continue
        seen.add(version_id)
        versions.append({
            "id": version_id,
            "label": str(raw.get("label") or version_id),
            "hidden": raw.get("hidden") is True,
        })
    for version_id in sorted(available - seen, reverse=True):
        versions.insert(0, {"id": version_id, "label": version_id, "hidden": False})
    latest = _canonical_version_id(str(payload.get("latestVersion") or ""))
    visible_ids = [str(value["id"]) for value in versions if value.get("hidden") is not True]
    if latest not in visible_ids:
        latest = visible_ids[0] if visible_ids else ""
    return {
        "schemaVersion": CATALOG_SCHEMA_VERSION,
        "game": game,
        "latestVersion": latest,
        "versions": versions,
    }


def save_local_catalog(data_dir: Path, game: str, catalog: dict[str, object]) -> dict[str, object]:
    versions = catalog.get("versions")
    if not isinstance(versions, list):
        raise ValueError("Local version catalog versions must be a list.")
    normalized = {
        "schemaVersion": CATALOG_SCHEMA_VERSION,
        "game": game,
        "latestVersion": _canonical_version_id(str(catalog.get("latestVersion") or "")),
        "versions": [],
    }
    seen: set[str] = set()
    for value in versions:
        if not isinstance(value, dict):
            raise ValueError("Every local version catalog entry must be an object.")
        version_id = _canonical_version_id(str(value.get("id") or "").strip())
        if not version_id or version_id in seen:
            raise ValueError("Local version IDs must be non-empty and unique.")
        seen.add(version_id)
        normalized["versions"].append({
            "id": version_id,
            "label": str(value.get("label") or version_id),
            "hidden": value.get("hidden") is True,
        })
    latest = str(normalized["latestVersion"])
    latest_entry = next(
        (value for value in normalized["versions"] if value["id"] == latest),
        None,
    )
    if normalized["versions"] and latest_entry is None:
        raise ValueError("The local latest version must exist in the catalog.")
    if latest_entry is not None and latest_entry["hidden"]:
        raise ValueError("The local latest version cannot be hidden.")
    _write_json(catalog_path(data_dir, game), normalized)
    return normalized


def register_local_version(
    data_dir: Path,
    game: str,
    version_id: str,
    label: str,
) -> dict[str, object]:
    catalog = load_local_catalog(data_dir, game)
    versions = catalog["versions"]
    assert isinstance(versions, list)
    existing = next((value for value in versions if value.get("id") == version_id), None)
    if existing is None:
        versions.insert(0, {"id": version_id, "label": label, "hidden": False})
    else:
        existing["label"] = label
    if not catalog.get("latestVersion"):
        catalog["latestVersion"] = version_id
    return save_local_catalog(data_dir, game, catalog)


def rebuild_local_preview_manifest(
    data_dir: Path,
    game: str,
    catalog: dict[str, object] | None = None,
) -> dict[str, object]:
    """Apply durable local ordering, labels, visibility, and latest selection."""
    data_dir = data_dir.expanduser().resolve()
    catalog = catalog or load_local_catalog(data_dir, game)
    game_root = data_dir / "preview-content" / game
    manifest_path = game_root / "manifest.json"
    if not manifest_path.is_file():
        raise ValueError("The local preview manifest does not exist.")
    manifest = _read_json(manifest_path)
    if not isinstance(manifest, dict):
        raise ValueError("The local preview manifest must be an object.")
    old_entries = manifest.get("versions", [])
    by_id = {
        _canonical_version_id(str(value.get("id"))): value
        for value in old_entries
        if isinstance(value, dict) and value.get("id")
    } if isinstance(old_entries, list) else {}
    entries: list[dict[str, object]] = []
    for value in catalog["versions"]:
        version_id = str(value["id"])
        preview_id = _preview_version_id(version_id)
        if not (game_root / "versions" / preview_id).is_dir():
            continue
        entry = dict(by_id.get(version_id, {}))
        entry.update({
            "id": preview_id,
            "label": f"Preview: {value['label']}",
            "hidden": value.get("hidden") is True,
        })
        entries.append(entry)
    manifest["versions"] = entries
    latest = str(catalog.get("latestVersion") or "")
    manifest["latestVersion"] = _preview_version_id(latest) if latest else ""
    manifest["updatedAt"] = datetime.now(timezone.utc).isoformat()
    _write_json(manifest_path, manifest)
    return manifest


def _walk_lines(node: object):
    if isinstance(node, dict):
        if isinstance(node.get("filename"), str):
            yield node
            return
        for value in node.values():
            yield from _walk_lines(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk_lines(value)


def _normalized_filename(value: str) -> str:
    return PurePosixPath(value.strip().replace("\\", "/")).as_posix().casefold()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _line_audio_hash(line: dict[str, object], version_root: Path) -> str | None:
    direct = line.get("audioSha256")
    if isinstance(direct, str) and len(direct) == 64:
        return direct.casefold()
    audio_key = line.get("audioKey")
    if isinstance(audio_key, str):
        match = SHA_AUDIO_KEY_RE.fullmatch(audio_key.casefold())
        if match:
            return match.group(1)
    filename = line.get("filename")
    if isinstance(filename, str):
        legacy_path = version_root / "Audio" / Path(*PurePosixPath(filename.replace("\\", "/")).parts)
        if legacy_path.is_file():
            return _file_sha256(legacy_path)
    return None


def _version_voiceline_path(data_dir: Path, game: str, version_id: str) -> tuple[Path | None, Path | None]:
    generated = data_dir / "generated" / version_id / "all_voicelines.json"
    preview = (
        data_dir / "preview-content" / game / "versions" /
        _preview_version_id(version_id) / "voicelines.json"
    )
    return (generated if generated.is_file() else None, preview if preview.is_file() else None)


def recalculate_version_statuses(
    data_dir: Path,
    game: str,
    catalog: dict[str, object] | None = None,
    progress: Progress = print,
) -> int:
    """Annotate all local voiceline JSON using adjacent catalog versions."""
    data_dir = data_dir.expanduser().resolve()
    catalog = catalog or load_local_catalog(data_dir, game)
    ordered_ids = [str(value["id"]) for value in catalog["versions"]]
    payloads: dict[str, object] = {}
    indexes: dict[str, dict[str, str | None]] = {}
    destinations: dict[str, list[Path]] = {}
    for version_id in ordered_ids:
        generated, preview = _version_voiceline_path(data_dir, game, version_id)
        source = generated or preview
        if source is None:
            continue
        payload = _read_json(source)
        root = data_dir / "generated" / version_id
        index: dict[str, str | None] = {}
        for line in _walk_lines(payload):
            filename = _normalized_filename(str(line["filename"]))
            audio_hash = _line_audio_hash(line, root)
            previous = index.get(filename)
            if filename in index and previous != audio_hash:
                raise ValueError(
                    f"Version {version_id!r} has different recordings at filename {filename!r}."
                )
            index[filename] = audio_hash
        payloads[version_id] = payload
        indexes[version_id] = index
        destinations[version_id] = [path for path in (generated, preview) if path is not None]

    changed_files = 0
    for position, version_id in enumerate(ordered_ids):
        payload = payloads.get(version_id)
        if payload is None:
            continue
        older_id = ordered_ids[position + 1] if position + 1 < len(ordered_ids) else None
        newer_id = ordered_ids[position - 1] if position > 0 else None
        current_index = indexes[version_id]
        older_index = indexes.get(older_id) if older_id else None
        newer_index = indexes.get(newer_id) if newer_id else None
        for line in _walk_lines(payload):
            filename = _normalized_filename(str(line["filename"]))
            status: dict[str, object] = {}
            if older_id and older_index is not None:
                status["comparedTo"] = older_id
                if filename not in older_index:
                    status["change"] = "new"
                elif (
                    current_index.get(filename) is not None
                    and older_index.get(filename) is not None
                    and current_index[filename] != older_index[filename]
                ):
                    status["change"] = "modified"
            if newer_id and newer_index is not None and filename not in newer_index:
                status["removedInNextVersion"] = True
                status["nextVersion"] = newer_id
            line["versionStatus"] = status
        serialized = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        for destination in destinations[version_id]:
            if destination.is_file() and destination.read_text(encoding="utf-8-sig") == serialized:
                continue
            _write_json(destination, payload)
            changed_files += 1
    progress(
        f"Recalculated adjacent-version status for {len(payloads)} version(s); "
        f"updated {changed_files} voiceline JSON file(s)."
    )
    return changed_files


def apply_local_catalog(
    data_dir: Path,
    game: str,
    catalog: dict[str, object],
    progress: Progress = print,
) -> dict[str, object]:
    saved = save_local_catalog(data_dir, game, catalog)
    rebuild_local_preview_manifest(data_dir, game, saved)
    recalculate_version_statuses(data_dir, game, saved, progress)
    return saved
