"""Export game localization files without importing or constructing a GUI."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from historical_content.parsing.vdf import ORDERED_KNOWN_SUFFIXES, parse_quoted_kv_line

Progress = Callable[[str], None]
LOCALIZATION_FILE_PREFIX = "citadel_generated_vo_"
HERO_NAME_FILE_PREFIX = "citadel_gc_hero_names_"
HERO_NAME_OUTPUT_FILE = "hero_name_localizations.json"

# Language key: (English name, native name, flag country).
LANGUAGE_METADATA = {
    "brazilian": ("Portuguese (Brazil)", "Português (Brasil)", "BR"),
    "bulgarian": ("Bulgarian", "Български", "BG"),
    "czech": ("Czech", "Čeština", "CZ"),
    "danish": ("Danish", "Dansk", "DK"),
    "dutch": ("Dutch", "Nederlands", "NL"),
    "english": ("English", "English", "US"),
    "finnish": ("Finnish", "Suomi", "FI"),
    "french": ("French", "Français", "FR"),
    "german": ("German", "Deutsch", "DE"),
    "greek": ("Greek", "Ελληνικά", "GR"),
    "hungarian": ("Hungarian", "Magyar", "HU"),
    "indonesian": ("Indonesian", "Bahasa Indonesia", "ID"),
    "italian": ("Italian", "Italiano", "IT"),
    "japanese": ("Japanese", "日本語", "JP"),
    "koreana": ("Korean", "한국어", "KR"),
    "latam": ("Spanish (Latin America)", "Español (Latinoamérica)", "MX"),
    "norwegian": ("Norwegian", "Norsk", "NO"),
    "polish": ("Polish", "Polski", "PL"),
    "portuguese": ("Portuguese", "Português", "PT"),
    "romanian": ("Romanian", "Română", "RO"),
    "russian": ("Russian", "Русский", "RU"),
    "schinese": ("Chinese (Simplified)", "简体中文", "CN"),
    "spanish": ("Spanish", "Español", "ES"),
    "swedish": ("Swedish", "Svenska", "SE"),
    "tchinese": ("Chinese (Traditional)", "繁體中文", "TW"),
    "thai": ("Thai", "ไทย", "TH"),
    "turkish": ("Turkish", "Türkçe", "TR"),
    "ukrainian": ("Ukrainian", "Українська", "UA"),
    "vietnamese": ("Vietnamese", "Tiếng Việt", "VN"),
}


class LocalizationMetadataError(Exception):
    """A discovered localization file has incomplete language metadata."""


def get_language_metadata(language: str) -> dict[str, str]:
    language_key = (language or "").strip().lower()
    try:
        friendly_name, native_name, country_code = LANGUAGE_METADATA[language_key]
    except KeyError as exc:
        raise LocalizationMetadataError(
            f"Missing metadata entry for language '{language_key}'."
        ) from exc
    flag = "".join(chr(127397 + ord(letter)) for letter in country_code)
    return {
        "friendly_name": friendly_name,
        "native_name": native_name,
        "country_code": country_code,
        "flag_emoji": flag,
        "flag_emoji_unicode": " ".join(f"U+{ord(char):04X}" for char in flag),
    }


def parse_localization_tokens(file_path: Path) -> dict[str, str]:
    """Read quoted pairs from Tokens blocks, preserving first insertion order."""
    tokens = {}
    waiting_for_tokens_block = False
    tokens_depth = 0
    with open(file_path, "r", encoding="utf-8-sig", errors="replace") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("//"):
                continue
            if waiting_for_tokens_block:
                if line.startswith("{"):
                    tokens_depth = 1
                    waiting_for_tokens_block = False
                continue
            if tokens_depth:
                if line.startswith("{"):
                    tokens_depth += 1
                    continue
                if line.startswith("}"):
                    tokens_depth -= 1
                    continue
                parsed = parse_quoted_kv_line(line)
                if parsed:
                    key, text = parsed
                    key = key.strip().lower()
                    text = text.strip()
                    if key:
                        tokens[key] = text
                continue
            line_lower = line.lower()
            if line_lower == '"tokens"':
                waiting_for_tokens_block = True
                continue
            if line_lower.startswith('"tokens"') and "{" in line:
                tokens_depth = 1
                continue
    return tokens


def _parse_hero_name_tokens(file_path: Path) -> dict[str, str]:
    """Read base hero names, excluding search/sort tokens and inline hash tags."""
    tokens = parse_localization_tokens(file_path)
    hero_names = {}
    for key, text in tokens.items():
        if not key.startswith("hero_"):
            continue
        if not key.endswith(":n"):
            continue
        if key.endswith(("_search:n", "_sort:n")):
            continue
        hero_id = key[len("hero_") : -2].strip()
        if not hero_id:
            continue
        cleaned_text = re.sub(r"#.*?#", "", text)
        cleaned_text = re.sub(r"\s{2,}", " ", cleaned_text).strip()
        if not cleaned_text:
            continue
        hero_names[hero_id] = cleaned_text
    return hero_names


def _load_character_mapping_indexes(character_mappings_path):
    """Return canonical order, canonical/alias lookups, and alias collisions."""
    with open(character_mappings_path, "r", encoding="utf-8") as f:
        mappings = json.load(f)
    if not isinstance(mappings, dict):
        raise ValueError(
            f"Character mappings file is not a JSON object: {character_mappings_path}"
        )
    canonical_order = []
    canonical_lookup = {}
    alias_lookup = {}
    alias_collisions = []
    for canonical_raw, aliases in mappings.items():
        canonical_original = str(canonical_raw).strip()
        canonical_norm = canonical_original.lower()
        if not canonical_original:
            continue
        canonical_order.append(canonical_original)
        canonical_lookup.setdefault(canonical_norm, canonical_original)
        if isinstance(aliases, list):
            for alias in aliases:
                alias_norm = str(alias).strip().lower()
                if not alias_norm:
                    continue
                existing = alias_lookup.get(alias_norm)
                if existing is None:
                    alias_lookup[alias_norm] = canonical_original
                elif existing != canonical_original:
                    alias_collisions.append((alias_norm, existing, canonical_original))
    return (canonical_order, canonical_lookup, alias_lookup, alias_collisions)


def _build_hero_name_localization_index(
    hero_names_by_language,
    ordered_languages,
    canonical_order,
    canonical_lookup,
    alias_lookup,
):
    """Return {character: [[language, name], ...]} in character mapping order."""
    index = {}
    unmatched_tokens = 0
    duplicate_language_hits = 0
    for language in ordered_languages:
        lang_map = hero_names_by_language.get(language, {})
        for hero_id, localized_name in lang_map.items():
            token_id = str(hero_id).strip().lower()
            canonical_key = canonical_lookup.get(token_id)
            if canonical_key is None:
                canonical_key = alias_lookup.get(token_id)
            if canonical_key is None:
                unmatched_tokens += 1
                continue
            row = index.setdefault(canonical_key, [])
            if any(item[0] == language for item in row):
                duplicate_language_hits += 1
                continue
            row.append([language, localized_name])
    ordered_index = {}
    for canonical_key in canonical_order:
        rows = index.get(canonical_key)
        if rows:
            ordered_index[canonical_key] = rows
    stats = {
        "unmatched_tokens": unmatched_tokens,
        "duplicate_language_hits": duplicate_language_hits,
        "emitted_keys": len(ordered_index),
    }
    return (ordered_index, stats)


def _build_voiceline_localization_index(lines_by_language, ordered_languages):
    """List available languages per filename stem in manifest order."""
    index = {}
    for language in ordered_languages:
        language_lines = lines_by_language.get(language, {})
        for voiceline_id in language_lines:
            key = (
                voiceline_id[:-4]
                if voiceline_id.lower().endswith(".mp3")
                else voiceline_id
            )
            row = index.setdefault(key, [])
            row.append(language)
    order_map = {lang: idx for idx, lang in enumerate(ordered_languages)}
    for filename in list(index.keys()):
        index[filename] = sorted(
            set(index[filename]), key=lambda lang: order_map.get(lang, 10**9)
        )
    return index


def normalize_localization_lines(
    tokens: dict[str, str],
) -> tuple[dict[str, str], int, int]:
    """Collapse known suffixes; exact keys override suffix matches, first suffix wins."""
    lines = {}
    source_kind = {}
    collisions = 0
    exact_overrides = 0
    for key, text in tokens.items():
        canonical_key = key
        incoming_kind = "exact"
        for suffix in ORDERED_KNOWN_SUFFIXES:
            if key.endswith(suffix):
                stripped = key[: -len(suffix)]
                if stripped:
                    canonical_key = stripped
                    incoming_kind = "suffix"
                break
        existing_kind = source_kind.get(canonical_key)
        if existing_kind is None:
            lines[canonical_key] = text
            source_kind[canonical_key] = incoming_kind
            continue
        if existing_kind == "suffix" and incoming_kind == "exact":
            lines[canonical_key] = text
            source_kind[canonical_key] = "exact"
            exact_overrides += 1
        else:
            collisions += 1
    return (lines, collisions, exact_overrides)


def _language_files(
    source: Path,
    destination: Path,
    prefix: str,
    label: str,
    progress: Progress,
) -> list[tuple[Path, str]]:
    if not source.is_dir():
        progress(f"[{label}] Source directory not found: {source}")
        return []
    try:
        destination.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        progress(f"[{label}] Failed to create output directory: {exc}")
        return []

    files = []
    for path in sorted(source.iterdir(), key=lambda path: path.name):
        name = path.name.lower()
        if name.startswith(prefix) and name.endswith(".txt"):
            language = name[len(prefix) : -4].strip()
            if language:
                files.append((path, language))
    if not files:
        description = (
            "hero-name localization" if label == "Hero Names" else "localization"
        )
        progress(f"[{label}] No {description} files found in: {source}")
    return files


def _file_language_metadata(
    language: str, path: Path, description: str
) -> dict[str, str]:
    try:
        return get_language_metadata(language)
    except LocalizationMetadataError as exc:
        raise LocalizationMetadataError(
            f"{description} file '{path.name}' is missing supporting info: {exc}"
        ) from exc


def _write_json(path: Path, data: object) -> None:
    with path.open("w", encoding="utf-8") as stream:
        json.dump(data, stream, indent=2, ensure_ascii=False)


def export_localizations(source: Path, destination: Path, progress: Progress) -> None:
    """Write language files, their manifest, and the voiceline language lookup."""
    files = _language_files(
        source, destination, LOCALIZATION_FILE_PREFIX, "Localization", progress
    )
    if not files:
        return

    manifest = {
        "generated_at": datetime.now().isoformat(),
        "source_directory": os.path.abspath(source),
        "languages": [],
    }
    lines_by_language = {}
    for path, language in files:
        language_meta = _file_language_metadata(language, path, "Localization")
        try:
            lines, collisions, exact_overrides = normalize_localization_lines(
                parse_localization_tokens(path)
            )
        except Exception as exc:
            progress(f"[Localization] Failed to parse {path.name}: {exc}")
            continue

        counts = {
            "entry_count": len(lines),
            "collision_count": collisions,
            "exact_override_count": exact_overrides,
        }
        metadata = {"language": language, **language_meta}
        payload = {
            "meta": {
                **metadata,
                "source_file": path.name,
                "generated_at": datetime.now().isoformat(),
                **counts,
            },
            "lines": lines,
        }
        output_file = f"{language}.json"
        try:
            _write_json(destination / output_file, payload)
        except Exception as exc:
            progress(f"[Localization] Failed to write {output_file}: {exc}")
            continue

        manifest["languages"].append(
            {
                **metadata,
                "output_file": output_file,
                "source_file": path.name,
                **counts,
            }
        )
        lines_by_language[language] = lines
        progress(
            f"[Localization] Wrote {output_file} with {len(lines)} lines "
            f"(collisions: {collisions}, exact overrides: {exact_overrides})"
        )

    manifest_path = destination / "manifest.json"
    try:
        _write_json(manifest_path, manifest)
        progress(
            f"[Localization] Wrote manifest with {len(manifest['languages'])} languages to {manifest_path}"
        )
    except Exception as exc:
        progress(f"[Localization] Failed to write manifest: {exc}")

    try:
        ordered_languages = [entry["language"] for entry in manifest["languages"]]
        index = _build_voiceline_localization_index(
            lines_by_language, ordered_languages
        )
        index_path = destination / "voiceline_localizations.json"
        _write_json(index_path, index)
        progress(
            f"[Localization] Wrote filename localization lookup with {len(index)} entries to {index_path}"
        )
    except Exception as exc:
        progress(f"[Localization] Failed to write voiceline localization index: {exc}")

    progress(
        f"[Localization] Export complete. Language files written: {len(manifest['languages'])}"
    )


def export_hero_names(
    source: Path,
    destination: Path,
    character_mappings: Path,
    progress: Progress,
) -> None:
    """Write localized hero names under the configured canonical character keys."""
    files = _language_files(
        source, destination, HERO_NAME_FILE_PREFIX, "Hero Names", progress
    )
    if not files:
        return
    if not character_mappings.is_file():
        raise FileNotFoundError(
            f"Character mappings file not found: {character_mappings}"
        )
    canonical_order, canonical_lookup, alias_lookup, alias_collisions = (
        _load_character_mapping_indexes(character_mappings)
    )
    if alias_collisions:
        progress(
            f"[Hero Names] Alias collisions in character mappings: {len(alias_collisions)} "
            "(using first canonical key encountered)"
        )

    hero_names_by_language = {}
    ordered_languages = []
    for path, language in files:
        _file_language_metadata(language, path, "Hero-name localization")
        try:
            hero_names = _parse_hero_name_tokens(path)
        except Exception as exc:
            progress(f"[Hero Names] Failed to parse {path.name}: {exc}")
            continue
        hero_names_by_language[language] = hero_names
        ordered_languages.append(language)
        progress(
            f"[Hero Names] Parsed {len(hero_names)} base hero-name tokens from {path.name}"
        )

    index, stats = _build_hero_name_localization_index(
        hero_names_by_language,
        ordered_languages,
        canonical_order,
        canonical_lookup,
        alias_lookup,
    )
    output_path = destination / HERO_NAME_OUTPUT_FILE
    _write_json(output_path, index)
    progress(
        f"[Hero Names] Wrote hero name localization index with {len(index)} keys to {output_path}"
    )
    progress(
        f"[Hero Names] Export complete. Keys: {stats['emitted_keys']}, "
        f"unmatched tokens: {stats['unmatched_tokens']}, "
        f"duplicate language hits: {stats['duplicate_language_hits']}"
    )
