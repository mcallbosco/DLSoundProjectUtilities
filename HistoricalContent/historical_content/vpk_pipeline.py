"""Mine one Deadlock VPK and build the source consumed by baseline generation.

This module is intentionally UI-free.  Historical Content is the only
operator-facing application; the older GUIs remain migration references only.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable


Progress = Callable[[str], None]
UTILITIES_DIR = Path(__file__).resolve().parents[2]
ASSETS_DIR = UTILITIES_DIR / "Assets"
VOICELINE_ROOT = UTILITIES_DIR / "Voiceline Utilities"
if str(UTILITIES_DIR) not in sys.path:
    sys.path.insert(0, str(UTILITIES_DIR))
VERSION_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
AUDIO_SUFFIXES = {".mp3", ".wav", ".ogg", ".m4a"}
RR_TEST_RE = re.compile(r"^rr_test_\d+_(?P<line>.+)$", re.IGNORECASE)
HISTORICAL_ICON_RE = re.compile(
    r"^(?P<hero>.+)_(?P<variant>mm|sm)(?:_psd)?$",
    re.IGNORECASE,
)
HISTORICAL_ICON_FORMAT_VERSION = 2

# These are deliberately exact folder fallbacks. Most voicelines must continue
# to identify their speaker in the filename; only known historical layouts may
# use directory ownership when that parse fails.
SPECIFIC_VOICE_FOLDER_FALLBACKS: dict[str, tuple[str, tuple[str, ...]]] = {
    "book/oathkeeper": ("oathkeeper", ("vn_geist",)),
    "neutral_gremlin": ("neutral_gremlin", ("neutral_gremlin",)),
    "announcer/count_up": ("announcer_count_up", ()),
    "announcer/female_patron": ("patron_female", ("patron_female",)),
    "announcer/male_patron": ("patron_male", ("patron_male",)),
    "npc_reporter": ("newscaster", ("newscaster", "npc_reporter")),
    "shopkeeper": ("shopkeeper_hotdog", ("shopkeeper_hotdog",)),
    "dynamo": ("dynamo", ("dynamo", "prof")),
    "nano": ("calico", ("calico", "nano")),
}
GUARDIAN_FOLDER_RE = re.compile(
    r"^t1_guardians/(?P<speaker>guardian_test_0[1-4])$",
    re.IGNORECASE,
)


class VpkPipelineError(RuntimeError):
    """A safe error that can be shown directly in the Historical Content GUI."""


@dataclass(frozen=True)
class VpkPipelineSettings:
    source2viewer_binary: Path
    vpk_path: Path
    data_dir: Path
    transcript_repo: Path
    version_id: str
    game: str = "deadlock"
    character_mappings: Path = ASSETS_DIR / "character_mappings.json"
    topic_aliases: Path = ASSETS_DIR / "topic_mappings.json"
    voiceline_groups: Path = ASSETS_DIR / "voiceline_groups.json"
    conversation_overrides: Path = ASSETS_DIR / "conversation_overrides.json"
    transcription_vocabulary: Path = ASSETS_DIR / "deadlock_vocabulary.json"
    include_phantom: bool = True
    extract_localization: bool = True
    extract_icons: bool = True
    extraction_threads: int = 8
    force_reextract: bool = False

    @property
    def workspace(self) -> Path:
        return self.data_dir.expanduser().resolve() / "workspaces" / self.game / self.version_id

    @property
    def source_dir(self) -> Path:
        return self.workspace / "source"


@dataclass(frozen=True)
class VpkPipelineResult:
    source_dir: Path
    workspace: Path
    audio_dir: Path
    state_path: Path
    character_mappings: Path
    topic_aliases: Path
    voiceline_groups: Path
    conversation_overrides: Path
    transcription_vocabulary: Path
    audio_count: int
    voiceline_count: int
    conversation_count: int


class _Value:
    """Small StringVar replacement used by the legacy voiceline parser core."""

    def __init__(self, value: str):
        self.value = value

    def get(self) -> str:
        return self.value


class _DiscardLog:
    def append(self, _value: object) -> None:
        return


def _ensure_voiceline_import_path() -> None:
    if str(VOICELINE_ROOT) not in sys.path:
        sys.path.insert(0, str(VOICELINE_ROOT))


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        raise VpkPipelineError(f"Invalid JSON in {path}: {exc}") from exc


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _copy_config_once(seed: Path, destination: Path) -> Path:
    if destination.is_file():
        return destination
    if not seed.is_file():
        raise VpkPipelineError(f"Required configuration file does not exist: {seed}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(seed, destination)
    return destination


def ensure_game_configs(
    settings: VpkPipelineSettings,
) -> tuple[Path, Path, Path, Path, Path]:
    """Seed readable per-game configuration in the transcript repository."""
    root = settings.transcript_repo.expanduser().resolve() / "config" / settings.game
    mappings = _copy_config_once(settings.character_mappings, root / "character-mappings.json")
    aliases = _copy_config_once(settings.topic_aliases, root / "topic-aliases.json")
    groups = _copy_config_once(settings.voiceline_groups, root / "voiceline-groups.json")
    overrides = _copy_config_once(
        settings.conversation_overrides,
        root / "conversation-overrides.json",
    )
    vocabulary = _copy_config_once(
        settings.transcription_vocabulary,
        root / "transcription-vocabulary.json",
    )
    return mappings, aliases, groups, overrides, vocabulary


def _validate_mapping(path: Path) -> dict[str, list[str]]:
    payload = _read_json(path)
    if not isinstance(payload, dict):
        raise VpkPipelineError(f"Character mappings must contain a JSON object: {path}")
    result: dict[str, list[str]] = {}
    aliases_seen: dict[str, str] = {}
    for canonical, aliases in payload.items():
        if not isinstance(canonical, str) or not canonical.strip() or not isinstance(aliases, list):
            raise VpkPipelineError(
                f"Character mappings must use canonical-name to string-array entries: {path}"
            )
        cleaned = [item.strip() for item in aliases if isinstance(item, str) and item.strip()]
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


def _alias_index(mappings: dict[str, list[str]]) -> dict[str, str]:
    return {
        alias.casefold(): canonical
        for canonical, aliases in mappings.items()
        for alias in aliases
    }


def _strip_historical_variation(value: str) -> str:
    result = value
    while True:
        updated = re.sub(r"_alt_\d+$", "", result, flags=re.IGNORECASE)
        updated = re.sub(r"_\d+_alt$", "", updated, flags=re.IGNORECASE)
        updated = re.sub(r"_\d+$", "", updated)
        if updated == result:
            return result
        result = updated


def _specific_folder_voiceline_fallback(
    relative_path: Path,
    organizer: object,
    alias_lookup: dict[str, str],
    topic_aliases: dict[str, object],
) -> tuple[str, str, str, None, str, bool] | None:
    """Parse only explicitly approved historical folders after normal parsing fails."""
    folder = relative_path.parent.as_posix().casefold()
    # parse_voicelines normally receives the extracted sounds/vo directory.
    # Accept an Audio-root-relative path too so direct callers and fixtures
    # use the same exact whitelist.
    if folder.startswith("sounds/vo/"):
        folder = folder[len("sounds/vo/"):]
    guardian_match = GUARDIAN_FOLDER_RE.fullmatch(folder)
    if guardian_match:
        speaker = guardian_match.group("speaker").casefold()
        prefix = f"rr_{speaker}_"
        body = relative_path.stem
        if body.casefold().startswith(prefix):
            body = body[len(prefix):]
        body = _strip_historical_variation(body)
        tokens = body.split("_") if body else []
        subject = "self"
        topic_raw = body
        for index in range(len(tokens)):
            candidate = "_".join(tokens[index:]).casefold()
            canonical = alias_lookup.get(candidate)
            if canonical:
                subject = canonical
                topic_raw = "_".join(tokens[:index]) or "general"
                break
        topic = organizer._format_topic(topic_raw, topic_aliases).replace("_", " ").capitalize()
        return speaker, subject, topic, None, relative_path.as_posix(), False

    fallback = SPECIFIC_VOICE_FOLDER_FALLBACKS.get(folder)
    if not fallback:
        return None
    speaker, removable_prefixes = fallback
    body = relative_path.stem
    for prefix in removable_prefixes:
        marker = prefix + "_"
        if body.casefold().startswith(marker.casefold()):
            body = body[len(marker):]
            break
    topic_raw = _strip_historical_variation(body) or "general"
    topic = organizer._format_topic(topic_raw, topic_aliases).replace("_", " ").capitalize()
    return speaker, "self", topic, None, relative_path.as_posix(), False


def _validate_settings(settings: VpkPipelineSettings) -> None:
    binary = settings.source2viewer_binary.expanduser().resolve()
    vpk = settings.vpk_path.expanduser().resolve()
    if not binary.is_file():
        raise VpkPipelineError(f"Source2Viewer executable does not exist: {binary}")
    if not vpk.is_file() or vpk.suffix.casefold() != ".vpk":
        raise VpkPipelineError(f"Select a valid .vpk file: {vpk}")
    if not VERSION_RE.fullmatch(settings.game):
        raise VpkPipelineError("Game ID must contain lowercase letters, numbers, dots, dashes, or underscores.")
    if not VERSION_RE.fullmatch(settings.version_id):
        raise VpkPipelineError("Version ID must contain lowercase letters, numbers, dots, dashes, or underscores.")
    if settings.extraction_threads < 1 or settings.extraction_threads > 64:
        raise VpkPipelineError("Extraction threads must be between 1 and 64.")


def _quick_vpk_fingerprint(path: Path) -> dict[str, object]:
    """Identify a large VPK without hashing every byte during every resume."""
    stat = path.stat()
    digest = hashlib.sha256()
    sample_size = 1024 * 1024
    with path.open("rb") as stream:
        digest.update(stream.read(sample_size))
        if stat.st_size > sample_size:
            stream.seek(max(0, stat.st_size - sample_size))
            digest.update(stream.read(sample_size))
    return {
        "size": stat.st_size,
        "mtimeNs": stat.st_mtime_ns,
        "sampleSha256": digest.hexdigest(),
    }


def _safe_replace(path: Path, parent: Path) -> None:
    resolved = path.resolve()
    allowed = parent.resolve()
    if resolved == allowed or allowed not in resolved.parents:
        raise VpkPipelineError(f"Refusing to replace a path outside {allowed}: {resolved}")
    if resolved.exists():
        shutil.rmtree(resolved)
    resolved.mkdir(parents=True, exist_ok=True)


def _run_source2viewer(
    binary: Path,
    vpk: Path,
    output: Path,
    file_filter: str,
    threads: int,
    progress: Progress,
) -> None:
    output.mkdir(parents=True, exist_ok=True)
    command = [
        str(binary),
        "-i", str(vpk),
        "-o", str(output),
        "-f", file_filter,
        "-d",
        "--threads", str(threads),
    ]
    progress(f"Extracting {file_filter} with Source2Viewer...")
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        raise VpkPipelineError(f"Could not start Source2Viewer: {exc}") from exc
    tail: list[str] = []
    for line in process.stdout or ():
        text = line.strip()
        if text:
            tail.append(text)
            if len(tail) > 30:
                tail.pop(0)
    return_code = process.wait()
    if return_code:
        detail = "\n".join(tail[-15:])
        raise VpkPipelineError(
            f"Source2Viewer exited with status {return_code} while extracting {file_filter}."
            + (f"\n{detail}" if detail else "")
        )


def _find_audio_dir(audio_root: Path) -> Path:
    preferred = audio_root / "sounds" / "vo"
    if preferred.is_dir() and any(path.suffix.casefold() in AUDIO_SUFFIXES for path in preferred.rglob("*")):
        return preferred
    candidates: list[tuple[int, Path]] = []
    for path in audio_root.rglob("*"):
        if path.is_dir() and path.name.casefold() == "vo":
            count = sum(item.suffix.casefold() in AUDIO_SUFFIXES for item in path.rglob("*"))
            if count:
                candidates.append((count, path))
    if candidates:
        return max(candidates, key=lambda item: item[0])[1]
    raise VpkPipelineError(f"Source2Viewer did not produce a voice-audio directory under {audio_root}.")


def _find_game_root(vpk: Path) -> Path | None:
    for root in vpk.parents:
        candidate = root / "game" / "citadel"
        if candidate.is_dir() and (candidate / vpk.name).is_file():
            return root
    return None


def _prepare_metadata(
    settings: VpkPipelineSettings,
    source_dir: Path,
    progress: Progress,
) -> tuple[Path | None, Path | None, Path | None, Path | None]:
    game_root = _find_game_root(settings.vpk_path.resolve())
    if not game_root:
        progress(
            "Related loose game directory was not found; localization and official VDF "
            "subtitles are unavailable. Historical icons can still be extracted from the VPK."
        )
        return None, None, None, None
    localization = game_root / "game" / "citadel" / "resource" / "localization" / "citadel_generated_vo"
    hero_names = game_root / "game" / "citadel" / "resource" / "localization" / "citadel_gc_hero_names"
    vdf_source = localization / "citadel_generated_vo_english.txt"
    vdf_path: Path | None = None
    if vdf_source.is_file():
        vdf_path = source_dir / "Metadata" / "citadel_generated_vo.txt"
        vdf_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(vdf_source, vdf_path)
        progress(f"Loaded official English subtitles from {vdf_source}.")
    return game_root, localization if localization.is_dir() else None, hero_names if hero_names.is_dir() else None, vdf_path


def _export_localization(
    settings: VpkPipelineSettings,
    source_dir: Path,
    localization_source: Path | None,
    hero_name_source: Path | None,
    character_mappings: Path,
    progress: Progress,
) -> None:
    if not settings.extract_localization or not localization_source:
        return
    try:
        from AllInOne.batch_gui import BatchGUI
    except Exception as exc:
        raise VpkPipelineError(f"Could not load the localization exporter: {exc}") from exc
    adapter = BatchGUI.__new__(BatchGUI)
    adapter.log_write = lambda message: progress(str(message).strip()) if str(message).strip() else None
    destination = source_dir / "Localization"
    adapter._export_localizations_from_game_files(str(localization_source), str(destination))
    if hero_name_source:
        adapter._export_hero_name_localizations_from_game_files(
            str(hero_name_source),
            str(destination),
            character_mappings_path=str(character_mappings),
        )


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


def _historical_icon_owners(extracted_root: Path) -> dict[tuple[str, str], set[str]]:
    """Read hero-to-portrait relationships from the selected build's heroes.vdata."""
    vdata = next(iter(sorted(extracted_root.rglob("heroes.vdata"))), None)
    if not vdata:
        return {}
    text = vdata.read_text(encoding="utf-8", errors="replace")
    entries = list(re.finditer(r"(?m)^\s*hero_([a-z0-9_]+)\s*=\s*$", text, re.IGNORECASE))
    owners: dict[tuple[str, str], set[str]] = {}
    field_patterns = {
        "normal": re.compile(
            r'm_strIconImageSmall\s*=.*?/heroes/([^/".]+?)(?:_sm)?\.psd',
            re.IGNORECASE,
        ),
        "minimap": re.compile(
            r'm_strMinimapImage\s*=.*?/heroes/([^/".]+?)(?:_mm)?\.psd',
            re.IGNORECASE,
        ),
    }
    for index, entry in enumerate(entries):
        hero = entry.group(1).casefold()
        end = entries[index + 1].start() if index + 1 < len(entries) else len(text)
        block = text[entry.end():end]
        for variant, pattern in field_patterns.items():
            match = pattern.search(block)
            if match:
                owners.setdefault((variant, match.group(1).casefold()), set()).add(hero)
    return owners


