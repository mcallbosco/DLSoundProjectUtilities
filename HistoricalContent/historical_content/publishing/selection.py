"""Discover generated versions and order a publication batch."""

from __future__ import annotations

import json
from pathlib import Path

from .core import PublisherError, PublisherSettings


def local_publish_versions(source_dir: Path, game: str) -> tuple[list[dict[str, object]], str]:
    """Return generated versions in local catalog order, followed by uncataloged folders."""
    generated_root = source_dir.parent
    catalog_path = generated_root.parent / "catalogs" / f"{game}.json"
    catalog: dict[str, object] = {}
    if catalog_path.is_file():
        try:
            value = json.loads(catalog_path.read_text(encoding="utf-8-sig"))
            if isinstance(value, dict):
                catalog = value
        except Exception:
            catalog = {}

    result: list[dict[str, object]] = []
    seen: set[str] = set()
    catalog_versions = catalog.get("versions", [])
    if isinstance(catalog_versions, list):
        for item in catalog_versions:
            if not isinstance(item, dict):
                continue
            version_id = str(item.get("id", "")).strip()
            version_dir = generated_root / version_id
            if not version_id or not version_dir.is_dir():
                continue
            result.append({
                "id": version_id,
                "label": str(item.get("label") or version_id),
                "hidden": item.get("hidden") is True,
                "source": version_dir,
                **{
                    field: item[field]
                    for field in (
                        "kind",
                        "basedOnVersion",
                        "defaultLocalizationLanguage",
                        "transcriptMode",
                        "embeddedTranscriptLanguage",
                        "transcriptSource",
                    )
                    if field in item
                },
            })
            seen.add(version_id)

    if generated_root.is_dir():
        for version_dir in sorted(generated_root.iterdir(), key=lambda path: path.name.casefold()):
            if not version_dir.is_dir() or version_dir.name in seen:
                continue
            result.append({
                "id": version_dir.name,
                "label": version_dir.name,
                "hidden": True,
                "source": version_dir,
            })
    latest = str(catalog.get("latestVersion") or "").strip()
    return result, latest


def bulk_publish_order(
    selected: list[dict[str, object]],
    settings_by_id: dict[str, PublisherSettings],
    remote_manifest: dict[str, object],
) -> list[dict[str, object]]:
    """Order a validated batch so every selected custom base is published first."""
    remote_versions = remote_manifest.get("versions", [])
    if not isinstance(remote_versions, list):
        raise PublisherError("The published manifest has an invalid versions list.")
    remote_by_id = {
        str(item.get("id")): item
        for item in remote_versions
        if isinstance(item, dict) and item.get("id")
    }
    selected_ids = {str(item["id"]) for item in selected}
    preferred_order = list(reversed(selected))
    official: list[dict[str, object]] = []
    custom: list[dict[str, object]] = []
    for item in preferred_order:
        version_id = str(item["id"])
        settings = settings_by_id[version_id]
        if settings.kind != "custom":
            official.append(item)
            continue
        base_id = settings.based_on_version
        if base_id in selected_ids:
            base_settings = settings_by_id[base_id]
            if base_settings.kind != "official":
                raise PublisherError(
                    f"Custom version {version_id!r} must be based on official content."
                )
        else:
            remote_base = remote_by_id.get(base_id)
            if remote_base is None:
                raise PublisherError(
                    f"Custom base version is neither selected nor published: {base_id!r}."
                )
            if remote_base.get("kind", "official") != "official":
                raise PublisherError(
                    f"Custom version {version_id!r} must be based on official content."
                )
        custom.append(item)
    return [*official, *custom]


