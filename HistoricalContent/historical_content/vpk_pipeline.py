"""Mine one Deadlock VPK and build the source consumed by baseline generation.

The desktop application and CLI share this coordinator.
"""

from __future__ import annotations

import hashlib
import re
import shutil
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath

from .errors import VpkPipelineError
from .settings import DEFAULTS_DIR
from .extraction.images import (
    CHARACTER_NAME_IMAGE_FORMAT_VERSION,
    CHARACTER_SELECT_BACKGROUND_FORMAT_VERSION,
    DEFAULT_CHARACTER_SELECT_BACKGROUND_WIDTH,
    DEFAULT_NAME_IMAGE_MAX_HEIGHT,
    HISTORICAL_ICON_FORMAT_VERSION,
    character_name_image_inputs,
    character_name_image_vpks,
    export_character_name_images,
    export_character_select_backgrounds,
    export_historical_icons,
)
from .extraction.localization import export_hero_names, export_localizations
from .extraction.source2viewer import (
    VpkVoiceAudioResult as VpkVoiceAudioResult,  # noqa: PLC0414 - public API
)
from .extraction.source2viewer import (
    extract_vpk_voice_audio as extract_vpk_voice_audio,  # noqa: PLC0414 - public API
)
from .extraction.source2viewer import (
    find_audio_dir,
    find_game_root,
    quick_vpk_fingerprint,
    replace_directory,
    run_source2viewer,
)
from .json_io import write_json
from .parsing.common import read_json
from .parsing.conversations import parse_conversations
from .parsing.groups import load_group_config
from .parsing.voicelines import parse_voicelines

Progress = Callable[[str], None]
UTILITIES_DIR = Path(__file__).resolve().parents[2]
ASSETS_DIR = DEFAULTS_DIR
VERSION_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
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
        write_json(path, {"schemaVersion": 1, "overrides": {}})
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
    payload = read_json(path)
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