def _build_historical_icon_pack(
    extracted_root: Path,
    destination: Path,
    mappings: dict[str, list[str]],
) -> int:
    """Build the website's default icon override from old hero textures."""
    variants = {"mm": "minimap", "sm": "normal"}
    aliases = _alias_index(mappings)
    owners = _historical_icon_owners(extracted_root)
    found: dict[str, dict[str, Path]] = {"minimap": {}, "normal": {}}
    for path in sorted(extracted_root.rglob("*")):
        if not path.is_file() or path.suffix.casefold() not in {".png", ".webp", ".jpg", ".jpeg"}:
            continue
        match = HISTORICAL_ICON_RE.fullmatch(path.stem)
        if not match:
            continue
        hero = match.group("hero").casefold()
        variant = variants[match.group("variant").casefold()]
        found[variant].setdefault(hero, path)

    image_count = sum(len(images) for images in found.values())
    if not image_count:
        return 0

    destination.mkdir(parents=True, exist_ok=True)
    manifest_icons: dict[str, dict[str, str]] = {}
    for variant, images in found.items():
        if not images:
            continue
        variant_dir = destination / variant
        variant_dir.mkdir(parents=True, exist_ok=True)
        entries: dict[str, str] = {}
        for hero, source in sorted(images.items()):
            filename = f"{hero}{source.suffix.casefold()}"
            shutil.copy2(source, variant_dir / filename)
            relative = f"{variant}/{filename}"
            icon_owners = {hero, *owners.get((variant, hero), set())}
            for owner in sorted(icon_owners):
                for key in _icon_manifest_keys(owner, mappings, aliases):
                    entries[key] = relative
        manifest_icons[variant] = entries

    _write_json(destination / "manifest.json", {
        "extractionFormatVersion": HISTORICAL_ICON_FORMAT_VERSION,
        "id": "default",
        "label": "Historical game icons",
        "familyId": "official",
        "description": "Hero portraits extracted from this historical Deadlock VPK.",
        "hidden": False,
        "credits": [{"name": "Valve", "role": "Original assets"}],
        "license": "Valve game assets. Use is subject to Valve's applicable terms.",
        "icons": manifest_icons,
    })
    return image_count


