"""Extract and convert historical portraits, localized wordmarks, and backgrounds."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

from ..errors import VpkPipelineError
from ..image_dimensions import read_image_dimensions
from ..json_io import write_json
from ..parsing.common import alias_index, validate_mapping
from .source2viewer import quick_vpk_fingerprint, replace_directory, run_source2viewer

Progress = Callable[[str], None]
HISTORICAL_ICON_RE = re.compile(
    r"^(?P<hero>.+)_(?P<variant>card_critical|card_gloat|card|sm|mm)(?:_(?:png|psd))?$",
    re.IGNORECASE,
)
HISTORICAL_ICON_FORMAT_VERSION = 8
HISTORICAL_ICON_VARIANTS = {
    "sm": "minimap",
    "mm": "minimap-low-res",
    "card": "normal",
    "card_gloat": "gloat",
    "card_critical": "critical",
}
HISTORICAL_HIGHLIGHT_VARIANTS = {"gloat", "critical"}
HISTORICAL_PATRON_MINIMAP_ICONS = {
    "patron_archmother_psd": "patron_female",
    "patron_hiddenking_psd": "patron_male",
}
CHARACTER_NAME_IMAGE_FORMAT_VERSION = 1
DEFAULT_NAME_IMAGE_MAX_HEIGHT = 512
CHARACTER_SELECT_BACKGROUND_FORMAT_VERSION = 2
DEFAULT_CHARACTER_SELECT_BACKGROUND_WIDTH = 1024
CHARACTER_SELECT_BACKGROUND_FILTER = "panorama/images/heroes/backgrounds"
NAME_IMAGE_FILTERS = (
    "panorama/images/heroes/hero_names",
    "panorama/images/hud/objectives/team1_patron_logo_psd",
    "panorama/images/hud/objectives/team2_patron_logo_psd",
)
NAME_IMAGE_CONVERTER = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "convert-character-name-images.mjs"
)
CHARACTER_SELECT_BACKGROUND_CONVERTER = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "convert-character-select-backgrounds.mjs"
)


def _vpk_name_image_filters(binary: Path, vpk: Path) -> tuple[str, ...]:
    """Return only the supported asset filters present in one VPK."""
    available: list[str] = []
    for file_filter in NAME_IMAGE_FILTERS:
        command = [
            str(binary),
            "-i",
            str(vpk),
            "--vpk_list",
            "-f",
            file_filter,
        ]
        try:
            completed = subprocess.run(
                command,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except OSError as exc:
            raise VpkPipelineError(
                f"Could not inspect character-name images in {vpk}: {exc}"
            ) from exc
        if completed.returncode:
            raise VpkPipelineError(
                f"Source2Viewer exited with status {completed.returncode} while inspecting "
                f"character-name images in {vpk}."
            )
        expected_marker = file_filter.rsplit("/", 1)[-1].casefold()
        if expected_marker in completed.stdout.casefold():
            available.append(file_filter)
    return tuple(available)


def character_name_image_vpks(
    vpk_path: Path,
    game_root: Path | None,
) -> dict[str, Path]:
    result = {"english": vpk_path.resolve()}
    if not game_root:
        return result
    game_dir = game_root / "game"
    if not game_dir.is_dir():
        return result
    for directory in sorted(
        game_dir.glob("citadel_*"), key=lambda item: item.name.casefold()
    ):
        if not directory.is_dir():
            continue
        language = directory.name[len("citadel_") :].strip().casefold()
        vpk = directory / "pak01_dir.vpk"
        if language and vpk.is_file():
            result[language] = vpk.resolve()
    return result


def character_name_image_inputs(vpks: dict[str, Path]) -> dict[str, object]:
    return {
        language: {
            "path": str(vpk),
            "fingerprint": quick_vpk_fingerprint(vpk),
        }
        for language, vpk in sorted(vpks.items())
    }


def _run_image_converter(
    converter: Path,
    arguments: list[str],
    label: str,
) -> tuple[dict[str, dict[str, object]], list[str]]:
    """Run an image converter and normalize its shared JSON response contract."""
    node = shutil.which("node")
    if not node:
        raise VpkPipelineError(f"Node.js is required to convert {label} to WebP.")
    if not converter.is_file():
        raise VpkPipelineError(
            f"{label.capitalize()} converter is missing: {converter}"
        )
    completed = subprocess.run(
        [node, str(converter), *arguments],
        capture_output=True,
        check=False,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise VpkPipelineError(
            f"{label.capitalize()} WebP conversion failed."
            + (f"\n{detail}" if detail else "")
        )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise VpkPipelineError(
            f"{label.capitalize()} converter returned invalid JSON."
        ) from exc
    images = payload.get("images") if isinstance(payload, dict) else None
    if not isinstance(images, dict):
        raise VpkPipelineError(
            f"{label.capitalize()} converter did not return an image map."
        )
    converted = {
        key: value
        for key, value in images.items()
        if isinstance(key, str) and isinstance(value, dict)
    }
    warnings: list[str] = []
    raw_warnings = payload.get("warnings", []) if isinstance(payload, dict) else []
    if isinstance(raw_warnings, list):
        for warning in raw_warnings:
            if not isinstance(warning, dict):
                continue
            filename = warning.get("file")
            detail = warning.get("error")
            if isinstance(filename, str) and isinstance(detail, str):
                warnings.append(f"{filename}: {detail}")
    return converted, warnings


def _run_name_image_converter(
    extracted: Path,
    destination: Path,
    max_height: int,
) -> tuple[dict[str, dict[str, object]], list[str]]:
    return _run_image_converter(
        NAME_IMAGE_CONVERTER,
        [
            "--source",
            str(extracted),
            "--output",
            str(destination),
            "--max-height",
            str(max_height),
        ],
        "character-name image",
    )


def export_character_name_images(
    *,
    source2viewer_binary: Path,
    vpk_path: Path,
    source_dir: Path,
    game_root: Path | None,
    character_mappings: Path,
    extraction_threads: int,
    max_height: int,
    progress: Progress,
) -> tuple[int, dict[str, object]]:
    """Extract available localized wordmarks and package immutable WebPs."""
    staging = source_dir.parent / "character-name-image-extraction"
    destination = source_dir / "CharacterNameImages"
    replace_directory(staging, source_dir.parent)
    replace_directory(destination, source_dir)
    mappings = validate_mapping(character_mappings)
    aliases = alias_index(mappings)
    vpks = character_name_image_vpks(vpk_path, game_root)
    languages: dict[str, dict[str, dict[str, object]]] = {}
    image_count = 0
    try:
        for language, vpk in sorted(vpks.items()):
            available_filters = _vpk_name_image_filters(
                source2viewer_binary.resolve(),
                vpk,
            )
            if not available_filters:
                progress(
                    f"Character-name images: [{language}] no supported assets found; skipping."
                )
                continue
            extracted = staging / language
            replace_directory(extracted, staging)
            for file_filter in available_filters:
                run_source2viewer(
                    source2viewer_binary.resolve(),
                    vpk,
                    extracted,
                    file_filter,
                    extraction_threads,
                    progress,
                )
            converted, conversion_warnings = _run_name_image_converter(
                extracted,
                destination / language,
                max_height,
            )
            for warning in conversion_warnings:
                progress(
                    f"Character-name images: [{language}] skipped malformed asset: {warning}"
                )
            if not converted:
                progress(
                    f"Character-name images: [{language}] no supported assets found; skipping."
                )
                shutil.rmtree(destination / language, ignore_errors=True)
                continue

            entries: dict[str, dict[str, object]] = {}
            for source_key, value in sorted(converted.items()):
                filename = value.get("file")
                width = value.get("width")
                height = value.get("height")
                if (
                    not isinstance(filename, str)
                    or Path(filename).name != filename
                    or not filename.casefold().endswith(".webp")
                    or not isinstance(width, int)
                    or not isinstance(height, int)
                ):
                    raise VpkPipelineError(
                        f"Character-name converter returned invalid metadata for {language}/{source_key}."
                    )
                asset = {
                    "path": f"{language}/{filename}",
                    "width": width,
                    "height": height,
                }
                for key in _icon_manifest_keys(source_key, mappings, aliases):
                    entries.setdefault(key, asset)
                image_count += 1
            if entries:
                languages[language] = entries
                progress(
                    f"Character-name images: [{language}] prepared {len(converted):,} WebP asset(s)."
                )
    finally:
        if staging.is_dir():
            shutil.rmtree(staging)

    if not image_count:
        shutil.rmtree(destination, ignore_errors=True)
        progress(
            "Character-name images were not present in the selected VPK set; continuing without them."
        )
        return 0, {"available": False}

    write_json(
        destination / "manifest.json",
        {
            "schemaVersion": 1,
            "extractionFormatVersion": CHARACTER_NAME_IMAGE_FORMAT_VERSION,
            "maxHeight": max_height,
            "languages": languages,
        },
    )
    progress(
        f"Character-name image set ready: {image_count:,} WebPs across "
        f"{len(languages):,} language(s) at {destination}."
    )
    return image_count, {"available": True}


def _run_character_select_background_converter(
    extracted: Path,
    destination: Path,
    width: int = DEFAULT_CHARACTER_SELECT_BACKGROUND_WIDTH,
) -> tuple[dict[str, dict[str, object]], list[str]]:
    return _run_image_converter(
        CHARACTER_SELECT_BACKGROUND_CONVERTER,
        [
            "--source",
            str(extracted),
            "--output",
            str(destination),
            "--width",
            str(width),
        ],
        "character-select background",
    )


def export_character_select_backgrounds(
    *,
    source2viewer_binary: Path,
    vpk_path: Path,
    source_dir: Path,
    character_mappings: Path,
    extraction_threads: int,
    progress: Progress,
) -> tuple[int, dict[str, object]]:
    """Extract the right half of each hero-select backdrop as an immutable WebP set."""
    staging = source_dir.parent / "character-select-background-extraction"
    destination = source_dir / "CharacterSelectBackgrounds"
    replace_directory(staging, source_dir.parent)
    replace_directory(destination, source_dir)
    mappings = validate_mapping(character_mappings)
    aliases = alias_index(mappings)
    try:
        run_source2viewer(
            source2viewer_binary.resolve(),
            vpk_path.resolve(),
            staging,
            CHARACTER_SELECT_BACKGROUND_FILTER,
            extraction_threads,
            progress,
        )
        converted, conversion_warnings = _run_character_select_background_converter(
            staging,
            destination,
        )
        for warning in conversion_warnings:
            progress(
                f"Character-select backgrounds: skipped malformed asset: {warning}"
            )
    finally:
        if staging.is_dir():
            shutil.rmtree(staging)

    if not converted:
        shutil.rmtree(destination, ignore_errors=True)
        progress(
            "Character-select backgrounds were not present in the selected VPK; "
            "continuing without them."
        )
        return 0, {"available": False}

    assets: dict[str, dict[str, object]] = {}
    for source_key, value in sorted(converted.items()):
        filename = value.get("file")
        width = value.get("width")
        height = value.get("height")
        accent_color = value.get("accentColor")
        if (
            not isinstance(filename, str)
            or Path(filename).name != filename
            or not filename.casefold().endswith(".webp")
            or not isinstance(width, int)
            or not isinstance(height, int)
            or not isinstance(accent_color, str)
            or not re.fullmatch(r"#[0-9a-fA-F]{6}", accent_color)
        ):
            raise VpkPipelineError(
                f"Character-select background converter returned invalid metadata for {source_key}."
            )
        assets[source_key] = {
            "path": filename,
            "width": width,
            "height": height,
            "accentColor": accent_color.lower(),
        }

    entries: dict[str, dict[str, object]] = {}
    for source_key, asset in assets.items():
        direct_key = source_key.strip().casefold()
        if direct_key:
            entries[direct_key] = asset
            entries[direct_key.replace(" ", "_")] = asset
    for source_key, asset in assets.items():
        for key in _icon_manifest_keys(source_key, mappings, aliases):
            entries.setdefault(key, asset)

    write_json(
        destination / "manifest.json",
        {
            "schemaVersion": 1,
            "extractionFormatVersion": CHARACTER_SELECT_BACKGROUND_FORMAT_VERSION,
            "crop": "right-half",
            "maxWidth": DEFAULT_CHARACTER_SELECT_BACKGROUND_WIDTH,
            "backgrounds": entries,
        },
    )
    progress(
        f"Character-select background set ready: {len(assets):,} WebPs and "
        f"{len(entries):,} lookup keys at {destination}."
    )
    return len(assets), {"available": True}


def _icon_manifest_keys(
    hero: str,
    mappings: dict[str, list[str]],
    aliases: dict[str, str],
) -> list[str]:
    """Return stable lookup keys for an internal historical hero name."""
    canonical = aliases.get(hero.casefold(), hero)
    candidates = {hero, canonical}
    candidates.update(mappings.get(canonical, []))
    keys: set[str] = set()
    for candidate in candidates:
        key = candidate.strip().casefold()
        if not key:
            continue
        keys.add(key)
        keys.add(key.replace(" ", "_"))
    return sorted(keys)


def _historical_icon_owners(extracted_root: Path) -> dict[str, set[str]]:
    """Read hero-to-portrait relationships from the selected build's heroes.vdata."""
    vdata = next(iter(sorted(extracted_root.rglob("heroes.vdata"))), None)
    if not vdata:
        return {}
    text = vdata.read_text(encoding="utf-8", errors="replace")
    entries = list(
        re.finditer(r"(?m)^\s*hero_([a-z0-9_]+)\s*=\s*$", text, re.IGNORECASE)
    )
    owners: dict[str, set[str]] = {}
    field_patterns = (
        re.compile(
            r'm_strIconImageSmall\s*=.*?/heroes/([^/".]+?)(?:_sm)?\.(?:png|psd)',
            re.IGNORECASE,
        ),
        re.compile(
            r'm_strMinimapImage\s*=.*?/heroes/([^/".]+?)(?:_mm)?\.(?:png|psd)',
            re.IGNORECASE,
        ),
    )
    for index, entry in enumerate(entries):
        hero = entry.group(1).casefold()
        end = entries[index + 1].start() if index + 1 < len(entries) else len(text)
        block = text[entry.end() : end]
        for pattern in field_patterns:
            match = pattern.search(block)
            if match:
                owners.setdefault(match.group(1).casefold(), set()).add(hero)
    return owners