def _prepare_metadata(
    settings: VpkPipelineSettings,
    source_dir: Path,
    progress: Progress,
) -> tuple[Path | None, Path | None, Path | None, Path | None]:
    game_root = find_game_root(settings.vpk_path.resolve())
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
    fingerprint = quick_vpk_fingerprint(settings.vpk_path.resolve())
    old_state = read_json(state_path) if state_path.is_file() else {}
    same_source = isinstance(old_state, dict) and old_state.get("vpkFingerprint") == fingerprint
    audio_root = source / "Audio"

    if settings.force_reextract or not same_source or not audio_root.is_dir():
        progress("Preparing the persistent version workspace...")
        try:
            state_path.unlink()
        except FileNotFoundError:
            pass
        replace_directory(source, workspace)
        audio_root = source / "Audio"
        run_source2viewer(
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
            "updatedAt": datetime.now().isoformat(),  # noqa: DTZ005 - preserve cache timestamps
        }
        write_json(state_path, old_state)
    else:
        progress("VPK fingerprint is unchanged; reusing the existing audio extraction.")

    audio_dir = find_audio_dir(audio_root)
    audio_count = sum(path.suffix.casefold() == ".mp3" for path in audio_dir.rglob("*"))
    progress(f"Audio inventory: {audio_count:,} MP3 files at {audio_dir}.")

    game_root, localization, hero_names, vdf_path = _prepare_metadata(settings, source, progress)
    localization_output = source / "Localization"
    localization_ready = (localization_output / "manifest.json").is_file()
    can_reuse_localization = (
        same_source
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
        old_state["localizationComplete"] = (localization_output / "manifest.json").is_file()
        write_json(state_path, old_state)
    elif settings.extract_localization:
        progress("VPK fingerprint is unchanged; reusing generated localization.")

    name_image_output = source / "CharacterNameImages"
    name_image_manifest_path = name_image_output / "manifest.json"
    if settings.extract_name_images:
        name_image_vpks = character_name_image_vpks(settings.vpk_path, game_root)
        name_image_inputs = character_name_image_inputs(name_image_vpks)
        saved_name_image_state = (
            old_state.get("characterNameImages", {})
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
            image_count, availability = export_character_name_images(
                source2viewer_binary=settings.source2viewer_binary,
                vpk_path=settings.vpk_path,
                source_dir=source,
                game_root=game_root,
                character_mappings=mappings,
                extraction_threads=settings.extraction_threads,
                max_height=settings.name_image_max_height,
                progress=progress,
            )
            old_state["characterNameImages"] = {
                "complete": True,
                "available": availability["available"],
                "imageCount": image_count,
                "extractionFormatVersion": CHARACTER_NAME_IMAGE_FORMAT_VERSION,
                "maxHeight": settings.name_image_max_height,
                "inputs": name_image_inputs,
            }
            write_json(state_path, old_state)
        else:
            progress("Character-name image inputs are unchanged; reusing generated WebPs.")
    elif name_image_output.is_dir():
        shutil.rmtree(name_image_output)

    background_output = source / "CharacterSelectBackgrounds"
    background_manifest_path = background_output / "manifest.json"
    saved_background_state = (
        old_state.get("characterSelectBackgrounds", {})
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
        background_count, availability = export_character_select_backgrounds(
            source2viewer_binary=settings.source2viewer_binary,
            vpk_path=settings.vpk_path,
            source_dir=source,
            character_mappings=mappings,
            extraction_threads=settings.extraction_threads,
            progress=progress,
        )
        old_state["characterSelectBackgrounds"] = {
            "complete": True,
            "available": availability["available"],
            "imageCount": background_count,
            "extractionFormatVersion": CHARACTER_SELECT_BACKGROUND_FORMAT_VERSION,
            "crop": "right-half",
            "maxWidth": DEFAULT_CHARACTER_SELECT_BACKGROUND_WIDTH,
            "mappingSha256": background_mapping_sha256,
        }
        write_json(state_path, old_state)
    elif settings.extract_character_select_backgrounds:
        progress("VPK fingerprint is unchanged; reusing character-select backgrounds.")
    elif background_output.is_dir():
        shutil.rmtree(background_output)

    icon_output = source / "IconPacks" / "default"
    icon_manifest_path = icon_output / "manifest.json"
    icon_manifest = read_json(icon_manifest_path) if icon_manifest_path.is_file() else {}
    icons_ready = (
        isinstance(icon_manifest, dict)
        and icon_manifest.get("extractionFormatVersion") == HISTORICAL_ICON_FORMAT_VERSION
    )
    can_reuse_icons = (
        same_source
        and old_state.get("iconsComplete") is True
        and icons_ready
    )
    if settings.extract_icons and not can_reuse_icons:
        icon_count = export_historical_icons(
            source2viewer_binary=settings.source2viewer_binary.resolve(),
            vpk_path=settings.vpk_path.resolve(),
            source_dir=source,
            character_mappings=mappings,
            extraction_threads=settings.extraction_threads,
            include_highlight_variants=True,
            progress=progress,
        )
        generated_icon_manifest = read_json(icon_manifest_path)
        generated_icon_variants = (
            list(generated_icon_manifest.get("icons", {}).keys())
            if isinstance(generated_icon_manifest, dict)
            and isinstance(generated_icon_manifest.get("icons"), dict)
            else []
        )
        old_state["iconsComplete"] = (icon_output / "manifest.json").is_file()
        old_state["historicalIcons"] = {
            "complete": old_state["iconsComplete"],
            "extractionFormatVersion": HISTORICAL_ICON_FORMAT_VERSION,
            "imageCount": icon_count,
            "variants": generated_icon_variants,
        }
        write_json(state_path, old_state)
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
    write_json(source / "all_conversations.json", conversations)

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
    write_json(source / "all_voicelines.json", voicelines)
    write_json(source / "coverage.json", create_coverage(audio_dir, voicelines, conversations))

    voice_count = sum(1 for _item in _walk_filenames(voicelines))
    conversation_count = int(conversations["total_conversations"])
    old_state.update({
        "parsed": True,
        "audioCount": audio_count,
        "voicelineCount": voice_count,
        "conversationCount": conversation_count,
        "updatedAt": datetime.now().isoformat(),  # noqa: DTZ005 - preserve cache timestamps
    })
    write_json(state_path, old_state)
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