def _export_historical_icons(
    settings: VpkPipelineSettings,
    source_dir: Path,
    character_mappings: Path,
    progress: Progress,
) -> int:
    """Extract and package old minimap/standard portraits directly from a VPK."""
    staging = source_dir.parent / "icon-extraction"
    destination = source_dir / "IconPacks" / "default"
    _safe_replace(staging, source_dir.parent)
    _safe_replace(destination, source_dir)
    try:
        _run_source2viewer(
            settings.source2viewer_binary.resolve(),
            settings.vpk_path.resolve(),
            staging,
            "panorama/images/heroes",
            settings.extraction_threads,
            progress,
        )
        _run_source2viewer(
            settings.source2viewer_binary.resolve(),
            settings.vpk_path.resolve(),
            staging,
            "scripts/heroes",
            settings.extraction_threads,
            progress,
        )
        image_count = _build_historical_icon_pack(
            staging,
            destination,
            _validate_mapping(character_mappings),
        )
        if not image_count:
            raise VpkPipelineError(
                "The VPK did not contain supported historical hero icons "
                "(*_mm[_psd] and *_sm[_psd])."
            )
        progress(f"Historical icon pack ready: {image_count:,} portraits at {destination}.")
        return image_count
    finally:
        if staging.is_dir():
            shutil.rmtree(staging)