def _build_historical_icon_pack(
    extracted_root: Path,
    destination: Path,
    mappings: dict[str, list[str]],
    *,
    include_highlight_variants: bool = True,
) -> int:
    """Build the website's default icon override from historical game textures."""
    enabled_variants = (
        set(HISTORICAL_ICON_VARIANTS.values())
        if include_highlight_variants
        else set(HISTORICAL_ICON_VARIANTS.values()) - HISTORICAL_HIGHLIGHT_VARIANTS
    )
    aliases = alias_index(mappings)
    owners = _historical_icon_owners(extracted_root)
    found: dict[str, dict[str, Path]] = {
        variant: {}
        for variant in HISTORICAL_ICON_VARIANTS.values()
        if variant in enabled_variants
    }
    for path in sorted(extracted_root.rglob("*")):
        if not path.is_file() or path.suffix.casefold() not in {
            ".png",
            ".webp",
            ".jpg",
            ".jpeg",
        }:
            continue
        stem = path.stem.casefold()
        patron = HISTORICAL_PATRON_MINIMAP_ICONS.get(stem)
        if patron and "minimap" in enabled_variants:
            found["minimap"].setdefault(patron, path)
            continue
        match = HISTORICAL_ICON_RE.fullmatch(stem)
        if not match:
            continue
        hero = match.group("hero").casefold()
        variant = HISTORICAL_ICON_VARIANTS[match.group("variant").casefold()]
        if variant not in enabled_variants:
            continue
        found[variant].setdefault(hero, path)

    image_count = sum(len(images) for images in found.values())
    if not image_count:
        return 0

    destination.mkdir(parents=True, exist_ok=True)
    manifest_icons: dict[str, dict[str, str]] = {}
    manifest_dimensions: dict[str, dict[str, int]] = {}
    for variant, images in found.items():
        if not images:
            continue
        variant_dir = destination / variant
        variant_dir.mkdir(parents=True, exist_ok=True)
        assets: dict[str, str] = {}
        for hero, source in sorted(images.items()):
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            filename = f"{hero}.{digest}{source.suffix.casefold()}"
            relative = f"{variant}/{filename}"
            width, height = read_image_dimensions(source)
            shutil.copy2(source, variant_dir / filename)
            manifest_dimensions[relative] = {"width": width, "height": height}
            assets[hero] = relative

        entries: dict[str, str] = {}
        # Preserve every raw Source 2 name before expanding aliases. Some
        # characters intentionally have multiple assets in one canonical
        # mapping (for example werewolf and werewolf_wolf). A direct raw key
        # must always resolve to its own image instead of being overwritten by
        # a later alias expansion.
        for hero, relative in assets.items():
            direct_key = hero.strip().casefold()
            if direct_key:
                entries[direct_key] = relative
                entries[direct_key.replace(" ", "_")] = relative

        # First-write-wins for canonical names and other aliases. Sorted raw
        # names match the website's icon-manifest generator and make collisions
        # deterministic while leaving the direct keys above untouched.
        for hero, relative in assets.items():
            icon_owners = {hero, *owners.get(hero, set())}
            for owner in sorted(icon_owners):
                for key in _icon_manifest_keys(owner, mappings, aliases):
                    entries.setdefault(key, relative)
        manifest_icons[variant] = entries

    write_json(
        destination / "manifest.json",
        {
            "extractionFormatVersion": HISTORICAL_ICON_FORMAT_VERSION,
            "id": "default",
            "label": "Historical game icons",
            "familyId": "official",
            "description": "Hero and patron portraits extracted from this historical Deadlock VPK.",
            "hidden": False,
            "credits": [{"name": "Valve", "role": "Original assets"}],
            "license": "Valve game assets. Use is subject to Valve's applicable terms.",
            "icons": manifest_icons,
            "iconDimensions": manifest_dimensions,
        },
    )
    return image_count


