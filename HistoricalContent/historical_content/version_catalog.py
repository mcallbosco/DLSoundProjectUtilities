"""Persistent local version ordering and adjacent-version voiceline comparisons."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Callable


from .json_io import write_json

Progress = Callable[[str], None]
CATALOG_SCHEMA_VERSION = 1
PREVIEW_PREFIX = "preview-"
SHA_AUDIO_KEY_RE = re.compile(r"^sha256/[0-9a-f]{2}/([0-9a-f]{64})\.mp3$")
VERSION_KINDS = {"official", "custom"}
TRANSCRIPT_MODES = {"localized", "embedded"}


def _read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def catalog_path(data_dir: Path, game: str) -> Path:
    return data_dir.expanduser().resolve() / "catalogs" / f"{game}.json"


def _canonical_version_id(value: str) -> str:
    return value[len(PREVIEW_PREFIX):] if value.startswith(PREVIEW_PREFIX) else value


def _preview_version_id(value: str) -> str:
    return f"{PREVIEW_PREFIX}{_canonical_version_id(value)}"


def _normalize_version_entry(raw: dict[str, object], version_id: str) -> dict[str, object]:
    kind = str(raw.get("kind") or "official").strip().casefold()
    transcript_mode = str(raw.get("transcriptMode") or "localized").strip().casefold()
    entry: dict[str, object] = {
        "id": version_id,
        "label": str(raw.get("label") or version_id),
        "hidden": raw.get("hidden") is True,
        "kind": kind,
        "transcriptMode": transcript_mode,
    }
    for field in (
        "basedOnVersion",
        "defaultLocalizationLanguage",
        "embeddedTranscriptLanguage",
    ):
        value = str(raw.get(field) or "").strip()
        if value:
            entry[field] = _canonical_version_id(value) if field == "basedOnVersion" else value
    transcript_source = raw.get("transcriptSource")
    if isinstance(transcript_source, dict):
        entry["transcriptSource"] = dict(transcript_source)
    return entry


def _generated_version_metadata(data_dir: Path, version_id: str) -> dict[str, object]:
    """Return authoritative custom metadata stored beside a generated version."""
    metadata_path = data_dir / "generated" / version_id / "custom-version.json"
    if not metadata_path.is_file():
        return {}
    try:
        metadata = _read_json(metadata_path)
    except Exception as exc:
        raise ValueError(
            f"Invalid generated custom metadata for {version_id!r}: {metadata_path}"
        ) from exc
    if not isinstance(metadata, dict) or metadata.get("kind") != "custom":
        raise ValueError(
            f"Generated custom metadata for {version_id!r} must declare kind 'custom'."
        )
    return metadata


def _normalize_catalog_entry(
    data_dir: Path,
    raw: dict[str, object],
    version_id: str,
) -> dict[str, object]:
    metadata = _generated_version_metadata(data_dir, version_id)
    return _normalize_version_entry({**raw, **metadata}, version_id)


def _validate_catalog_versions(versions: list[dict[str, object]], latest: str) -> None:
    by_id = {str(value["id"]): value for value in versions}
    for entry in versions:
        version_id = str(entry["id"])
        kind = str(entry.get("kind") or "official")
        transcript_mode = str(entry.get("transcriptMode") or "localized")
        if kind not in VERSION_KINDS:
            raise ValueError(f"Unsupported version kind for {version_id!r}: {kind!r}.")
        if transcript_mode not in TRANSCRIPT_MODES:
            raise ValueError(
                f"Unsupported transcript mode for {version_id!r}: {transcript_mode!r}."
            )
        if kind != "custom":
            continue
        based_on = str(entry.get("basedOnVersion") or "")
        base_entry = by_id.get(based_on)
        if not based_on or base_entry is None:
            raise ValueError(
                f"Custom version {version_id!r} must reference an existing basedOnVersion."
            )
        if base_entry.get("kind") == "custom":
            raise ValueError(
                f"Custom version {version_id!r} must be based on an official version."
            )
        if transcript_mode != "embedded":
            raise ValueError(
                f"Custom version {version_id!r} must use transcriptMode 'embedded'."
            )
        if not str(entry.get("defaultLocalizationLanguage") or "").strip():
            raise ValueError(
                f"Custom version {version_id!r} needs a defaultLocalizationLanguage."
            )
        if not str(entry.get("embeddedTranscriptLanguage") or "").strip():
            raise ValueError(
                f"Custom version {version_id!r} needs an embeddedTranscriptLanguage."
            )
        if not isinstance(entry.get("transcriptSource"), dict):
            raise ValueError(
                f"Custom version {version_id!r} needs immutable transcriptSource metadata."
            )
    latest_entry = by_id.get(latest) if latest else None
    if latest_entry is not None and latest_entry.get("kind") == "custom":
        raise ValueError("The local latest version cannot be custom content.")


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
                    versions.append(_normalize_version_entry(
                        {**value, "label": label},
                        _canonical_version_id(str(value["id"])),
                    ))
    return {
        "schemaVersion": CATALOG_SCHEMA_VERSION,
        "game": game,
        "latestVersion": latest,
        "versions": versions,
    }


def load_cataloged_local_versions(data_dir: Path, game: str) -> list[dict[str, object]]:
    """Load only versions explicitly recorded in the catalog or preview manifest."""
    data_dir = data_dir.expanduser().resolve()
    path = catalog_path(data_dir, game)
    payload = _read_json(path) if path.is_file() else _catalog_from_preview_manifest(data_dir, game)
    if not isinstance(payload, dict) or not isinstance(payload.get("versions"), list):
        raise ValueError(f"Invalid local version catalog: {path}")
    versions: list[dict[str, object]] = []
    seen: set[str] = set()
    for raw in payload["versions"]:
        if not isinstance(raw, dict):
            continue
        version_id = _canonical_version_id(str(raw.get("id") or "").strip())
        if not version_id or version_id in seen:
            continue
        seen.add(version_id)
        versions.append(_normalize_catalog_entry(data_dir, raw, version_id))
    return versions


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
        versions.append(_normalize_catalog_entry(data_dir, raw, version_id))
    for version_id in sorted(available - seen, reverse=True):
        versions.insert(0, _normalize_catalog_entry(data_dir, {}, version_id))
    latest = _canonical_version_id(str(payload.get("latestVersion") or ""))
    visible_ids = [
        str(value["id"])
        for value in versions
        if value.get("hidden") is not True and value.get("kind") != "custom"
    ]
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
        normalized["versions"].append(_normalize_version_entry(value, version_id))
    latest = str(normalized["latestVersion"])
    _validate_catalog_versions(normalized["versions"], latest)
    latest_entry = next(
        (value for value in normalized["versions"] if value["id"] == latest),
        None,
    )
    if normalized["versions"] and latest_entry is None:
        raise ValueError("The local latest version must exist in the catalog.")
    if latest_entry is not None and latest_entry["hidden"]:
        raise ValueError("The local latest version cannot be hidden.")
    write_json(catalog_path(data_dir, game), normalized)
    return normalized


def register_local_version(
    data_dir: Path,
    game: str,
    version_id: str,
    label: str,
    *,
    metadata: dict[str, object] | None = None,
) -> dict[str, object]:
    catalog = load_local_catalog(data_dir, game)
    versions = catalog["versions"]
    assert isinstance(versions, list)
    existing = next((value for value in versions if value.get("id") == version_id), None)
    if existing is None:
        versions.insert(0, _normalize_version_entry(
            {"label": label, "hidden": False, **(metadata or {})},
            version_id,
        ))
    else:
        existing["label"] = label
        if metadata:
            existing.update(_normalize_version_entry({**existing, **metadata}, version_id))
    if not catalog.get("latestVersion"):
        candidate = next(value for value in versions if value.get("id") == version_id)
        if candidate.get("kind") != "custom":
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
        for field in (
            "kind",
            "basedOnVersion",
            "defaultLocalizationLanguage",
            "transcriptMode",
            "embeddedTranscriptLanguage",
            "transcriptSource",
        ):
            if field not in value:
                entry.pop(field, None)
                continue
            field_value = value[field]
            entry[field] = (
                _preview_version_id(str(field_value))
                if field == "basedOnVersion"
                else field_value
            )
        entries.append(entry)
    manifest["versions"] = entries
    latest = str(catalog.get("latestVersion") or "")
    manifest["latestVersion"] = _preview_version_id(latest) if latest else ""
    manifest["updatedAt"] = datetime.now(timezone.utc).isoformat()
    write_json(manifest_path, manifest)
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
    entries = [value for value in catalog["versions"] if isinstance(value, dict)]
    ordered_ids = [str(value["id"]) for value in entries]
    custom_ids = {
        str(value["id"])
        for value in entries
        if value.get("kind") == "custom"
    }
    official_ids = [version_id for version_id in ordered_ids if version_id not in custom_ids]
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
            if not filename:
                continue
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
    for version_id in ordered_ids:
        payload = payloads.get(version_id)
        if payload is None:
            continue
        if version_id in custom_ids:
            older_id = None
            newer_id = None
        else:
            position = official_ids.index(version_id)
            older_id = official_ids[position + 1] if position + 1 < len(official_ids) else None
            newer_id = official_ids[position - 1] if position > 0 else None
        current_index = indexes[version_id]
        older_index = indexes.get(older_id) if older_id else None
        newer_index = indexes.get(newer_id) if newer_id else None
        for line in _walk_lines(payload):
            filename = _normalized_filename(str(line["filename"]))
            if version_id in custom_ids or not filename:
                line["versionStatus"] = {}
                if version_id in custom_ids:
                    line.pop("status", None)
                continue
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
            write_json(destination, payload)
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