def _load_vdf(path: Path | None) -> dict[str, str]:
    _ensure_voiceline_import_path()
    from modules.vdf_kv_common import load_vdf_key_text_map

    return load_vdf_key_text_map(str(path)) if path else {}


def _materialize_voicelines(node: object, audio_dir: Path, vdf: dict[str, str]) -> object:
    from modules.vdf_kv_common import find_vdf_match_for_filename

    if isinstance(node, dict):
        # Phantom lines are already materialized records.  Do not interpret
        # their transcript and ID strings as filesystem paths.
        if isinstance(node.get("filename"), str):
            return dict(node)
        return {key: _materialize_voicelines(value, audio_dir, vdf) for key, value in node.items()}
    if isinstance(node, list):
        return [_materialize_voicelines(value, audio_dir, vdf) for value in node]
    if not isinstance(node, str):
        return node
    relative = Path(node)
    audio_path = audio_dir.joinpath(*relative.parts)
    audio_key = relative.as_posix()
    filename = relative.name
    try:
        date = datetime.fromtimestamp(audio_path.stat().st_mtime).strftime("%Y-%m-%d")
    except OSError:
        date = None
    _vdf_key, official_text = find_vdf_match_for_filename(filename, vdf)
    entry: dict[str, object] = {
        # filename is the key relative to audioBaseUrl.  Folder components are
        # required because Source 2 can contain different recordings with the
        # same basename in different voice folders.
        "filename": audio_key,
        "date": date,
        "voiceline_id": Path(filename).stem,
        "transcription": official_text or "",
    }
    if official_text:
        entry["officialtranscription"] = True
    return entry


