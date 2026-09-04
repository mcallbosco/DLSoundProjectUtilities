"""Mine one Deadlock VPK and build the source consumed by baseline generation.

The desktop application and CLI share this coordinator.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Callable, Iterable

from .extraction.localization import export_hero_names, export_localizations
from .errors import VpkPipelineError
from .settings import DEFAULTS_DIR
from .image_dimensions import read_image_dimensions
from .parsing.common import (
    alias_index as _alias_index,
    read_json as _read_json,
    validate_mapping as _validate_mapping,
)
from .parsing.conversations import parse_conversations
from .parsing.groups import load_group_config
from .parsing.voicelines import _normalize_shopkeeper_topics, parse_voicelines


Progress = Callable[[str], None]
UTILITIES_DIR = Path(__file__).resolve().parents[2]
ASSETS_DIR = DEFAULTS_DIR
VERSION_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
AUDIO_SUFFIXES = {".mp3", ".wav", ".ogg", ".m4a"}
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
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "convert-character-name-images.mjs"
)
CHARACTER_SELECT_BACKGROUND_CONVERTER = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "convert-character-select-backgrounds.mjs"
)



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
    extract_name_images: bool = True
    extract_character_select_backgrounds: bool = True
    name_image_max_height: int = DEFAULT_NAME_IMAGE_MAX_HEIGHT
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
    audio_filename_overrides: Path
    transcription_vocabulary: Path
    audio_count: int
    voiceline_count: int
    conversation_count: int


@dataclass(frozen=True)
class VpkVoiceAudioResult:
    """One isolated, reusable `sounds/vo` extraction from a VPK."""

    audio_dir: Path
    workspace: Path
    state_path: Path
    audio_count: int
    vpk_fingerprint: dict[str, object]
    reused: bool


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


def _ensure_version_audio_filename_overrides(settings: VpkPipelineSettings) -> Path:
    path = (
        settings.transcript_repo.expanduser().resolve()
        / "config"
        / settings.game
        / "versions"
        / settings.version_id
        / "audio-filename-overrides.json"
    )
    if not path.is_file():
        _write_json(path, {"schemaVersion": 1, "overrides": {}})
    return path


def _normalize_override_path(value: str, field: str, config_path: Path) -> str:
    raw = value.strip().replace("\\", "/")
    path = PurePosixPath(raw)
    if (
        not raw
        or raw.startswith("/")
        or any(part in {"", ".", ".."} for part in path.parts)
        or any(":" in part for part in path.parts)
        or path.suffix.casefold() != ".mp3"
    ):
        raise VpkPipelineError(
            f"{field} must be a safe relative MP3 path in {config_path}: {value!r}"
        )
    return path.as_posix()


def _load_audio_filename_overrides(path: Path) -> dict[str, str | None]:
    """Load original-path to parser-path overrides without renaming source audio."""
    payload = _read_json(path)
    if not isinstance(payload, dict) or payload.get("schemaVersion") != 1:
        raise VpkPipelineError(
            f"Audio filename overrides must be a schemaVersion 1 object: {path}"
        )
    entries = payload.get("overrides")
    if not isinstance(entries, dict):
        raise VpkPipelineError(f"Audio filename overrides must contain an overrides object: {path}")
    result: dict[str, str | None] = {}
    for source, rule in entries.items():
        if not isinstance(source, str) or not isinstance(rule, dict):
            raise VpkPipelineError(
                f"Audio filename overrides must map paths to rule objects: {path}"
            )
        normalized_source = _normalize_override_path(source, "Override source", path)
        key = normalized_source.casefold()
        if key in result:
            raise VpkPipelineError(f"Duplicate audio filename override for {source!r} in {path}")
        parse_as = rule.get("parseAs")
        ignore = rule.get("ignore") is True
        if ignore == isinstance(parse_as, str):
            raise VpkPipelineError(
                f"Override {source!r} must specify exactly one of parseAs or ignore: true in {path}"
            )
        result[key] = (
            None
            if ignore
            else _normalize_override_path(str(parse_as), f"parseAs for {source!r}", path)
        )
    return result


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
    if settings.name_image_max_height < 64 or settings.name_image_max_height > 4096:
        raise VpkPipelineError("Character-name image height must be between 64 and 4096 pixels.")


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


def extract_vpk_voice_audio(
    *,
    source2viewer_binary: Path,
    vpk_path: Path,
    workspace: Path,
    extraction_threads: int = 8,
    force_reextract: bool = False,
    progress: Progress = print,
) -> VpkVoiceAudioResult:
    """Decode `sounds/vo` from one VPK into an isolated persistent workspace."""
    binary = source2viewer_binary.expanduser().resolve()
    vpk = vpk_path.expanduser().resolve()
    resolved_workspace = workspace.expanduser().resolve()
    if not binary.is_file():
        raise VpkPipelineError(f"Source2Viewer executable does not exist: {binary}")
    if not vpk.is_file() or vpk.suffix.casefold() != ".vpk":
        raise VpkPipelineError(f"Select a valid .vpk file: {vpk}")
    if extraction_threads < 1 or extraction_threads > 64:
        raise VpkPipelineError("Extraction threads must be between 1 and 64.")

    resolved_workspace.mkdir(parents=True, exist_ok=True)
    state_path = resolved_workspace / "vpk-audio-state.json"
    audio_root = resolved_workspace / "Audio"
    fingerprint = _quick_vpk_fingerprint(vpk)
    old_state = _read_json(state_path) if state_path.is_file() else {}
    same_source = (
        isinstance(old_state, dict)
        and old_state.get("vpkFingerprint") == fingerprint
        and audio_root.is_dir()
    )
    reused = same_source and not force_reextract
    if not reused:
        try:
            state_path.unlink()
        except FileNotFoundError:
            pass
        _safe_replace(audio_root, resolved_workspace)
        _run_source2viewer(
            binary,
            vpk,
            audio_root,
            "sounds/vo",
            extraction_threads,
            progress,
        )
        _write_json(state_path, {
            "schemaVersion": 1,
            "vpkPath": str(vpk),
            "vpkFingerprint": fingerprint,
            "extractionComplete": True,
            "updatedAt": datetime.now().isoformat(),
        })
    else:
        progress("VPK fingerprint is unchanged; reusing the isolated voice-audio extraction.")

    audio_dir = _find_audio_dir(audio_root)
    audio_count = sum(
        path.is_file() and path.suffix.casefold() == ".mp3"
        for path in audio_dir.rglob("*")
    )
    if not audio_count:
        raise VpkPipelineError(
            f"Source2Viewer did not decode any MP3 voice audio from {vpk}."
        )
    progress(f"VPK voice-audio inventory: {audio_count:,} MP3 files at {audio_dir}.")
    return VpkVoiceAudioResult(
        audio_dir=audio_dir,
        workspace=resolved_workspace,
        state_path=state_path,
        audio_count=audio_count,
        vpk_fingerprint=fingerprint,
        reused=reused,
    )


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
    destination = source_dir / "Localization"
    export_localizations(localization_source, destination, progress)
    if hero_name_source:
        export_hero_names(hero_name_source, destination, character_mappings, progress)


def _vpk_name_image_filters(binary: Path, vpk: Path) -> tuple[str, ...]:
    """Return only the supported asset filters present in one VPK."""
    available: list[str] = []
    for file_filter in NAME_IMAGE_FILTERS:
        command = [
            str(binary),
            "-i", str(vpk),
            "--vpk_list",
            "-f", file_filter,
        ]
        try:
            completed = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except OSError as exc:
            raise VpkPipelineError(f"Could not inspect character-name images in {vpk}: {exc}") from exc
        if completed.returncode:
            raise VpkPipelineError(
                f"Source2Viewer exited with status {completed.returncode} while inspecting "
                f"character-name images in {vpk}."
            )
        expected_marker = file_filter.rsplit("/", 1)[-1].casefold()
        if expected_marker in completed.stdout.casefold():
            available.append(file_filter)
    return tuple(available)


def _character_name_image_vpks(
    settings: VpkPipelineSettings,
    game_root: Path | None,
) -> dict[str, Path]:
    result = {"english": settings.vpk_path.resolve()}
    if not game_root:
        return result
    game_dir = game_root / "game"
    if not game_dir.is_dir():
        return result
    for directory in sorted(game_dir.glob("citadel_*"), key=lambda item: item.name.casefold()):
        if not directory.is_dir():
            continue
        language = directory.name[len("citadel_"):].strip().casefold()
        vpk = directory / "pak01_dir.vpk"
        if language and vpk.is_file():
            result[language] = vpk.resolve()
    return result


def _character_name_image_inputs(vpks: dict[str, Path]) -> dict[str, object]:
    return {
        language: {
            "path": str(vpk),
            "fingerprint": _quick_vpk_fingerprint(vpk),
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
        raise VpkPipelineError(f"{label.capitalize()} converter is missing: {converter}")
    completed = subprocess.run(
        [node, str(converter), *arguments],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
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
        raise VpkPipelineError(f"{label.capitalize()} converter returned invalid JSON.") from exc
    images = payload.get("images") if isinstance(payload, dict) else None
    if not isinstance(images, dict):
        raise VpkPipelineError(f"{label.capitalize()} converter did not return an image map.")
    converted = {
        str(key): value
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
            "--source", str(extracted),
            "--output", str(destination),
            "--max-height", str(max_height),
        ],
        "character-name image",
    )


def _export_character_name_images(
    settings: VpkPipelineSettings,
    source_dir: Path,
    game_root: Path | None,
    character_mappings: Path,
    progress: Progress,
) -> tuple[int, dict[str, object]]:
    """Extract available localized wordmarks and package immutable WebPs."""
    staging = source_dir.parent / "character-name-image-extraction"
    destination = source_dir / "CharacterNameImages"
    _safe_replace(staging, source_dir.parent)
    _safe_replace(destination, source_dir)
    mappings = _validate_mapping(character_mappings)
    aliases = _alias_index(mappings)
    vpks = _character_name_image_vpks(settings, game_root)
    languages: dict[str, dict[str, dict[str, object]]] = {}
    image_count = 0
    try:
        for language, vpk in sorted(vpks.items()):
            available_filters = _vpk_name_image_filters(
                settings.source2viewer_binary.resolve(),
                vpk,
            )
            if not available_filters:
                progress(f"Character-name images: [{language}] no supported assets found; skipping.")
                continue
            extracted = staging / language
            _safe_replace(extracted, staging)
            for file_filter in available_filters:
                _run_source2viewer(
                    settings.source2viewer_binary.resolve(),
                    vpk,
                    extracted,
                    file_filter,
                    settings.extraction_threads,
                    progress,
                )
            converted, conversion_warnings = _run_name_image_converter(
                extracted,
                destination / language,
                settings.name_image_max_height,
            )
            for warning in conversion_warnings:
                progress(
                    f"Character-name images: [{language}] skipped malformed asset: {warning}"
                )
            if not converted:
                progress(f"Character-name images: [{language}] no supported assets found; skipping.")
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
        progress("Character-name images were not present in the selected VPK set; continuing without them.")
        return 0, {"available": False}

    _write_json(destination / "manifest.json", {
        "schemaVersion": 1,
        "extractionFormatVersion": CHARACTER_NAME_IMAGE_FORMAT_VERSION,
        "maxHeight": settings.name_image_max_height,
        "languages": languages,
    })
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
            "--source", str(extracted),
            "--output", str(destination),
            "--width", str(width),
        ],
        "character-select background",
    )


def _export_character_select_backgrounds(
    settings: VpkPipelineSettings,
    source_dir: Path,
    character_mappings: Path,
    progress: Progress,
) -> tuple[int, dict[str, object]]:
    """Extract the right half of each hero-select backdrop as an immutable WebP set."""
    staging = source_dir.parent / "character-select-background-extraction"
    destination = source_dir / "CharacterSelectBackgrounds"
    _safe_replace(staging, source_dir.parent)
    _safe_replace(destination, source_dir)
    mappings = _validate_mapping(character_mappings)
    aliases = _alias_index(mappings)
    try:
        _run_source2viewer(
            settings.source2viewer_binary.resolve(),
            settings.vpk_path.resolve(),
            staging,
            CHARACTER_SELECT_BACKGROUND_FILTER,
            settings.extraction_threads,
            progress,
        )
        converted, conversion_warnings = _run_character_select_background_converter(
            staging,
            destination,
        )
        for warning in conversion_warnings:
            progress(f"Character-select backgrounds: skipped malformed asset: {warning}")
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

    _write_json(destination / "manifest.json", {
        "schemaVersion": 1,
        "extractionFormatVersion": CHARACTER_SELECT_BACKGROUND_FORMAT_VERSION,
        "crop": "right-half",
        "maxWidth": DEFAULT_CHARACTER_SELECT_BACKGROUND_WIDTH,
        "backgrounds": entries,
    })
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
    entries = list(re.finditer(r"(?m)^\s*hero_([a-z0-9_]+)\s*=\s*$", text, re.IGNORECASE))
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
        block = text[entry.end():end]
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
    aliases = _alias_index(mappings)
    owners = _historical_icon_owners(extracted_root)
    found: dict[str, dict[str, Path]] = {
        variant: {}
        for variant in HISTORICAL_ICON_VARIANTS.values()
        if variant in enabled_variants
    }
    for path in sorted(extracted_root.rglob("*")):
        if not path.is_file() or path.suffix.casefold() not in {".png", ".webp", ".jpg", ".jpeg"}:
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

    _write_json(destination / "manifest.json", {
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
    })
    return image_count


def _export_historical_icons(
    settings: VpkPipelineSettings,
    source_dir: Path,
    character_mappings: Path,
    progress: Progress,
    *,
    include_highlight_variants: bool = True,
) -> int:
    """Extract and package version-correct hero portraits directly from a VPK."""
    return _export_historical_icons_from_vpk(
        source2viewer_binary=settings.source2viewer_binary.resolve(),
        vpk_path=settings.vpk_path.resolve(),
        source_dir=source_dir,
        character_mappings=character_mappings,
        extraction_threads=settings.extraction_threads,
        include_highlight_variants=include_highlight_variants,
        progress=progress,
    )


def _export_historical_icons_from_vpk(
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
    _safe_replace(staging, source_dir.parent)
    _safe_replace(prepared, source_dir.parent)
    try:
        _run_source2viewer(
            source2viewer_binary,
            vpk_path,
            staging,
            "panorama/images/heroes",
            extraction_threads,
            progress,
        )
        _run_source2viewer(
            source2viewer_binary,
            vpk_path,
            staging,
            "panorama/images/npcs/patron",
            extraction_threads,
            progress,
        )
        _run_source2viewer(
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
            _validate_mapping(character_mappings),
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
                raise VpkPipelineError(f"Historical icon destination is not a directory: {destination}")
            shutil.rmtree(destination)
        os.replace(prepared, destination)
        progress(f"Historical icon pack ready: {image_count:,} portraits at {destination}.")
        return image_count
    finally:
        if staging.is_dir():
            shutil.rmtree(staging)
        if prepared.is_dir():
            shutil.rmtree(prepared)


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
    filename_overrides_path = _ensure_version_audio_filename_overrides(settings)
    filename_overrides = _load_audio_filename_overrides(filename_overrides_path)
    ignored_override_count = sum(value is None for value in filename_overrides.values())
    progress(
        f"Loaded {len(filename_overrides):,} version audio filename override(s) "
        f"({ignored_override_count:,} ignored)."
    )

    # Loading validates the selected group file before a large extraction starts.
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

    name_image_output = source / "CharacterNameImages"
    name_image_manifest_path = name_image_output / "manifest.json"
    if settings.extract_name_images:
        name_image_vpks = _character_name_image_vpks(settings, game_root)
        name_image_inputs = _character_name_image_inputs(name_image_vpks)
        saved_name_image_state = (
            old_state.get("characterNameImages", {}) if isinstance(old_state, dict) else {}
        )
        name_image_available = (
            isinstance(saved_name_image_state, dict)
            and saved_name_image_state.get("available") is True
        )
        name_image_absent = (
            isinstance(saved_name_image_state, dict)
            and saved_name_image_state.get("available") is False
        )
        can_reuse_name_images = (
            isinstance(saved_name_image_state, dict)
            and saved_name_image_state.get("complete") is True
            and saved_name_image_state.get("extractionFormatVersion")
            == CHARACTER_NAME_IMAGE_FORMAT_VERSION
            and saved_name_image_state.get("maxHeight") == settings.name_image_max_height
            and saved_name_image_state.get("inputs") == name_image_inputs
            and (
                (name_image_available and name_image_manifest_path.is_file())
                or (name_image_absent and not name_image_manifest_path.exists())
            )
        )
        if not can_reuse_name_images:
            image_count, availability = _export_character_name_images(
                settings,
                source,
                game_root,
                mappings,
                progress,
            )
            old_state = dict(old_state) if isinstance(old_state, dict) else {}
            old_state["characterNameImages"] = {
                "complete": True,
                "available": availability["available"],
                "imageCount": image_count,
                "extractionFormatVersion": CHARACTER_NAME_IMAGE_FORMAT_VERSION,
                "maxHeight": settings.name_image_max_height,
                "inputs": name_image_inputs,
            }
            _write_json(state_path, old_state)
        else:
            progress("Character-name image inputs are unchanged; reusing generated WebPs.")
    elif name_image_output.is_dir():
        shutil.rmtree(name_image_output)

    background_output = source / "CharacterSelectBackgrounds"
    background_manifest_path = background_output / "manifest.json"
    saved_background_state = (
        old_state.get("characterSelectBackgrounds", {}) if isinstance(old_state, dict) else {}
    )
    background_available = (
        isinstance(saved_background_state, dict)
        and saved_background_state.get("available") is True
    )
    background_absent = (
        isinstance(saved_background_state, dict)
        and saved_background_state.get("available") is False
    )
    background_mapping_sha256 = hashlib.sha256(mappings.read_bytes()).hexdigest()
    can_reuse_backgrounds = (
        same_source
        and isinstance(saved_background_state, dict)
        and saved_background_state.get("complete") is True
        and saved_background_state.get("extractionFormatVersion")
        == CHARACTER_SELECT_BACKGROUND_FORMAT_VERSION
        and saved_background_state.get("mappingSha256") == background_mapping_sha256
        and (
            (background_available and background_manifest_path.is_file())
            or (background_absent and not background_manifest_path.exists())
        )
    )
    if settings.extract_character_select_backgrounds and not can_reuse_backgrounds:
        background_count, availability = _export_character_select_backgrounds(
            settings,
            source,
            mappings,
            progress,
        )
        old_state = dict(old_state) if isinstance(old_state, dict) else {}
        old_state["characterSelectBackgrounds"] = {
            "complete": True,
            "available": availability["available"],
            "imageCount": background_count,
            "extractionFormatVersion": CHARACTER_SELECT_BACKGROUND_FORMAT_VERSION,
            "crop": "right-half",
            "maxWidth": DEFAULT_CHARACTER_SELECT_BACKGROUND_WIDTH,
            "mappingSha256": background_mapping_sha256,
        }
        _write_json(state_path, old_state)
    elif settings.extract_character_select_backgrounds:
        progress("VPK fingerprint is unchanged; reusing character-select backgrounds.")
    elif background_output.is_dir():
        shutil.rmtree(background_output)

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
        icon_count = _export_historical_icons(settings, source, mappings, progress)
        generated_icon_manifest = _read_json(icon_manifest_path)
        generated_icon_variants = (
            list(generated_icon_manifest.get("icons", {}).keys())
            if isinstance(generated_icon_manifest, dict)
            and isinstance(generated_icon_manifest.get("icons"), dict)
            else []
        )
        old_state = dict(old_state) if isinstance(old_state, dict) else {}
        old_state["iconsComplete"] = (icon_output / "manifest.json").is_file()
        old_state["historicalIcons"] = {
            "complete": old_state["iconsComplete"],
            "extractionFormatVersion": HISTORICAL_ICON_FORMAT_VERSION,
            "imageCount": icon_count,
            "variants": generated_icon_variants,
        }
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
        filename_overrides,
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
        filename_overrides,
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
        audio_filename_overrides=filename_overrides_path,
        transcription_vocabulary=vocabulary,
        audio_count=audio_count,
        voiceline_count=voice_count,
        conversation_count=conversation_count,
    )