def export_historical_icons(
    *,
    source2viewer_binary: Path,
    vpk_path: Path,
    source_dir: Path,
    character_mappings: Path,
    extraction_threads: int,
    include_highlight_variants: bool,
    progress: Progress,
) -> int:
    """Extract a historical icon pack without running any other VPK stages."""
    staging = source_dir.parent / "icon-extraction"
    prepared = source_dir.parent / "icon-pack-prepared"
    destination = source_dir / "IconPacks" / "default"
    replace_directory(staging, source_dir.parent)
    replace_directory(prepared, source_dir.parent)
    try:
        run_source2viewer(
            source2viewer_binary,
            vpk_path,
            staging,
            "panorama/images/heroes",
            extraction_threads,
            progress,
        )
        run_source2viewer(
            source2viewer_binary,
            vpk_path,
            staging,
            "panorama/images/npcs/patron",
            extraction_threads,
            progress,
        )
        run_source2viewer(
            source2viewer_binary,
            vpk_path,
            staging,
            "scripts/heroes",
            extraction_threads,
            progress,
        )
        image_count = _build_historical_icon_pack(
            staging,
            prepared,
            validate_mapping(character_mappings),
            include_highlight_variants=include_highlight_variants,
        )
        if not image_count:
            raise VpkPipelineError(
                "The VPK did not contain supported historical hero icons "
                "(*_sm[_png|_psd], *_mm[_png|_psd], *_card[_psd], *_card_gloat[_psd], or "
                "*_card_critical[_psd]) or patron objective icons."
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            if not destination.is_dir():
                raise VpkPipelineError(
                    f"Historical icon destination is not a directory: {destination}"
                )
            shutil.rmtree(destination)
        os.replace(prepared, destination)
        progress(
            f"Historical icon pack ready: {image_count:,} portraits at {destination}."
        )
        return image_count
    finally:
        if staging.is_dir():
            shutil.rmtree(staging)
        if prepared.is_dir():
            shutil.rmtree(prepared)