def parse_voicelines(
    audio_dir: Path,
    character_mappings: Path,
    topic_aliases: Path,
    voiceline_groups: Path,
    vdf_path: Path | None,
    include_phantom: bool,
    progress: Progress,
) -> tuple[dict[str, object], set[str]]:
    _ensure_voiceline_import_path()
    from modules.vdf_kv_common import ORDERED_KNOWN_SUFFIXES
    from modules.voice_line_organizer import VoiceLineOrganizer
    from modules.voiceline_groups import load_group_config, sort_subject_topics

    alias_data = _validate_mapping(character_mappings)
    topic_data = _read_json(topic_aliases)
    if not isinstance(topic_data, dict):
        raise VpkPipelineError(f"Topic aliases must contain a JSON object: {topic_aliases}")
    group_config = load_group_config(voiceline_groups)
    valid_speakers = {
        alias.casefold()
        for aliases in alias_data.values()
        for alias in aliases
    }
    alias_lookup = _alias_index(alias_data)
    organizer = VoiceLineOrganizer.__new__(VoiceLineOrganizer)
    organizer.processing_debug_log = _DiscardLog()
    organizer.sort_debug_log = _DiscardLog()
    organizer.source_folder_path = _Value(str(audio_dir))
    organizer.disregarded_heroes = set()
    organizer.group_config = group_config
    organizer.log = lambda *_args, **_kwargs: None

    audio_files = sorted(
        path
        for path in audio_dir.rglob("*")
        if path.is_file()
        and path.suffix.casefold() == ".mp3"
        and _conversation_key_from_name(path.name, {}) is None
    )
    result: dict[str, object] = {}
    vdf = _load_vdf(vdf_path)
    used_vdf: set[str] = set()
    legacy_count = 0
    folder_fallback_count = 0
    for index, path in enumerate(audio_files, start=1):
        parse_path = path
        relative_path = path.relative_to(audio_dir)
        legacy_match = RR_TEST_RE.fullmatch(path.stem)
        legacy_speaker: str | None = None
        if legacy_match and len(relative_path.parts) > 1:
            # Very old builds used a numeric rr_test prefix instead of putting
            # the speaker in the filename. The first voice-folder component is
            # the stable speaker alias (for example kali -> vyper).
            speaker_alias = relative_path.parts[0].casefold()
            if speaker_alias not in alias_lookup:
                # Preserve an unknown historical character under its readable
                # folder name. The operator can rename it later in the per-game
                # character mapping JSON without losing the recording.
                alias_data[speaker_alias] = [speaker_alias]
                alias_lookup[speaker_alias] = speaker_alias
                valid_speakers.add(speaker_alias)
            legacy_speaker = alias_lookup[speaker_alias]
            parse_path = path.with_name(f"{speaker_alias}_{legacy_match.group('line')}{path.suffix}")

        unresolved_before = set(organizer.disregarded_heroes)
        parsed = organizer._process_file(str(parse_path), alias_data, topic_data, valid_speakers)
        if legacy_match and legacy_speaker and (not parsed or parsed == "disregarded"):
            # Some rr_test event names predate the current speaker/subject
            # grammar. Keep them as readable Self topics instead of dropping
            # historical audio merely because its old category is unknown.
            organizer.disregarded_heroes = unresolved_before
            topic_raw = legacy_match.group("line")
            topic_raw = re.sub(r"_alt_\d+$", "", topic_raw, flags=re.IGNORECASE)
            topic_raw = re.sub(r"_\d+_alt$", "", topic_raw, flags=re.IGNORECASE)
            topic_raw = re.sub(r"_\d+$", "", topic_raw)
            topic = organizer._format_topic(topic_raw, topic_data).replace("_", " ").capitalize()
            parsed = (
                legacy_speaker,
                "self",
                topic,
                None,
                relative_path.as_posix(),
                topic_raw.casefold().startswith("ping"),
            )
        if not parsed or parsed == "disregarded":
            folder_parsed = _specific_folder_voiceline_fallback(
                relative_path,
                organizer,
                alias_lookup,
                topic_data,
            )
            if folder_parsed:
                organizer.disregarded_heroes = unresolved_before
                parsed = folder_parsed
                folder_fallback_count += 1
        if parsed and parsed != "disregarded":
            matched = organizer._find_vdf_match(path.name, vdf)
            if matched:
                used_vdf.add(matched)
            if legacy_match and len(relative_path.parts) > 1:
                parsed = (*parsed[:4], relative_path.as_posix(), parsed[5])
                legacy_count += 1
            organizer._place_in_result(result, parsed, parsed[4])
        if index % 1000 == 0:
            progress(f"Parsed {index:,}/{len(audio_files):,} voiceline audio files...")

    if legacy_count:
        progress(f"Captured {legacy_count:,} legacy rr_test voicelines from their hero folders.")
    if folder_fallback_count:
        progress(
            f"Captured {folder_fallback_count:,} voicelines from approved historical folder fallbacks."
        )

    if include_phantom and vdf:
        for key in sorted(set(vdf) - used_vdf):
            suffix = next((item for item in ORDERED_KNOWN_SUFFIXES if key.endswith(item)), None)
            if not suffix:
                continue
            fake_path = key[:-len(suffix)] + ".mp3"
            if _conversation_key_from_name(Path(fake_path).name, {}) is not None:
                continue
            parsed = organizer._process_file(fake_path, alias_data, topic_data, valid_speakers)
            if parsed and parsed != "disregarded":
                organizer._place_in_result(result, parsed, {
                    "filename": "",
                    "is_phantom": True,
                    "transcription": vdf[key],
                    "officialtranscription": True,
                    "voiceline_id": key,
                })

    for speaker_topics in result.values():
        if isinstance(speaker_topics, dict) and isinstance(speaker_topics.get("Self"), dict):
            speaker_topics["Self"] = sort_subject_topics(group_config, speaker_topics["Self"])
    materialized = _materialize_voicelines(result, audio_dir, vdf)
    assert isinstance(materialized, dict)
    return materialized, set(organizer.disregarded_heroes)


