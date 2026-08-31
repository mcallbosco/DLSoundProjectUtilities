#!/usr/bin/env python3
"""Core validation, planning, and R2 publishing logic for VLViewer content.

The publisher deliberately separates mutable JSON documents from immutable
binary assets. Existing binary object keys may never be replaced with different
bytes, while transcript and metadata JSON can be corrected under the same
user-visible version ID.
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable


INVENTORY_SCHEMA_VERSION = 2
MANIFEST_SCHEMA_VERSION = 1
IMMUTABLE_CACHE_CONTROL = "public, max-age=31536000, immutable"
MUTABLE_JSON_CACHE_CONTROL = "public, max-age=0, must-revalidate"
VERSION_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
GAME_KEY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")

ProgressCallback = Callable[[str], None]


class PublisherError(RuntimeError):
    """Raised for a safe, user-facing publisher failure."""


@dataclass(frozen=True)
class SourceFile:
    local_path: Path
    relative_path: str
    scope: str = "version"
    published_path: str | None = None

    @property
    def mutable(self) -> bool:
        return self.relative_path.lower().endswith(".json")

    @property
    def content_type(self) -> str:
        if self.mutable:
            return "application/json; charset=utf-8"
        explicit = {
            ".webp": "image/webp",
            ".png": "image/png",
            ".svg": "image/svg+xml",
            ".mp3": "audio/mpeg",
        }.get(self.local_path.suffix.casefold())
        if explicit:
            return explicit
        guessed, _ = mimetypes.guess_type(self.relative_path)
        return guessed or "application/octet-stream"


@dataclass
class ValidationReport:
    source_dir: Path
    files: list[SourceFile] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    referenced_audio_count: int = 0
    audio_file_count: int = 0
    orphan_audio_count: int = 0
    total_bytes: int = 0

    @property
    def valid(self) -> bool:
        return not self.errors

    @property
    def json_file_count(self) -> int:
        return sum(item.mutable for item in self.files)

    @property
    def immutable_file_count(self) -> int:
        return len(self.files) - self.json_file_count


@dataclass(frozen=True)
class InventoryRecord:
    relative_path: str
    local_path: Path | None
    size: int
    sha256: str
    content_type: str
    mutable: bool
    scope: str = "version"
    published_path: str | None = None
    present_in_source: bool = True

    def to_json(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "size": self.size,
            "sha256": self.sha256,
            "contentType": self.content_type,
            "mutable": self.mutable,
        }
        if not self.present_in_source:
            result["presentInSource"] = False
        if self.scope != "version":
            result["scope"] = self.scope
        if self.published_path:
            result["publishedPath"] = self.published_path
        return result


@dataclass
class PublishPlan:
    validation: ValidationReport
    local_records: dict[str, InventoryRecord] = field(default_factory=dict)
    remote_inventory: dict[str, Any] | None = None
    upload_new: list[InventoryRecord] = field(default_factory=list)
    upload_changed_json: list[InventoryRecord] = field(default_factory=list)
    unchanged: list[InventoryRecord] = field(default_factory=list)
    immutable_conflicts: list[tuple[InventoryRecord, dict[str, Any]]] = field(default_factory=list)
    remote_only_json: list[str] = field(default_factory=list)
    retained_remote_binaries: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def can_publish(self) -> bool:
        return self.validation.valid and not self.immutable_conflicts

    @property
    def upload_records(self) -> list[InventoryRecord]:
        return [*self.upload_new, *self.upload_changed_json]

    @property
    def changed_json_paths(self) -> list[str]:
        return [item.relative_path for item in self.upload_changed_json]

    @property
    def has_content_changes(self) -> bool:
        return bool(self.upload_records)


@dataclass
class PublisherSettings:
    source_dir: Path
    game: str
    version: str
    label: str
    bucket: str = ""
    endpoint_url: str = ""
    cdn_base_url: str = "https://cdn.vlviewer.com"
    state_dir: Path | None = None
    concurrency: int = 12
    zone_id: str = ""
    promote_to_latest: bool = True
    hidden: bool = False

    def __post_init__(self) -> None:
        self.source_dir = Path(self.source_dir).expanduser().resolve()
        if self.state_dir is not None:
            self.state_dir = Path(self.state_dir).expanduser().resolve()
        self.game = self.game.strip().lower()
        self.version = self.version.strip().lower()
        self.label = self.label.strip()
        self.bucket = self.bucket.strip()
        self.endpoint_url = self.endpoint_url.strip().rstrip("/")
        self.cdn_base_url = self.cdn_base_url.strip().rstrip("/")
        self.zone_id = self.zone_id.strip()
        self.concurrency = max(1, min(int(self.concurrency), 64))

    @property
    def version_prefix(self) -> str:
        return f"{self.game}/versions/{self.version}"

    @property
    def inventory_key(self) -> str:
        return f"{self.version_prefix}/publish-inventory.json"

    @property
    def release_key(self) -> str:
        return f"{self.version_prefix}/release.json"

    @property
    def game_manifest_key(self) -> str:
        return f"{self.game}/manifest.json"

    @property
    def game_default_categories_key(self) -> str:
        return f"{self.game}/categories.json"

    @property
    def game_characters_key(self) -> str:
        return f"{self.game}/characters.json"

    @property
    def game_character_names_key(self) -> str:
        return f"{self.game}/character-names.json"

    def public_url(self, key: str) -> str:
        return f"{self.cdn_base_url}/{key}"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def _sorted_character_names(values: Iterable[Any]) -> list[str]:
    names: dict[str, str] = {}
    for value in values:
        if not isinstance(value, str):
            continue
        name = value.strip()
        if not name or name.casefold() == "self":
            continue
        names.setdefault(name.casefold(), name)
    return sorted(names.values(), key=lambda item: (item.casefold(), item))


def collect_content_characters(
    conversations: Any,
    voicelines: Any,
) -> list[str]:
    """Collect every character route referenced by one content version."""
    names: list[Any] = []
    if isinstance(voicelines, dict):
        for speaker, targets in voicelines.items():
            names.append(speaker)
            if isinstance(targets, dict):
                names.extend(
                    target
                    for target, target_data in targets.items()
                    if isinstance(target_data, dict)
                )

    conversation_list = (
        conversations.get("conversations", [])
        if isinstance(conversations, dict)
        else []
    )
    if isinstance(conversation_list, list):
        for conversation in conversation_list:
            if not isinstance(conversation, dict):
                continue
            names.extend((conversation.get("character1"), conversation.get("character2")))
            speakers = conversation.get("speakers", [])
            if isinstance(speakers, list):
                names.extend(speakers)
            lines = conversation.get("lines", [])
            if isinstance(lines, list):
                names.extend(
                    line.get("speaker")
                    for line in lines
                    if isinstance(line, dict)
                )
    return _sorted_character_names(names)


def game_characters_payload(
    game: str,
    version_characters: dict[str, Iterable[Any]],
    version_order: Iterable[str],
) -> dict[str, Any]:
    """Build the small mutable route manifest consumed by static website builds."""
    ordered_versions: dict[str, list[str]] = {}
    all_names: list[str] = []
    for version_id in version_order:
        if not isinstance(version_id, str) or not version_id:
            continue
        characters = _sorted_character_names(version_characters.get(version_id, []))
        ordered_versions[version_id] = characters
        all_names.extend(characters)
    return {
        "schemaVersion": 1,
        "game": game,
        "updatedAt": _utc_now(),
        "characters": _sorted_character_names(all_names),
        "versions": ordered_versions,
    }


def _add_tree(
    files: dict[str, SourceFile],
    source_dir: Path,
    remote_dir: str,
    errors: list[str],
    *,
    scope: str = "version",
    published_dir: str | None = None,
) -> None:
    if not source_dir.is_dir():
        return
    for local_path in sorted(source_dir.rglob("*")):
        if not local_path.is_file():
            continue
        relative_suffix = local_path.relative_to(source_dir).as_posix()
        relative_path = str(PurePosixPath(remote_dir, relative_suffix))
        if relative_path in files:
            errors.append(f"Two source files map to the same published path: {relative_path}")
            continue
        published_path = (
            str(PurePosixPath(published_dir, relative_suffix))
            if published_dir is not None
            else None
        )
        files[relative_path] = SourceFile(
            local_path,
            relative_path,
            scope=scope,
            published_path=published_path,
        )


def discover_version_files(source_dir: Path) -> tuple[list[SourceFile], list[str], list[str]]:
    """Map the existing website version layout to the public R2 layout."""
    source_dir = Path(source_dir).expanduser().resolve()
    errors: list[str] = []
    warnings: list[str] = []
    files: dict[str, SourceFile] = {}

    direct_files = {
        "all_conversations.json": "conversations.json",
        "all_voicelines.json": "voicelines.json",
        "coverage.json": "coverage.json",
    }
    for local_name, relative_path in direct_files.items():
        local_path = source_dir / local_name
        if not local_path.is_file():
            errors.append(f"Required file is missing: {local_name}")
            continue
        files[relative_path] = SourceFile(local_path, relative_path)

    categories_path = source_dir / "categories.json"
    if categories_path.is_file():
        files["categories.json"] = SourceFile(categories_path, "categories.json")
    else:
        warnings.append(
            "Version categories.json is missing; this version will inherit the game's default categories."
        )

    character_names_overlay_path = source_dir / "character-names-overlay.json"
    if character_names_overlay_path.is_file():
        files["character-names.json"] = SourceFile(
            character_names_overlay_path,
            "character-names.json",
        )

    audio_dir = source_dir / "Audio"
    shared_audio_dir = source_dir / "SharedAudio"
    if audio_dir.is_dir():
        _add_tree(files, audio_dir, "audio", errors)
    if shared_audio_dir.is_dir():
        _add_tree(
            files,
            shared_audio_dir,
            "shared-audio",
            errors,
            scope="game",
            published_dir="audio",
        )
    if not audio_dir.is_dir() and not shared_audio_dir.is_dir():
        errors.append("Required audio directory is missing: Audio or SharedAudio")

    localization_dir = source_dir / "Localization"
    if localization_dir.is_dir():
        _add_tree(files, localization_dir, "localization", errors)
    else:
        warnings.append("Official localization directory is missing: Localization")

    fan_localization_dir = source_dir / "FanLocalization"
    if fan_localization_dir.is_dir():
        _add_tree(files, fan_localization_dir, "fan-localization", errors)
    else:
        warnings.append("Fan localization directory is missing: FanLocalization")

    default_icons_dir = source_dir / "IconPacks" / "default"
    if default_icons_dir.is_dir():
        _add_tree(files, default_icons_dir, "icons/default", errors)
    else:
        warnings.append("Default icon override directory is missing: IconPacks/default")

    character_name_images_dir = source_dir / "CharacterNameImages"
    if character_name_images_dir.is_dir():
        _add_tree(files, character_name_images_dir, "character-name-images", errors)
    else:
        warnings.append("Character-name image directory is missing: CharacterNameImages")

    character_select_backgrounds_dir = source_dir / "CharacterSelectBackgrounds"
    if character_select_backgrounds_dir.is_dir():
        _add_tree(
            files,
            character_select_backgrounds_dir,
            "character-select-backgrounds",
            errors,
        )
    else:
        warnings.append(
            "Character-select background directory is missing: CharacterSelectBackgrounds"
        )

    return sorted(files.values(), key=lambda item: item.relative_path), errors, warnings


def _collect_audio_references(
    value: Any,
    legacy_output: set[str],
    shared_output: set[str],
) -> None:
    if isinstance(value, dict):
        filename = value.get("filename")
        if isinstance(filename, str) and filename.strip().lower().endswith(".mp3"):
            audio_key = value.get("audioKey")
            if isinstance(audio_key, str) and audio_key.strip():
                shared_output.add(
                    PurePosixPath(audio_key.strip().replace("\\", "/")).as_posix()
                )
            else:
                legacy_output.add(
                    PurePosixPath(filename.strip().replace("\\", "/")).as_posix()
                )
        for child in value.values():
            _collect_audio_references(child, legacy_output, shared_output)
    elif isinstance(value, list):
        for child in value:
            _collect_audio_references(child, legacy_output, shared_output)


def _validate_icon_manifest(source_dir: Path, report: ValidationReport) -> None:
    manifest_path = source_dir / "IconPacks" / "default" / "manifest.json"
    if not manifest_path.is_file():
        return
    try:
        manifest = _read_json(manifest_path)
    except Exception as exc:
        report.errors.append(f"Invalid IconPacks/default/manifest.json: {exc}")
        return

    referenced: set[str] = set()

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
        elif isinstance(value, str) and value.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".svg")):
            referenced.add(value.replace("/", os.sep))

    walk(manifest.get("icons", {}))
    walk(manifest.get("fallbackIcon"))
    missing = [item for item in referenced if not (manifest_path.parent / item).is_file()]
    if missing:
        sample = ", ".join(sorted(missing)[:8])
        report.errors.append(
            f"Default icon manifest references {len(missing)} missing image(s): {sample}"
        )


def _validate_character_name_images_manifest(
    source_dir: Path,
    report: ValidationReport,
) -> None:
    root = source_dir / "CharacterNameImages"
    manifest_path = root / "manifest.json"
    if not root.is_dir():
        return
    if not manifest_path.is_file():
        report.errors.append("CharacterNameImages is present but manifest.json is missing.")
        return
    try:
        manifest = _read_json(manifest_path)
    except Exception as exc:
        report.errors.append(f"Invalid CharacterNameImages/manifest.json: {exc}")
        return
    if not isinstance(manifest, dict) or manifest.get("schemaVersion") != 1:
        report.errors.append("CharacterNameImages/manifest.json must use schemaVersion 1.")
        return
    languages = manifest.get("languages")
    if not isinstance(languages, dict):
        report.errors.append("CharacterNameImages/manifest.json languages must be an object.")
        return

    referenced: set[str] = set()
    for language, entries in languages.items():
        if not isinstance(language, str) or not VERSION_ID_PATTERN.fullmatch(language):
            report.errors.append(f"Character-name image language is invalid: {language!r}")
            continue
        if not isinstance(entries, dict):
            report.errors.append(f"Character-name image language {language!r} must contain an object.")
            continue
        for character, asset in entries.items():
            if not isinstance(character, str) or not character.strip() or not isinstance(asset, dict):
                report.errors.append(
                    f"Character-name image entry in {language!r} has an invalid character or asset."
                )
                continue
            relative = asset.get("path")
            width = asset.get("width")
            height = asset.get("height")
            if (
                not isinstance(relative, str)
                or "\\" in relative
                or "%" in relative
                or "?" in relative
                or "#" in relative
                or re.match(r"^[a-z][a-z0-9+.-]*:", relative, re.IGNORECASE)
                or PurePosixPath(relative).is_absolute()
                or any(part in ("", ".", "..") for part in PurePosixPath(relative).parts)
                or not relative.casefold().endswith(".webp")
            ):
                report.errors.append(
                    f"Character-name image {language}/{character} has an unsafe or non-WebP path."
                )
                continue
            if (
                not isinstance(width, int)
                or isinstance(width, bool)
                or width < 1
                or not isinstance(height, int)
                or isinstance(height, bool)
                or height < 1
            ):
                report.errors.append(
                    f"Character-name image {language}/{character} has invalid dimensions."
                )
                continue
            referenced.add(PurePosixPath(relative).as_posix())

    missing = sorted(path for path in referenced if not (root / Path(*path.split("/"))).is_file())
    if missing:
        report.errors.append(
            f"Character-name image manifest references {len(missing)} missing WebP file(s): "
            + ", ".join(missing[:10])
        )
    unexpected = sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file()
        and path != manifest_path
        and path.suffix.casefold() != ".webp"
    )
    if unexpected:
        report.errors.append(
            "CharacterNameImages contains non-WebP assets: " + ", ".join(unexpected[:10])
        )


def _validate_character_select_backgrounds_manifest(
    source_dir: Path,
    report: ValidationReport,
) -> None:
    root = source_dir / "CharacterSelectBackgrounds"
    manifest_path = root / "manifest.json"
    if not root.is_dir():
        return
    if not manifest_path.is_file():
        report.errors.append("CharacterSelectBackgrounds is present but manifest.json is missing.")
        return
    try:
        manifest = _read_json(manifest_path)
    except Exception as exc:
        report.errors.append(f"Invalid CharacterSelectBackgrounds/manifest.json: {exc}")
        return
    if not isinstance(manifest, dict) or manifest.get("schemaVersion") != 1:
        report.errors.append("CharacterSelectBackgrounds/manifest.json must use schemaVersion 1.")
        return
    backgrounds = manifest.get("backgrounds")
    if not isinstance(backgrounds, dict) or not backgrounds:
        report.errors.append(
            "CharacterSelectBackgrounds/manifest.json backgrounds must be a non-empty object."
        )
        return

    referenced: set[str] = set()
    for character, asset in backgrounds.items():
        if not isinstance(character, str) or not character.strip() or not isinstance(asset, dict):
            report.errors.append("Character-select background has an invalid character or asset.")
            continue
        relative = asset.get("path")
        width = asset.get("width")
        height = asset.get("height")
        accent_color = asset.get("accentColor")
        if (
            not isinstance(relative, str)
            or "\\" in relative
            or "%" in relative
            or "?" in relative
            or "#" in relative
            or re.match(r"^[a-z][a-z0-9+.-]*:", relative, re.IGNORECASE)
            or PurePosixPath(relative).is_absolute()
            or any(part in ("", ".", "..") for part in PurePosixPath(relative).parts)
            or not relative.casefold().endswith(".webp")
        ):
            report.errors.append(
                f"Character-select background {character!r} has an unsafe or non-WebP path."
            )
            continue
        if (
            not isinstance(width, int)
            or isinstance(width, bool)
            or width < 1
            or not isinstance(height, int)
            or isinstance(height, bool)
            or height < 1
        ):
            report.errors.append(
                f"Character-select background {character!r} has invalid dimensions."
            )
            continue
        if not isinstance(accent_color, str) or not re.fullmatch(
            r"#[0-9a-fA-F]{6}", accent_color
        ):
            report.errors.append(
                f"Character-select background {character!r} has an invalid accent color."
            )
            continue
        referenced.add(PurePosixPath(relative).as_posix())

    missing = sorted(path for path in referenced if not (root / Path(*path.split("/"))).is_file())
    if missing:
        report.errors.append(
            f"Character-select background manifest references {len(missing)} missing WebP file(s): "
            + ", ".join(missing[:10])
        )
    unexpected = sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file()
        and path != manifest_path
        and path.suffix.casefold() != ".webp"
    )
    if unexpected:
        report.errors.append(
            "CharacterSelectBackgrounds contains non-WebP assets: "
            + ", ".join(unexpected[:10])
        )


def _validate_categories_manifest(value: Any, report: ValidationReport) -> None:
    if value is None:
        return
    if not isinstance(value, dict):
        report.errors.append("categories.json must contain a JSON object")
        return
    if value.get("schemaVersion") != 1:
        report.errors.append("categories.json schemaVersion must be 1")
    default_category = value.get("defaultCategory")
    categories = value.get("categories")
    if not isinstance(default_category, str) or not default_category.strip():
        report.errors.append("categories.json must contain a non-empty defaultCategory")
    if not isinstance(categories, list):
        report.errors.append("categories.json must contain a categories array")
        return

    names: set[str] = set()
    assigned: set[str] = set()
    default_visible = False
    for index, category in enumerate(categories):
        if not isinstance(category, dict):
            report.errors.append(f"categories.json categories[{index}] must be an object")
            continue
        name = category.get("name")
        characters = category.get("characters")
        if not isinstance(name, str) or not name.strip():
            report.errors.append(f"categories.json categories[{index}] needs a non-empty name")
            continue
        normalized_name = name.strip().casefold()
        if normalized_name in names:
            report.errors.append(f"categories.json contains duplicate category name: {name}")
        names.add(normalized_name)
        if (
            isinstance(default_category, str)
            and name.strip().casefold() == default_category.strip().casefold()
            and category.get("hidden") is not True
        ):
            default_visible = True
        if not isinstance(characters, list) or any(
            not isinstance(character, str) or not character.strip() for character in characters
        ):
            report.errors.append(
                f"categories.json category {name!r} must contain a characters string array"
            )
            continue
        for character in characters:
            normalized_character = character.strip().casefold()
            if normalized_character in assigned:
                report.errors.append(
                    f"categories.json assigns character more than once: {character}"
                )
            assigned.add(normalized_character)
    if isinstance(default_category, str) and default_category.strip() and not default_visible:
        report.errors.append(
            "categories.json defaultCategory must name a visible category in categories"
        )


def _validate_character_names_manifest(
    value: Any,
    report: ValidationReport,
    expected_game: str | None = None,
) -> None:
    if value is None:
        return
    if not isinstance(value, dict):
        report.errors.append("character-names.json must contain a JSON object")
        return
    if value.get("schemaVersion") != 1:
        report.errors.append("character-names.json schemaVersion must be 1")
    game = value.get("game")
    if not isinstance(game, str) or not game.strip():
        report.errors.append("character-names.json must contain a non-empty game")
    elif expected_game and game.strip().casefold() != expected_game.casefold():
        report.errors.append(
            f"character-names.json game must be {expected_game!r}, received {game!r}"
        )
    names = value.get("names")
    if not isinstance(names, dict) or not names:
        report.errors.append("character-names.json must contain a non-empty names object")
        return
    seen: set[str] = set()
    for key, display_name in names.items():
        if not isinstance(key, str) or not key.strip():
            report.errors.append("character-names.json contains an empty character key")
            continue
        normalized = " ".join(key.strip().casefold().split())
        if normalized in seen:
            report.errors.append(f"character-names.json contains a duplicate key: {key}")
        seen.add(normalized)
        if not isinstance(display_name, str) or not display_name.strip():
            report.errors.append(
                f"character-names.json display name for {key!r} must be a non-empty string"
            )


def validate_version_source(
    source_dir: Path,
    expected_game: str | None = None,
) -> ValidationReport:
    source_dir = Path(source_dir).expanduser().resolve()
    report = ValidationReport(source_dir=source_dir)
    if not source_dir.is_dir():
        report.errors.append(f"Version source directory does not exist: {source_dir}")
        return report

    files, errors, warnings = discover_version_files(source_dir)
    report.files = files
    report.errors.extend(errors)
    report.warnings.extend(warnings)

    parsed_json: dict[str, Any] = {}
    for item in files:
        try:
            report.total_bytes += item.local_path.stat().st_size
        except OSError as exc:
            report.errors.append(f"Cannot read {item.local_path}: {exc}")
            continue
        if not item.mutable:
            continue
        try:
            parsed_json[item.relative_path] = _read_json(item.local_path)
        except Exception as exc:
            report.errors.append(f"Invalid JSON in {item.relative_path}: {exc}")

    conversation_data = parsed_json.get("conversations.json")
    if conversation_data is not None:
        if not isinstance(conversation_data, dict) or not isinstance(
            conversation_data.get("conversations"), list
        ):
            report.errors.append("conversations.json does not contain a conversations array")

    voice_line_data = parsed_json.get("voicelines.json")
    if voice_line_data is not None and not isinstance(voice_line_data, dict):
        report.errors.append("voicelines.json must contain a JSON object")

    coverage_data = parsed_json.get("coverage.json")
    if coverage_data is not None and not isinstance(coverage_data, dict):
        report.errors.append("coverage.json must contain a JSON object")

    _validate_categories_manifest(parsed_json.get("categories.json"), report)

    version_character_names = parsed_json.get("character-names.json")
    if version_character_names is not None:
        _validate_character_names_manifest(
            version_character_names,
            report,
            expected_game,
        )

    character_names_path = source_dir / "character-names.json"
    if character_names_path.is_file():
        try:
            character_names = _read_json(character_names_path)
        except Exception as exc:
            report.errors.append(f"Invalid JSON in character-names.json: {exc}")
        else:
            _validate_character_names_manifest(character_names, report)

    referenced_audio: set[str] = set()
    referenced_shared_audio: set[str] = set()
    if conversation_data is not None:
        _collect_audio_references(
            conversation_data, referenced_audio, referenced_shared_audio
        )
    if voice_line_data is not None:
        _collect_audio_references(
            voice_line_data, referenced_audio, referenced_shared_audio
        )

    audio_names = {
        PurePosixPath(item.relative_path).relative_to("audio").as_posix()
        for item in files
        if item.scope == "version"
        and item.relative_path.lower().startswith("audio/")
        and item.relative_path.lower().endswith(".mp3")
    }
    shared_audio_names = {
        PurePosixPath(item.published_path or "").relative_to("audio").as_posix()
        for item in files
        if item.scope == "game"
        and isinstance(item.published_path, str)
        and item.published_path.lower().startswith("audio/")
        and item.published_path.lower().endswith(".mp3")
    }
    invalid_shared_keys = sorted(
        key for key in referenced_shared_audio
        if not re.fullmatch(r"sha256/[0-9a-f]{2}/[0-9a-f]{64}\.mp3", key)
    )
    if invalid_shared_keys:
        report.errors.append(
            "JSON contains invalid shared audioKey value(s): "
            + ", ".join(invalid_shared_keys[:10])
        )
    report.referenced_audio_count = len(referenced_audio) + len(referenced_shared_audio)
    report.audio_file_count = len(audio_names) + len(shared_audio_names)
    report.orphan_audio_count = (
        len(audio_names - referenced_audio)
        + len(shared_audio_names - referenced_shared_audio)
    )
    missing_audio = sorted(referenced_audio - audio_names)
    missing_shared_audio = sorted(referenced_shared_audio - shared_audio_names)
    missing_audio.extend(missing_shared_audio)
    if missing_audio:
        sample = ", ".join(missing_audio[:10])
        report.errors.append(
            f"JSON references {len(missing_audio)} missing audio file(s): {sample}"
        )
    if report.orphan_audio_count:
        report.warnings.append(
            f"Audio contains {report.orphan_audio_count} unreferenced MP3 file(s); they will still be published."
        )

    for required_manifest in ("localization/manifest.json",):
        if not any(item.relative_path == required_manifest for item in files):
            report.warnings.append(f"Published content will not include {required_manifest}")

    _validate_icon_manifest(source_dir, report)
    _validate_character_name_images_manifest(source_dir, report)
    _validate_character_select_backgrounds_manifest(source_dir, report)
    return report


class HashCache:
    def __init__(self, path: Path | None, source_dir: Path) -> None:
        self.path = path
        self.source_dir = str(source_dir)
        self.entries: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        if path and path.is_file():
            try:
                data = _read_json(path)
                if data.get("sourceDir") == self.source_dir:
                    self.entries = data.get("files", {})
            except Exception:
                self.entries = {}

    def get(self, item: SourceFile) -> str | None:
        try:
            stat = item.local_path.stat()
        except OSError:
            return None
        cached = self.entries.get(item.relative_path)
        if not cached:
            return None
        if cached.get("size") != stat.st_size or cached.get("mtimeNs") != stat.st_mtime_ns:
            return None
        value = cached.get("sha256")
        return value if isinstance(value, str) else None

    def put(self, item: SourceFile, sha256: str) -> None:
        stat = item.local_path.stat()
        with self._lock:
            self.entries[item.relative_path] = {
                "size": stat.st_size,
                "mtimeNs": stat.st_mtime_ns,
                "sha256": sha256,
            }

    def save(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_name(
            f".{self.path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
        )
        payload = {
            "schemaVersion": 1,
            "sourceDir": self.source_dir,
            "updatedAt": _utc_now(),
            "files": self.entries,
        }
        temp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        try:
            for attempt in range(5):
                try:
                    temp_path.replace(self.path)
                    break
                except PermissionError:
                    if attempt == 4:
                        raise
                    time.sleep(0.1 * (attempt + 1))
        finally:
            temp_path.unlink(missing_ok=True)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _hash_files(
    report: ValidationReport,
    settings: PublisherSettings,
    progress: ProgressCallback | None = None,
) -> dict[str, InventoryRecord]:
    cache_path = None
    if settings.state_dir:
        cache_path = settings.state_dir / settings.game / settings.version / "local-hashes.json"
    cache = HashCache(cache_path, report.source_dir)
    records: dict[str, InventoryRecord] = {}
    pending: list[SourceFile] = []

    def validate_shared_path(item: SourceFile, sha256: str) -> None:
        if item.scope != "game" or not item.published_path:
            return
        expected_hash = PurePosixPath(item.published_path).stem.casefold()
        if expected_hash != sha256.casefold():
            raise PublisherError(
                f"Shared audio path hash does not match its bytes: {item.relative_path}"
            )

    for item in report.files:
        cached_hash = cache.get(item)
        if cached_hash:
            validate_shared_path(item, cached_hash)
            stat = item.local_path.stat()
            records[item.relative_path] = InventoryRecord(
                relative_path=item.relative_path,
                local_path=item.local_path,
                size=stat.st_size,
                sha256=cached_hash,
                content_type=item.content_type,
                mutable=item.mutable,
                scope=item.scope,
                published_path=item.published_path,
            )
        else:
            pending.append(item)

    if progress:
        progress(
            f"Hash inventory: {len(records):,} cached, {len(pending):,} file(s) to hash."
        )

    completed = 0
    with ThreadPoolExecutor(max_workers=min(settings.concurrency, 8)) as executor:
        futures = {executor.submit(_sha256_file, item.local_path): item for item in pending}
        for future in as_completed(futures):
            item = futures[future]
            try:
                sha256 = future.result()
                stat = item.local_path.stat()
            except Exception as exc:
                raise PublisherError(f"Failed to hash {item.local_path}: {exc}") from exc
            validate_shared_path(item, sha256)
            cache.put(item, sha256)
            records[item.relative_path] = InventoryRecord(
                relative_path=item.relative_path,
                local_path=item.local_path,
                size=stat.st_size,
                sha256=sha256,
                content_type=item.content_type,
                mutable=item.mutable,
                scope=item.scope,
                published_path=item.published_path,
            )
            completed += 1
            if progress and (completed % 1000 == 0 or completed == len(pending)):
                progress(f"Hashed {completed:,}/{len(pending):,} uncached file(s).")

    cache.save()
    return records


def build_publish_plan(
    settings: PublisherSettings,
    remote_inventory: dict[str, Any] | None = None,
    progress: ProgressCallback | None = None,
) -> PublishPlan:
    if not GAME_KEY_PATTERN.fullmatch(settings.game):
        raise PublisherError(
            "Game key must begin with a letter or number and contain only lowercase letters, numbers, or hyphens."
        )
    if not VERSION_ID_PATTERN.fullmatch(settings.version):
        raise PublisherError(
            "Version ID must begin with a letter or number and contain only lowercase letters, numbers, dots, underscores, or hyphens."
        )
    if not settings.label:
        raise PublisherError("A human-readable version label is required.")
    if settings.hidden and settings.promote_to_latest:
        raise PublisherError(
            "A version cannot be hidden and promoted to latest in the same publication."
        )

    validation = validate_version_source(settings.source_dir, settings.game)
    plan = PublishPlan(validation=validation, remote_inventory=remote_inventory)
    if not validation.valid:
        return plan

    plan.local_records = _hash_files(validation, settings, progress)
    remote_files = (remote_inventory or {}).get("files", {})
    if not isinstance(remote_files, dict):
        remote_files = {}

    for relative_path, local_record in sorted(plan.local_records.items()):
        remote_record = remote_files.get(relative_path)
        if not isinstance(remote_record, dict):
            plan.upload_new.append(local_record)
        elif remote_record.get("sha256") == local_record.sha256:
            plan.unchanged.append(local_record)
        elif local_record.mutable:
            plan.upload_changed_json.append(local_record)
        else:
            plan.immutable_conflicts.append((local_record, remote_record))

    for relative_path, remote_record in remote_files.items():
        if relative_path in plan.local_records or not isinstance(remote_record, dict):
            continue
        if relative_path.lower().endswith(".json"):
            plan.remote_only_json.append(relative_path)
        else:
            plan.retained_remote_binaries[relative_path] = remote_record

    return plan


def inventory_payload(
    settings: PublisherSettings,
    plan: PublishPlan,
    content_revision: int,
) -> dict[str, Any]:
    files = {path: record.to_json() for path, record in sorted(plan.local_records.items())}
    for path, record in sorted(plan.retained_remote_binaries.items()):
        retained = dict(record)
        retained["mutable"] = False
        retained["presentInSource"] = False
        files[path] = retained
    return {
        "schemaVersion": INVENTORY_SCHEMA_VERSION,
        "game": settings.game,
        "version": settings.version,
        "contentRevision": content_revision,
        "generatedAt": _utc_now(),
        "files": files,
    }


def version_manifest_entry(
    settings: PublisherSettings,
    content_revision: int,
    existing: dict[str, Any] | None = None,
    has_categories: bool = False,
    has_character_name_images: bool = False,
    has_character_select_backgrounds: bool = False,
    has_character_names: bool = False,
) -> dict[str, Any]:
    existing = existing or {}
    base = f"{settings.cdn_base_url}/{settings.version_prefix}"
    now = _utc_now()
    entry = {
        "id": settings.version,
        "label": settings.label,
        "publishedAt": existing.get("publishedAt") or now,
        "updatedAt": now,
        "contentRevision": content_revision,
        "hidden": settings.hidden,
        "conversationUrl": f"{base}/conversations.json",
        "voiceLineUrl": f"{base}/voicelines.json",
        "audioBaseUrl": f"{base}/audio/",
        "localizationManifestUrl": f"{base}/localization/manifest.json",
        "fanLocalizationManifestUrl": f"{base}/fan-localization/manifest.json",
        "coverageUrl": f"{base}/coverage.json",
        "iconOverridesUrl": f"{base}/icons/default/manifest.json",
    }
    if has_categories:
        entry["categoriesUrl"] = f"{base}/categories.json"
    if has_character_name_images:
        entry["characterNameImagesUrl"] = f"{base}/character-name-images/manifest.json"
    if has_character_select_backgrounds:
        entry["characterSelectBackgroundsUrl"] = (
            f"{base}/character-select-backgrounds/manifest.json"
        )
    if has_character_names:
        entry["characterNamesUrl"] = f"{base}/character-names.json"
    return entry


class R2Publisher:
    def __init__(
        self,
        settings: PublisherSettings,
        progress: ProgressCallback | None = None,
    ) -> None:
        self.settings = settings
        self.progress = progress or (lambda _message: None)
        self._client: Any = None

    def _log(self, message: str) -> None:
        self.progress(message)

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        if not self.settings.bucket:
            raise PublisherError("R2 bucket name is required.")
        if not self.settings.endpoint_url:
            raise PublisherError("R2 endpoint URL is required.")
        access_key = os.environ.get("R2_ACCESS_KEY_ID", "").strip()
        secret_key = os.environ.get("R2_SECRET_ACCESS_KEY", "").strip()
        if not access_key or not secret_key:
            raise PublisherError(
                "Set R2_ACCESS_KEY_ID and R2_SECRET_ACCESS_KEY in the environment before connecting."
            )
        try:
            import boto3  # type: ignore
            from botocore.config import Config  # type: ignore
        except ImportError as exc:
            raise PublisherError(
                "R2 upload support requires boto3. Launch with run_publisher_gui.bat so "
                "missing requirements are installed automatically."
            ) from exc
        self._client = boto3.client(
            service_name="s3",
            endpoint_url=self.settings.endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name="auto",
            config=Config(
                max_pool_connections=max(16, self.settings.concurrency * 2),
                retries={"max_attempts": 5, "mode": "standard"},
            ),
        )
        return self._client

    def test_connection(self) -> None:
        client = self._get_client()
        try:
            client.head_bucket(Bucket=self.settings.bucket)
        except Exception as exc:
            raise PublisherError(f"Could not access R2 bucket {self.settings.bucket!r}: {exc}") from exc
        self._log(f"Connected to R2 bucket {self.settings.bucket!r}.")

    @staticmethod
    def _missing_object(exc: Exception) -> bool:
        response = getattr(exc, "response", {})
        code = str(response.get("Error", {}).get("Code", ""))
        status = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        return code in {"NoSuchKey", "404", "NotFound"} or status == 404

    def _get_remote_json(self, key: str) -> dict[str, Any] | None:
        client = self._get_client()
        try:
            response = client.get_object(Bucket=self.settings.bucket, Key=key)
        except Exception as exc:
            if self._missing_object(exc):
                return None
            raise PublisherError(f"Failed to read r2://{self.settings.bucket}/{key}: {exc}") from exc
        try:
            data = json.loads(response["Body"].read().decode("utf-8-sig"))
        except Exception as exc:
            raise PublisherError(f"Remote JSON is invalid at {key}: {exc}") from exc
        if not isinstance(data, dict):
            raise PublisherError(f"Remote JSON must contain an object: {key}")
        return data

    def load_remote_inventory(self) -> dict[str, Any] | None:
        return self._get_remote_json(self.settings.inventory_key)

    def load_game_manifest(self) -> dict[str, Any]:
        """Load the editable game catalog, returning an empty catalog if unpublished."""
        manifest = self._get_remote_json(self.settings.game_manifest_key) or {
            "schemaVersion": MANIFEST_SCHEMA_VERSION,
            "game": self.settings.game,
            "latestVersion": "",
            "versions": [],
        }
        versions = manifest.get("versions")
        if not isinstance(versions, list):
            raise PublisherError("Remote game manifest has an invalid versions field.")
        seen: set[str] = set()
        for item in versions:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                raise PublisherError("Every game manifest version must be an object with an ID.")
            version_id = item["id"]
            if version_id in seen:
                raise PublisherError(f"Game manifest contains duplicate version ID: {version_id}")
            seen.add(version_id)
        return manifest

    def _local_version_characters(self) -> list[str]:
        try:
            conversations = _read_json(self.settings.source_dir / "all_conversations.json")
            voicelines = _read_json(self.settings.source_dir / "all_voicelines.json")
        except Exception as exc:
            raise PublisherError(f"Could not build the character route list: {exc}") from exc
        return collect_content_characters(conversations, voicelines)

    def _local_game_character_names(self) -> dict[str, Any] | None:
        path = self.settings.source_dir / "character-names.json"
        if not path.is_file():
            return None
        try:
            payload = _read_json(path)
        except Exception as exc:
            raise PublisherError(f"Invalid character-names.json: {exc}") from exc
        report = ValidationReport(source_dir=self.settings.source_dir)
        _validate_character_names_manifest(payload, report, self.settings.game)
        if report.errors:
            raise PublisherError(
                "Invalid character-names.json: " + "; ".join(report.errors)
            )
        assert isinstance(payload, dict)
        return payload

    def _local_version_character_names(self) -> dict[str, Any] | None:
        path = self.settings.source_dir / "character-names-overlay.json"
        if not path.is_file():
            return None
        try:
            payload = _read_json(path)
        except Exception as exc:
            raise PublisherError(
                f"Invalid character-names-overlay.json: {exc}"
            ) from exc
        report = ValidationReport(source_dir=self.settings.source_dir)
        _validate_character_names_manifest(payload, report, self.settings.game)
        if report.errors:
            raise PublisherError(
                "Invalid character-names-overlay.json: " + "; ".join(report.errors)
            )
        assert isinstance(payload, dict)
        return payload

    def _remote_version_characters(self, version_id: str) -> list[str]:
        prefix = f"{self.settings.game}/versions/{version_id}"
        conversations = self._get_remote_json(f"{prefix}/conversations.json")
        voicelines = self._get_remote_json(f"{prefix}/voicelines.json")
        if conversations is None or voicelines is None:
            raise PublisherError(
                f"Cannot bootstrap characters.json because version {version_id!r} "
                "is missing conversations.json or voicelines.json."
            )
        return collect_content_characters(conversations, voicelines)

    def _build_game_characters(self, manifest: dict[str, Any]) -> dict[str, Any]:
        versions = manifest.get("versions")
        if not isinstance(versions, list):
            raise PublisherError("Game manifest versions must be a list.")
        version_order = [
            item["id"]
            for item in versions
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        ]

        existing = self._get_remote_json(self.settings.game_characters_key) or {}
        existing_versions = existing.get("versions", {})
        if not isinstance(existing_versions, dict):
            existing_versions = {}
        by_version: dict[str, Iterable[Any]] = {
            version_id: characters
            for version_id, characters in existing_versions.items()
            if isinstance(version_id, str) and isinstance(characters, list)
        }

        for version_id in version_order:
            if version_id == self.settings.version:
                by_version[version_id] = self._local_version_characters()
            elif version_id not in by_version:
                self._log(
                    f"Bootstrapping character routes from published version {version_id!r}..."
                )
                by_version[version_id] = self._remote_version_characters(version_id)

        return game_characters_payload(
            self.settings.game,
            by_version,
            version_order,
        )

    def save_game_manifest(self, manifest: dict[str, Any]) -> dict[str, Any]:
        """Persist visibility/order/latest changes without uploading version content."""
        versions = manifest.get("versions")
        if not isinstance(versions, list):
            raise PublisherError("Game manifest versions must be a list.")
        seen: set[str] = set()
        for item in versions:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                raise PublisherError("Every game manifest version must be an object with an ID.")
            version_id = item["id"]
            if version_id in seen:
                raise PublisherError(f"Game manifest contains duplicate version ID: {version_id}")
            seen.add(version_id)

        latest = manifest.get("latestVersion", "")
        latest_entry = next(
            (item for item in versions if item.get("id") == latest),
            None,
        )
        if latest and latest_entry is None:
            raise PublisherError(f"Latest version does not exist in the catalog: {latest}")
        if latest_entry and latest_entry.get("hidden") is True:
            raise PublisherError(
                "The latest version cannot be hidden. Unhide it or promote another version first."
            )

        manifest.update(
            {
                "schemaVersion": MANIFEST_SCHEMA_VERSION,
                "game": self.settings.game,
                "updatedAt": _utc_now(),
            }
        )
        characters = self._build_game_characters(manifest)
        manifest["charactersUrl"] = self.settings.public_url(
            self.settings.game_characters_key
        )
        character_names = self._local_game_character_names()
        if character_names is not None:
            manifest["characterNamesUrl"] = self.settings.public_url(
                self.settings.game_character_names_key
            )
        # Write the route index first. The manifest must never advertise an
        # object that is not available yet.
        self._put_json(self.settings.game_characters_key, characters)
        if character_names is not None:
            self._put_json(self.settings.game_character_names_key, character_names)
        self._put_json(self.settings.game_manifest_key, manifest)
        try:
            self._purge_urls(
                self.settings.public_url(key)
                for key in (
                    self.settings.game_characters_key,
                    *(
                        [self.settings.game_character_names_key]
                        if character_names is not None
                        else []
                    ),
                    self.settings.game_manifest_key,
                )
            )
        except PublisherError as exc:
            self._log(
                "Warning: version catalog was saved, but CDN purging failed: " + str(exc)
            )
        self._log("Saved the version catalog and all-version character routes.")
        return manifest

    def publish_game_default_categories(
        self,
        categories_path: Path | None = None,
    ) -> dict[str, Any]:
        """Publish categories.json as this game's inherited default only."""
        if not GAME_KEY_PATTERN.fullmatch(self.settings.game):
            raise PublisherError(
                "Game key must begin with a letter or number and contain only lowercase letters, numbers, or hyphens."
            )
        categories_path = (
            Path(categories_path).expanduser().resolve()
            if categories_path is not None
            else self.settings.source_dir / "categories.json"
        )
        if not categories_path.is_file():
            raise PublisherError(
                f"Game default categories file is missing: {categories_path}"
            )
        try:
            categories = _read_json(categories_path)
        except Exception as exc:
            raise PublisherError(f"Invalid categories.json: {exc}") from exc
        report = ValidationReport(source_dir=self.settings.source_dir)
        _validate_categories_manifest(categories, report)
        if report.errors:
            raise PublisherError("Invalid categories.json: " + "; ".join(report.errors))
        assert isinstance(categories, dict)

        manifest = self.load_game_manifest()
        manifest.update(
            {
                "schemaVersion": MANIFEST_SCHEMA_VERSION,
                "game": self.settings.game,
                "defaultCategoriesUrl": self.settings.public_url(
                    self.settings.game_default_categories_key
                ),
                "updatedAt": _utc_now(),
            }
        )
        self._log(f"Updating the default categories for game {self.settings.game!r}...")
        self._put_json(self.settings.game_default_categories_key, categories)
        self._log("Updating the public game manifest last...")
        self._put_json(self.settings.game_manifest_key, manifest)
        try:
            self._purge_urls(
                self.settings.public_url(key)
                for key in (
                    self.settings.game_default_categories_key,
                    self.settings.game_manifest_key,
                )
            )
        except PublisherError as exc:
            self._log(
                "Warning: default categories were published, but CDN purging failed: "
                + str(exc)
            )
        self._log(f"Published the inherited category default for game {self.settings.game!r}.")
        return manifest

    def list_game_content(self) -> list[dict[str, Any]]:
        """List every object in this game's namespace."""
        client = self._get_client()
        prefix = f"{self.settings.game}/"
        objects: list[dict[str, Any]] = []
        continuation: str | None = None
        while True:
            request: dict[str, Any] = {
                "Bucket": self.settings.bucket,
                "Prefix": prefix,
                "MaxKeys": 1000,
            }
            if continuation:
                request["ContinuationToken"] = continuation
            response = client.list_objects_v2(**request)
            for item in response.get("Contents", []):
                if isinstance(item, dict) and isinstance(item.get("Key"), str):
                    objects.append(item)
            if not response.get("IsTruncated"):
                return objects
            continuation = response.get("NextContinuationToken")
            if not isinstance(continuation, str) or not continuation:
                raise PublisherError(
                    "R2 game-content listing was truncated without a continuation token."
                )

    def clear_game_content(self) -> dict[str, int]:
        """Delete only this game's R2 namespace and verify that it is empty."""
        objects = self.list_game_content()
        total_bytes = sum(int(item.get("Size", 0)) for item in objects)
        if not objects:
            self._log(f"No objects exist below {self.settings.game}/.")
            return {"deleted": 0, "bytes": 0}

        client = self._get_client()
        self._log(
            f"Deleting {len(objects):,} object(s) below {self.settings.game}/ "
            f"from R2 bucket {self.settings.bucket!r}..."
        )
        deleted = 0
        for index in range(0, len(objects), 1000):
            batch = objects[index : index + 1000]
            response = client.delete_objects(
                Bucket=self.settings.bucket,
                Delete={
                    "Objects": [{"Key": item["Key"]} for item in batch],
                    "Quiet": True,
                },
            )
            errors = response.get("Errors", [])
            if errors:
                first = errors[0] if isinstance(errors[0], dict) else errors[0]
                raise PublisherError(f"R2 rejected one or more deletions: {first}")
            deleted += len(batch)
            self._log(f"Deleted {deleted:,}/{len(objects):,} object(s).")

        remaining = self.list_game_content()
        if remaining:
            raise PublisherError(
                f"R2 reset verification failed: {len(remaining):,} object(s) remain below "
                f"{self.settings.game}/."
            )
        self._log(
            f"Cleared and verified the R2 namespace {self.settings.game}/ "
            f"({deleted:,} object(s), {format_bytes(total_bytes)})."
        )
        return {"deleted": deleted, "bytes": total_bytes}

    def publish_game_character_names(self) -> dict[str, Any]:
        """Publish the per-game character display-name mapping only."""
        if not GAME_KEY_PATTERN.fullmatch(self.settings.game):
            raise PublisherError(
                "Game key must begin with a letter or number and contain only lowercase letters, numbers, or hyphens."
            )
        character_names = self._local_game_character_names()
        if character_names is None:
            raise PublisherError(
                "Game character-name mapping is missing: "
                f"{self.settings.source_dir / 'character-names.json'}"
            )

        manifest = self.load_game_manifest()
        manifest.update(
            {
                "schemaVersion": MANIFEST_SCHEMA_VERSION,
                "game": self.settings.game,
                "characterNamesUrl": self.settings.public_url(
                    self.settings.game_character_names_key
                ),
                "updatedAt": _utc_now(),
            }
        )
        self._log(f"Updating character display names for game {self.settings.game!r}...")
        self._put_json(self.settings.game_character_names_key, character_names)
        self._log("Updating the public game manifest last...")
        self._put_json(self.settings.game_manifest_key, manifest)
        try:
            self._purge_urls(
                self.settings.public_url(key)
                for key in (
                    self.settings.game_character_names_key,
                    self.settings.game_manifest_key,
                )
            )
        except PublisherError as exc:
            self._log(
                "Warning: character names were published, but CDN purging failed: "
                + str(exc)
            )
        self._log(f"Published character display names for game {self.settings.game!r}.")
        return manifest

    def publish_version_character_names(self) -> dict[str, Any]:
        """Publish only this version's character-name overlay and metadata."""
        if not GAME_KEY_PATTERN.fullmatch(self.settings.game):
            raise PublisherError(
                "Game key must begin with a letter or number and contain only lowercase letters, numbers, or hyphens."
            )
        if not VERSION_ID_PATTERN.fullmatch(self.settings.version):
            raise PublisherError(
                "Version ID must begin with a letter or number and contain only lowercase letters, numbers, dots, underscores, or hyphens."
            )
        character_names = self._local_version_character_names()
        if character_names is None:
            raise PublisherError(
                "Version character-name overlay is missing: "
                f"{self.settings.source_dir / 'character-names-overlay.json'}"
            )

        manifest = self.load_game_manifest()
        versions = manifest.get("versions")
        assert isinstance(versions, list)
        version_index = next(
            (
                index
                for index, item in enumerate(versions)
                if item.get("id") == self.settings.version
            ),
            None,
        )
        if version_index is None:
            raise PublisherError(
                f"Version {self.settings.version!r} is not present in the game manifest."
            )

        inventory = self._get_remote_json(self.settings.inventory_key)
        release = self._get_remote_json(self.settings.release_key)
        if inventory is None or release is None:
            raise PublisherError(
                "The published version is missing its inventory or release record; "
                "use a normal version publication instead."
            )
        files = inventory.get("files")
        if not isinstance(files, dict):
            raise PublisherError("The published version inventory has an invalid files object.")

        body = json.dumps(character_names, indent=2, ensure_ascii=False).encode("utf-8")
        digest = hashlib.sha256(body).hexdigest()
        object_key = f"{self.settings.version_prefix}/character-names.json"
        public_url = self.settings.public_url(object_key)
        current_record = files.get("character-names.json")
        current_entry = versions[version_index]
        if (
            isinstance(current_record, dict)
            and current_record.get("sha256") == digest
            and current_entry.get("characterNamesUrl") == public_url
        ):
            self._log(
                f"Version character display names are already current for {self.settings.version!r}."
            )
            return manifest

        try:
            revision = int(inventory.get("contentRevision", 0)) + 1
        except (TypeError, ValueError):
            revision = 1
        now = _utc_now()
        files["character-names.json"] = {
            "size": len(body),
            "sha256": digest,
            "contentType": "application/json; charset=utf-8",
            "mutable": True,
        }
        inventory.update(
            {
                "contentRevision": revision,
                "generatedAt": now,
                "files": files,
            }
        )
        total_bytes = sum(
            int(item.get("size", 0))
            for item in files.values()
            if isinstance(item, dict)
        )
        release.update(
            {
                "updatedAt": now,
                "contentRevision": revision,
                "characterNamesUrl": public_url,
                "fileCount": len(files),
                "totalBytes": total_bytes,
            }
        )
        updated_entry = {
            **current_entry,
            "updatedAt": now,
            "contentRevision": revision,
            "characterNamesUrl": public_url,
        }
        versions[version_index] = updated_entry
        manifest.update({"updatedAt": now, "versions": versions})

        self._log(
            f"Publishing the character display-name overlay for {self.settings.version!r}..."
        )
        self._put_json(object_key, character_names)
        self._put_json(self.settings.inventory_key, inventory)
        self._put_json(self.settings.release_key, release)
        self._log("Updating the public game manifest last...")
        self._put_json(self.settings.game_manifest_key, manifest)
        try:
            self._purge_urls(
                self.settings.public_url(key)
                for key in (
                    object_key,
                    self.settings.inventory_key,
                    self.settings.release_key,
                    self.settings.game_manifest_key,
                )
            )
        except PublisherError as exc:
            self._log(
                "Warning: version character names were published, but CDN purging failed: "
                + str(exc)
            )
        self._log(
            f"Published version character display names for {self.settings.version!r} "
            f"at content revision {revision}."
        )
        return manifest

    def create_plan(self) -> PublishPlan:
        self._log("Loading the previously published inventory...")
        remote_inventory = self.load_remote_inventory()
        if remote_inventory is None:
            response = self._get_client().list_objects_v2(
                Bucket=self.settings.bucket,
                Prefix=f"{self.settings.version_prefix}/",
                MaxKeys=1,
            )
            if response.get("KeyCount", 0):
                raise PublisherError(
                    "This version prefix already contains objects but has no publish inventory. "
                    "Refusing to overwrite untracked content."
                )
            self._log("No remote inventory exists; this will be a new version upload.")
        else:
            self._log(
                f"Remote inventory revision: {remote_inventory.get('contentRevision', 0)}."
            )
        plan = build_publish_plan(self.settings, remote_inventory, self.progress)
        shared_records = [
            record for record in plan.local_records.values() if record.scope == "game"
        ]
        if shared_records:
            self._log("Checking the game-level shared audio index...")
            existing_keys = self._list_game_shared_audio_keys()
            reused = [
                record for record in plan.upload_new
                if record.scope == "game"
                if self._object_key(record) in existing_keys
            ]
            if reused:
                reused_ids = {id(record) for record in reused}
                plan.upload_new = [
                    record for record in plan.upload_new
                    if id(record) not in reused_ids
                ]
                plan.unchanged.extend(reused)
                self._log(
                    f"Reusing {len(reused):,} shared audio object(s) already in R2."
                )
            missing = [
                record for record in plan.unchanged
                if record.scope == "game"
                and self._object_key(record) not in existing_keys
            ]
            if missing:
                missing_ids = {id(record) for record in missing}
                plan.unchanged = [
                    record for record in plan.unchanged
                    if id(record) not in missing_ids
                ]
                plan.upload_new.extend(missing)
                self._log(
                    f"Repairing {len(missing):,} shared audio object(s) missing from R2."
                )
        return plan

    def _object_key(self, record: InventoryRecord) -> str:
        if record.scope == "game":
            published_path = record.published_path or record.relative_path
            return f"{self.settings.game}/{published_path}"
        return f"{self.settings.version_prefix}/{record.relative_path}"

    def _list_game_shared_audio_keys(self) -> set[str]:
        client = self._get_client()
        prefix = f"{self.settings.game}/audio/sha256/"
        keys: set[str] = set()
        continuation: str | None = None
        while True:
            request: dict[str, Any] = {
                "Bucket": self.settings.bucket,
                "Prefix": prefix,
                "MaxKeys": 1000,
            }
            if continuation:
                request["ContinuationToken"] = continuation
            response = client.list_objects_v2(**request)
            for item in response.get("Contents", []):
                key = item.get("Key") if isinstance(item, dict) else None
                if isinstance(key, str):
                    keys.add(key)
            if not response.get("IsTruncated"):
                return keys
            continuation = response.get("NextContinuationToken")
            if not isinstance(continuation, str) or not continuation:
                raise PublisherError("R2 shared audio listing was truncated without a continuation token.")

    def _upload_file(self, record: InventoryRecord) -> str:
        if not record.local_path:
            raise PublisherError(f"No local source path for {record.relative_path}")
        client = self._get_client()
        key = self._object_key(record)
        if not record.mutable:
            try:
                existing = client.head_object(Bucket=self.settings.bucket, Key=key)
            except Exception as exc:
                if not self._missing_object(exc):
                    raise
            else:
                existing_hash = existing.get("Metadata", {}).get("sha256")
                if existing_hash == record.sha256:
                    return key
                raise PublisherError(
                    f"Immutable object already exists with different or unverifiable bytes: {key}"
                )
        extra_args = {
            "ContentType": record.content_type,
            "CacheControl": (
                MUTABLE_JSON_CACHE_CONTROL if record.mutable else IMMUTABLE_CACHE_CONTROL
            ),
            "Metadata": {"sha256": record.sha256},
        }
        client.upload_file(
            str(record.local_path),
            self.settings.bucket,
            key,
            ExtraArgs=extra_args,
        )
        response = client.head_object(Bucket=self.settings.bucket, Key=key)
        remote_hash = response.get("Metadata", {}).get("sha256")
        if remote_hash != record.sha256:
            raise PublisherError(f"Upload verification failed for {key}")
        return key

    def _put_json(self, key: str, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")
        digest = hashlib.sha256(body).hexdigest()
        self._get_client().put_object(
            Bucket=self.settings.bucket,
            Key=key,
            Body=body,
            ContentType="application/json; charset=utf-8",
            CacheControl=MUTABLE_JSON_CACHE_CONTROL,
            Metadata={"sha256": digest},
        )

    def _next_revision(self, plan: PublishPlan) -> int:
        current = 0
        if plan.remote_inventory:
            try:
                current = int(plan.remote_inventory.get("contentRevision", 0))
            except (TypeError, ValueError):
                current = 0
        return max(1, current + (1 if plan.has_content_changes else 0))

    def _build_game_manifest(
        self,
        content_revision: int,
        has_categories: bool | None = None,
        has_shared_audio: bool = False,
        has_character_name_images: bool | None = None,
        has_character_select_backgrounds: bool | None = None,
        has_character_names: bool | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        manifest = self.load_game_manifest()
        versions = manifest.get("versions")
        assert isinstance(versions, list)
        existing_index = next(
            (
                index
                for index, item in enumerate(versions)
                if item.get("id") == self.settings.version
            ),
            None,
        )
        existing = versions[existing_index] if existing_index is not None else None
        if has_categories is None:
            has_categories = (self.settings.source_dir / "categories.json").is_file()
        if has_character_name_images is None:
            has_character_name_images = (
                self.settings.source_dir / "CharacterNameImages" / "manifest.json"
            ).is_file()
        if has_character_select_backgrounds is None:
            has_character_select_backgrounds = (
                self.settings.source_dir / "CharacterSelectBackgrounds" / "manifest.json"
            ).is_file()
        if has_character_names is None:
            has_character_names = (
                self.settings.source_dir / "character-names-overlay.json"
            ).is_file()
        entry = version_manifest_entry(
            self.settings,
            content_revision,
            existing,
            has_categories=has_categories,
            has_character_name_images=has_character_name_images,
            has_character_select_backgrounds=has_character_select_backgrounds,
            has_character_names=has_character_names,
        )
        new_versions = list(versions)
        if existing_index is None:
            new_versions.insert(0, entry)
        else:
            new_versions[existing_index] = entry
        manifest.update(
            {
                "schemaVersion": MANIFEST_SCHEMA_VERSION,
                "game": self.settings.game,
                "updatedAt": _utc_now(),
                "versions": new_versions,
            }
        )
        if has_shared_audio:
            manifest["sharedAudioBaseUrl"] = self.settings.public_url(
                f"{self.settings.game}/audio/"
            )
        if self.settings.promote_to_latest:
            if self.settings.hidden:
                raise PublisherError("A hidden version cannot be promoted to latest.")
            manifest["latestVersion"] = self.settings.version
        elif manifest.get("latestVersion") == self.settings.version and self.settings.hidden:
            manifest["latestVersion"] = next(
                (
                    item["id"]
                    for item in new_versions
                    if item.get("id") != self.settings.version
                    and item.get("hidden") is not True
                ),
                "",
            )
        elif not manifest.get("latestVersion") and not self.settings.hidden:
            manifest["latestVersion"] = self.settings.version
        return manifest, entry

    def _purge_urls(self, urls: Iterable[str]) -> None:
        unique_urls = sorted(set(urls))
        if not unique_urls:
            return
        token = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
        if not token or not self.settings.zone_id:
            self._log(
                "Warning: JSON was updated but CDN purge was skipped. Set CLOUDFLARE_API_TOKEN "
                "and a Zone ID to enable targeted purging."
            )
            return
        endpoint = (
            f"https://api.cloudflare.com/client/v4/zones/{self.settings.zone_id}/purge_cache"
        )
        for index in range(0, len(unique_urls), 30):
            batch = unique_urls[index : index + 30]
            request = urllib.request.Request(
                endpoint,
                data=json.dumps({"files": batch}).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    result = json.loads(response.read().decode("utf-8"))
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                raise PublisherError(f"Cloudflare cache purge failed: {exc}") from exc
            if not result.get("success"):
                raise PublisherError(
                    f"Cloudflare rejected the cache purge: {result.get('errors', result)}"
                )
            self._log(f"Purged {len(batch)} changed URL(s) from Cloudflare cache.")

    def publish(self, plan: PublishPlan) -> dict[str, Any]:
        if not plan.validation.valid:
            raise PublisherError("Cannot publish because source validation failed.")
        if plan.immutable_conflicts:
            paths = ", ".join(item.relative_path for item, _ in plan.immutable_conflicts[:5])
            raise PublisherError(
                "Publishing would overwrite immutable binary content. Use a new object name or version ID: "
                + paths
            )

        binary_uploads = [item for item in plan.upload_new if not item.mutable]
        json_uploads = [
            item for item in [*plan.upload_new, *plan.upload_changed_json] if item.mutable
        ]
        uploads = [*binary_uploads, *json_uploads]
        self._log(
            f"Uploading {len(uploads):,} new or changed content file(s); "
            f"skipping {len(plan.unchanged):,} unchanged file(s)."
        )
        uploaded_keys: list[str] = []
        for phase_label, phase_records in (
            ("immutable binary", binary_uploads),
            ("mutable JSON", json_uploads),
        ):
            if not phase_records:
                continue
            self._log(f"Starting {phase_label} upload phase ({len(phase_records):,} file(s)).")
            completed = 0
            with ThreadPoolExecutor(max_workers=self.settings.concurrency) as executor:
                futures = {
                    executor.submit(self._upload_file, item): item for item in phase_records
                }
                for future in as_completed(futures):
                    item = futures[future]
                    try:
                        uploaded_keys.append(future.result())
                    except Exception as exc:
                        for pending in futures:
                            pending.cancel()
                        raise PublisherError(
                            f"Upload failed for {item.relative_path}: {exc}"
                        ) from exc
                    completed += 1
                    if completed % 250 == 0 or completed == len(phase_records):
                        self._log(
                            f"Uploaded and verified {completed:,}/{len(phase_records):,} "
                            f"{phase_label} file(s)."
                        )

        revision = self._next_revision(plan)
        inventory = inventory_payload(self.settings, plan, revision)
        manifest, entry = self._build_game_manifest(
            revision,
            has_categories="categories.json" in inventory["files"],
            has_character_name_images=(
                "character-name-images/manifest.json" in inventory["files"]
            ),
            has_character_names="character-names.json" in inventory["files"],
            has_shared_audio=any(
                record.scope == "game" for record in plan.local_records.values()
            ),
        )
        release = {
            "schemaVersion": MANIFEST_SCHEMA_VERSION,
            **entry,
            "fileCount": len(inventory["files"]),
            "totalBytes": sum(
                int(item.get("size", 0)) for item in inventory["files"].values()
            ),
        }
        characters = self._build_game_characters(manifest)
        manifest["charactersUrl"] = self.settings.public_url(
            self.settings.game_characters_key
        )
        character_names = self._local_game_character_names()
        if character_names is not None:
            manifest["characterNamesUrl"] = self.settings.public_url(
                self.settings.game_character_names_key
            )

        self._log("Writing the version release record and publish inventory...")
        self._put_json(self.settings.release_key, release)
        self._put_json(self.settings.inventory_key, inventory)
        self._log("Updating the all-version character route list...")
        self._put_json(self.settings.game_characters_key, characters)
        if character_names is not None:
            self._log("Updating the game character display names...")
            self._put_json(self.settings.game_character_names_key, character_names)
        self._log("Updating the public game manifest last...")
        self._put_json(self.settings.game_manifest_key, manifest)

        purge_keys = [
            self._object_key(plan.local_records[path])
            for path in plan.changed_json_paths
        ]
        purge_keys.extend(
            [
                self.settings.release_key,
                self.settings.inventory_key,
                self.settings.game_characters_key,
                *(
                    [self.settings.game_character_names_key]
                    if character_names is not None
                    else []
                ),
                self.settings.game_manifest_key,
            ]
        )
        try:
            self._purge_urls(self.settings.public_url(key) for key in purge_keys)
        except PublisherError as exc:
            self._log(
                "Warning: publication completed, but targeted CDN purging failed: " + str(exc)
            )
        self._log(
            f"Published {self.settings.game}/{self.settings.version} at content revision {revision}."
        )
        return {
            "contentRevision": revision,
            "uploaded": len(uploaded_keys),
            "skipped": len(plan.unchanged),
            "manifest": manifest,
        }


def format_bytes(value: int) -> str:
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    amount = float(value)
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            return f"{amount:.1f} {unit}"
        amount /= 1024
    return f"{value} B"
