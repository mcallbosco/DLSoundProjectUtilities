"""One-time, resumable historical portrait backfill for existing workspaces."""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .version_catalog import load_local_catalog
from .vpk_pipeline import (
    HISTORICAL_ICON_FORMAT_VERSION,
    VERSION_RE,
    VpkPipelineError,
    _export_historical_icons_from_vpk,
)


Progress = Callable[[str], None]


@dataclass(frozen=True)
class IconBackfillSettings:
    data_dir: Path
    game: str
    source2viewer_binary: Path
    character_mappings: Path
    extraction_threads: int = 8


@dataclass(frozen=True)
class IconBackfillResult:
    updated_versions: tuple[str, ...]
    skipped_versions: tuple[str, ...]
    failed_versions: tuple[str, ...]
    image_count: int


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        raise VpkPipelineError(f"Invalid JSON in {path}: {exc}") from exc


def _write_json(path: Path, value: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _assert_inside(parent: Path, target: Path, label: str) -> None:
    resolved_parent = parent.resolve()
    resolved_target = target.resolve()
    if resolved_target == resolved_parent or resolved_parent not in resolved_target.parents:
        raise VpkPipelineError(f"{label} resolves outside {resolved_parent}: {resolved_target}")


def _replace_icon_tree(source: Path, destination: Path, allowed_parent: Path) -> None:
    _assert_inside(allowed_parent, destination, "Icon backfill destination")
    if not source.is_dir() or not (source / "manifest.json").is_file():
        raise VpkPipelineError(f"Generated historical icon pack is incomplete: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".icon-backfill-tmp")
    _assert_inside(allowed_parent, temporary, "Icon backfill temporary directory")
    if temporary.exists():
        if not temporary.is_dir():
            raise VpkPipelineError(f"Icon backfill temporary path is not a directory: {temporary}")
        shutil.rmtree(temporary)
    shutil.copytree(source, temporary)
    if destination.exists():
        if not destination.is_dir():
            raise VpkPipelineError(f"Icon backfill destination is not a directory: {destination}")
        shutil.rmtree(destination)
    os.replace(temporary, destination)


def _manifest_variants(icon_pack: Path) -> list[str]:
    manifest = _read_json(icon_pack / "manifest.json")
    if not isinstance(manifest, dict) or not isinstance(manifest.get("icons"), dict):
        raise VpkPipelineError(f"Historical icon manifest is invalid: {icon_pack / 'manifest.json'}")
    return [
        str(variant)
        for variant, entries in manifest["icons"].items()
        if isinstance(entries, dict) and entries
    ]


def _sync_derived_icon_packs(
    *,
    data_dir: Path,
    game: str,
    version_id: str,
    source_icon_pack: Path,
    progress: Progress,
) -> None:
    targets = (
        (
            data_dir / "generated" / version_id,
            data_dir / "generated" / version_id / "IconPacks" / "default",
            "publisher source",
        ),
        (
            data_dir / "preview-content" / game / "versions" / f"preview-{version_id}",
            data_dir / "preview-content" / game / "versions" / f"preview-{version_id}"
            / "icons" / "default",
            "preview",
        ),
    )
    for version_root, destination, label in targets:
        if not version_root.is_dir():
            progress(
                f"Icon backfill: [{version_id}] {label} root is missing; "
                "workspace icons were still updated."
            )
            continue
        _replace_icon_tree(source_icon_pack, destination, version_root)


def backfill_historical_icons(
    settings: IconBackfillSettings,
    progress: Progress = print,
) -> IconBackfillResult:
    """Refresh icons only; audio, text, localization, and publication are untouched."""
    data_dir = settings.data_dir.expanduser().resolve()
    binary = settings.source2viewer_binary.expanduser().resolve()
    mappings = settings.character_mappings.expanduser().resolve()
    if not binary.is_file():
        raise VpkPipelineError(f"Source2Viewer executable does not exist: {binary}")
    if not mappings.is_file():
        raise VpkPipelineError(f"Character mapping does not exist: {mappings}")
    if not VERSION_RE.fullmatch(settings.game):
        raise VpkPipelineError("Game ID contains unsupported path characters.")
    if settings.extraction_threads < 1 or settings.extraction_threads > 64:
        raise VpkPipelineError("Extraction threads must be between 1 and 64.")

    catalog = load_local_catalog(data_dir, settings.game, include_missing=True)
    versions = catalog.get("versions", [])
    if not isinstance(versions, list) or not versions:
        raise VpkPipelineError("No local versions are registered for icon backfill.")
    latest_version = str(catalog.get("latestVersion") or "")
    updated: list[str] = []
    skipped: list[str] = []
    failed: list[str] = []
    total_images = 0

    for entry in versions:
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
            continue
        version_id = entry["id"]
        if not VERSION_RE.fullmatch(version_id):
            failed.append(version_id)
            progress(f"Icon backfill: [{version_id}] invalid version ID; skipping.")
            continue
        workspace = data_dir / "workspaces" / settings.game / version_id
        state_path = workspace / "pipeline-state.json"
        source_dir = workspace / "source"
        if not state_path.is_file() or not source_dir.is_dir():
            progress(f"Icon backfill: [{version_id}] workspace is incomplete; skipping.")
            skipped.append(version_id)
            continue
        try:
            state = _read_json(state_path)
            if not isinstance(state, dict) or not isinstance(state.get("vpkPath"), str):
                raise VpkPipelineError(f"Workspace has no recorded VPK path: {state_path}")
            vpk_path = Path(state["vpkPath"]).expanduser().resolve()
            if not vpk_path.is_file():
                progress(f"Icon backfill: [{version_id}] recorded VPK is missing; skipping: {vpk_path}")
                skipped.append(version_id)
                continue

            include_highlights = version_id == latest_version
            variant_label = "all four variants" if include_highlights else "minimap and normal"
            progress(f"Icon backfill: [{version_id}] extracting {variant_label} from {vpk_path}.")
            image_count = _export_historical_icons_from_vpk(
                source2viewer_binary=binary,
                vpk_path=vpk_path,
                source_dir=source_dir,
                character_mappings=mappings,
                extraction_threads=settings.extraction_threads,
                include_highlight_variants=include_highlights,
                progress=progress,
            )
            icon_pack = source_dir / "IconPacks" / "default"
            variants = _manifest_variants(icon_pack)
            _sync_derived_icon_packs(
                data_dir=data_dir,
                game=settings.game,
                version_id=version_id,
                source_icon_pack=icon_pack,
                progress=progress,
            )
            state["iconsComplete"] = True
            state["historicalIcons"] = {
                "complete": True,
                "extractionFormatVersion": HISTORICAL_ICON_FORMAT_VERSION,
                "imageCount": image_count,
                "variants": variants,
                "backfilledAt": datetime.now(timezone.utc).isoformat(),
            }
            _write_json(state_path, state)
            total_images += image_count
            updated.append(version_id)
            progress(
                f"Icon backfill: [{version_id}] updated {image_count:,} images "
                f"across {', '.join(variants)}."
            )
        except Exception as exc:
            failed.append(version_id)
            progress(f"Icon backfill: [{version_id}] ERROR: {exc}")

    return IconBackfillResult(
        updated_versions=tuple(updated),
        skipped_versions=tuple(skipped),
        failed_versions=tuple(failed),
        image_count=total_images,
    )