def _conversation_key_from_name(
    filename: str,
    aliases: dict[str, str],
) -> tuple[tuple[tuple[str, str], str] | tuple[tuple[str, str], str, str], int, int, str] | None:
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
    resolve = lambda value: aliases.get(value.casefold(), value)
    pair = tuple(sorted((resolve(char1), resolve(char2))))
    key = (pair, convo, topic) if topic else (pair, convo)
    return key, int(part), int(variation), resolve(starter)


def _conversation_completeness(files: list[dict[str, object]]) -> tuple[bool, list[int], list[str]]:
    parts = sorted({int(item["part"]) for item in files if item.get("filename")})
    if not parts:
        return False, [], ["No audio files"]
    expected = list(range(parts[0], parts[-1] + 1))
    missing = [part for part in expected if part not in parts]
    reasons: list[str] = []
    if parts[0] != 1:
        reasons.append(f"Missing parts 1-{parts[0] - 1}")
        missing = [*range(1, parts[0]), *missing]
    if missing:
        reasons.append("Missing parts: " + ", ".join(str(item) for item in missing))
    if len(parts) <= 1:
        reasons.append("Only one part found")
    complete = parts[0] == 1 and not missing and len(parts) > 1
    return complete, sorted(set(missing)), reasons


def _load_conversation_vdf(
    vdf_path: Path | None,
    aliases: dict[str, str],
) -> dict[tuple, dict[tuple[int, int], dict[str, str]]]:
    if not vdf_path or not vdf_path.is_file():
        return {}
    _ensure_voiceline_import_path()
    from modules.vdf_kv_common import parse_quoted_kv_line

    result: dict[tuple, dict[tuple[int, int], dict[str, str]]] = {}
    for line in vdf_path.read_text(encoding="utf-8", errors="replace").splitlines():
        parsed_line = parse_quoted_kv_line(line)
        if not parsed_line:
            continue
        key_text, text = parsed_line
        parsed = _conversation_key_from_name(key_text, aliases)
        if not parsed:
            continue
        key, part, variation, speaker = parsed
        result.setdefault(key, {})[(part, variation)] = {"text": text, "speaker": speaker}
    return result


def parse_conversations(
    audio_dir: Path,
    character_mappings: Path,
    conversation_overrides: Path,
    vdf_path: Path | None,
    include_phantom: bool,
) -> dict[str, object]:
    mappings = _validate_mapping(character_mappings)
    aliases = _alias_index(mappings)
    grouped: dict[tuple, list[dict[str, object]]] = {}
    for path in sorted(audio_dir.rglob("*.mp3")):
        parsed = _conversation_key_from_name(path.name, aliases)
        if not parsed:
            continue
        key, part, variation, starter = parsed
        pair = key[0]
        grouped.setdefault(key, []).append({
            "filename": str(path.relative_to(audio_dir)),
            "part": part,
            "variation": variation,
            "characters": pair,
            "starter": starter,
            "topic": key[2] if len(key) > 2 else None,
        })

    vdf = _load_conversation_vdf(vdf_path, aliases)
    if include_phantom:
        for key, parts in vdf.items():
            files = grouped.setdefault(key, [])
            existing = {(int(item["part"]), int(item["variation"])) for item in files}
            for (part, variation), data in parts.items():
                if (part, variation) not in existing:
                    files.append({
                        "filename": "",
                        "part": part,
                        "variation": variation,
                        "characters": key[0],
                        "starter": data["speaker"],
                        "topic": key[2] if len(key) > 2 else None,
                        "is_phantom": True,
                        "speaker": data["speaker"],
                    })

    override_payload = _read_json(conversation_overrides)
    complete_overrides = set()
    if isinstance(override_payload, dict) and isinstance(override_payload.get("complete_conversations"), list):
        complete_overrides = {
            item for item in override_payload["complete_conversations"] if isinstance(item, str)
        }

    conversations: list[dict[str, object]] = []
    for key, files in sorted(grouped.items(), key=lambda item: str(item[0])):
        pair, convo_number, *topic_values = key
        topic = topic_values[0] if topic_values else None
        conversation_id = f"{pair[0]}_{pair[1]}_convo{convo_number}" + (f"_{topic}" if topic else "")
        complete, missing, _reasons = _conversation_completeness(files)
        if conversation_id in complete_overrides:
            complete, missing = True, []
        lines: list[dict[str, object]] = []
        for item in sorted(files, key=lambda value: (int(value["part"]), int(value["variation"]), str(value["filename"]))):
            part = int(item["part"])
            variation = int(item["variation"])
            official = vdf.get(key, {}).get((part, variation))
            audio_key = Path(str(item["filename"])).as_posix() if item["filename"] else ""
            filename = Path(audio_key).name if audio_key else ""
            speaker = (
                str(item.get("speaker"))
                if item.get("speaker")
                else aliases.get(filename.split("_", 1)[0].casefold(), filename.split("_", 1)[0])
            )
            line: dict[str, object] = {
                "part": part,
                "variation": variation,
                "speaker": speaker,
                "filename": audio_key,
                "transcription": official["text"] if official else "",
                "has_transcription": bool(official),
            }
            if official:
                line["officialtranscription"] = True
            lines.append(line)
        conversations.append({
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
        })
    return {
        "export_date": datetime.now().isoformat(),
        "total_conversations": len(conversations),
        "conversations": conversations,
    }


def _walk_filenames(value: object) -> Iterable[str]:
    if isinstance(value, dict):
        filename = value.get("filename")
        if isinstance(filename, str) and filename:
            yield filename.replace("\\", "/").lstrip("/")
        for child in value.values():
            yield from _walk_filenames(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_filenames(child)


def create_coverage(audio_dir: Path, voicelines: object, conversations: object) -> dict[str, object]:
    all_files = [
        path for path in audio_dir.rglob("*") if path.is_file() and path.suffix.casefold() == ".mp3"
    ]
    voice_names = set(_walk_filenames(voicelines))
    conversation_names = set(_walk_filenames(conversations))
    matched = voice_names | conversation_names
    unmatched = sorted(
        "sounds/vo/" + path.relative_to(audio_dir).as_posix()
        for path in all_files
        if path.relative_to(audio_dir).as_posix() not in matched
    )
    by_folder: dict[str, list[str]] = {}
    for relative in unmatched:
        folder, _, filename = relative.rpartition("/")
        by_folder.setdefault(folder, []).append(filename)
    total = len(all_files)
    matched_count = sum(path.relative_to(audio_dir).as_posix() in matched for path in all_files)
    return {
        "summary": {
            "total_files": total,
            "matched_files": matched_count,
            "unmatched_files": len(unmatched),
            "coverage_percentage": round((matched_count / total * 100) if total else 0.0, 2),
            "matched_in_voicelines": len(voice_names),
            "matched_in_conversations": len(conversation_names),
        },
        "unmatched_by_folder": {key: sorted(value) for key, value in sorted(by_folder.items())},
        "unmatched_files": unmatched,
    }


def prepare_vpk_export(
    settings: VpkPipelineSettings,
    progress: Progress = print,
) -> VpkPipelineResult:
    """Create or resume one VPK-to-baseline source workspace."""
    _validate_settings(settings)
    mappings, aliases, groups, overrides, vocabulary = ensure_game_configs(settings)

    # Loading validates the selected group file before a large extraction starts.
    _ensure_voiceline_import_path()
    from modules.voiceline_groups import load_group_config
    load_group_config(groups)

    workspace = settings.workspace
    source = settings.source_dir
    state_path = workspace / "pipeline-state.json"
    workspace.mkdir(parents=True, exist_ok=True)
    fingerprint = _quick_vpk_fingerprint(settings.vpk_path.resolve())
    old_state = _read_json(state_path) if state_path.is_file() else {}
    same_source = isinstance(old_state, dict) and old_state.get("vpkFingerprint") == fingerprint
    audio_root = source / "Audio"

    if settings.force_reextract or not same_source or not audio_root.is_dir():
        progress("Preparing the persistent version workspace...")
        try:
            state_path.unlink()
        except FileNotFoundError:
            pass
        _safe_replace(source, workspace)
        audio_root = source / "Audio"
        _run_source2viewer(
            settings.source2viewer_binary.resolve(),
            settings.vpk_path.resolve(),
            audio_root,
            "sounds/vo",
            settings.extraction_threads,
            progress,
        )
        old_state = {
            "schemaVersion": 1,
            "game": settings.game,
            "versionId": settings.version_id,
            "vpkPath": str(settings.vpk_path.resolve()),
            "vpkFingerprint": fingerprint,
            "extractionComplete": True,
            "updatedAt": datetime.now().isoformat(),
        }
        _write_json(state_path, old_state)
    else:
        progress("VPK fingerprint is unchanged; reusing the existing audio extraction.")

    audio_dir = _find_audio_dir(audio_root)
    audio_count = sum(path.suffix.casefold() == ".mp3" for path in audio_dir.rglob("*"))
    progress(f"Audio inventory: {audio_count:,} MP3 files at {audio_dir}.")

    game_root, localization, hero_names, vdf_path = _prepare_metadata(settings, source, progress)
    localization_output = source / "Localization"
    localization_ready = (localization_output / "manifest.json").is_file()
    can_reuse_localization = (
        same_source
        and isinstance(old_state, dict)
        and old_state.get("localizationComplete") is True
        and localization_ready
    )
    if settings.extract_localization and not can_reuse_localization:
        _export_localization(
            settings,
            source,
            localization,
            hero_names,
            mappings,
            progress,
        )
        old_state = dict(old_state) if isinstance(old_state, dict) else {}
        old_state["localizationComplete"] = (localization_output / "manifest.json").is_file()
        _write_json(state_path, old_state)
    elif settings.extract_localization:
        progress("VPK fingerprint is unchanged; reusing generated localization.")

    icon_output = source / "IconPacks" / "default"
    icon_manifest_path = icon_output / "manifest.json"
    icon_manifest = _read_json(icon_manifest_path) if icon_manifest_path.is_file() else {}
    icons_ready = (
        isinstance(icon_manifest, dict)
        and icon_manifest.get("extractionFormatVersion") == HISTORICAL_ICON_FORMAT_VERSION
    )
    can_reuse_icons = (
        same_source
        and isinstance(old_state, dict)
        and old_state.get("iconsComplete") is True
        and icons_ready
    )
    if settings.extract_icons and not can_reuse_icons:
        _export_historical_icons(settings, source, mappings, progress)
        old_state = dict(old_state) if isinstance(old_state, dict) else {}
        old_state["iconsComplete"] = (icon_output / "manifest.json").is_file()
        _write_json(state_path, old_state)
    elif settings.extract_icons:
        progress("VPK fingerprint is unchanged; reusing the historical icon pack.")

    progress("Parsing conversations without the conversation GUI...")
    conversations = parse_conversations(
        audio_dir,
        mappings,
        overrides,
        vdf_path,
        settings.include_phantom,
    )
    _write_json(source / "all_conversations.json", conversations)

    progress("Parsing and grouping voicelines from JSON configuration...")
    voicelines, unresolved = parse_voicelines(
        audio_dir,
        mappings,
        aliases,
        groups,
        vdf_path,
        settings.include_phantom,
        progress,
    )
    if unresolved:
        progress(
            f"Review warning: {len(unresolved)} unresolved voiceline speaker/subject aliases: "
            + ", ".join(sorted(unresolved)[:30])
        )
    _write_json(source / "all_voicelines.json", voicelines)
    _write_json(source / "coverage.json", create_coverage(audio_dir, voicelines, conversations))

    voice_count = sum(1 for _item in _walk_filenames(voicelines))
    conversation_count = int(conversations["total_conversations"])
    old_state = dict(old_state) if isinstance(old_state, dict) else {}
    old_state.update({
        "parsed": True,
        "audioCount": audio_count,
        "voicelineCount": voice_count,
        "conversationCount": conversation_count,
        "updatedAt": datetime.now().isoformat(),
    })
    _write_json(state_path, old_state)
    progress(
        f"VPK source ready: {voice_count:,} voicelines and "
        f"{conversation_count:,} conversations."
    )
    return VpkPipelineResult(
        source_dir=source,
        workspace=workspace,
        audio_dir=audio_dir,
        state_path=state_path,
        character_mappings=mappings,
        topic_aliases=aliases,
        voiceline_groups=groups,
        conversation_overrides=overrides,
        transcription_vocabulary=vocabulary,
        audio_count=audio_count,
        voiceline_count=voice_count,
        conversation_count=conversation_count,
    )
