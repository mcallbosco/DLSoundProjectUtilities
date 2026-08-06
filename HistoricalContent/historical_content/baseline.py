"""Create a baseline transcript repository and local CDN preview from an export."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import sqlite3
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Callable, Iterable

from mutagen import File as MutagenFile, MutagenError

from .predefined_transcripts import (
    PredefinedTranscriptCatalog,
    PredefinedTranscriptError,
    load_predefined_transcripts,
)
from .transcription import DEFAULT_MODEL, transcribe_audio
from .version_catalog import (
    rebuild_local_preview_manifest,
    recalculate_version_statuses,
    register_local_version,
)


Progress = Callable[[str], None]
VERSION_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
TRANSCRIPT_SCHEMA_VERSION = 2
SKIPPED_EFFORT_SOURCE = "skippedeffort"
SKIPPED_NON_SPEECH_SOURCE = "skippednonspeech"
TERMINAL_BLANK_SOURCES = {SKIPPED_EFFORT_SOURCE, SKIPPED_NON_SPEECH_SOURCE}
TRANSCRIPT_SOURCES = {
    "generated",
    "official",
    "manual",
    *TERMINAL_BLANK_SOURCES,
}
TRANSCRIPT_SOURCE_PRIORITY = {
    SKIPPED_EFFORT_SOURCE: 0,
    SKIPPED_NON_SPEECH_SOURCE: 0,
    "generated": 1,
    "official": 2,
    "manual": 3,
}


class BaselineError(RuntimeError):
    pass


def _normalize_audio_key(filename: str) -> str:
    """Return a safe key relative to a version's audioBaseUrl."""
    value = filename.strip().replace("\\", "/")
    if not value:
        return ""
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or re.match(r"^[A-Za-z]:", value)
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise BaselineError(f"Audio filename must be a safe relative path: {filename!r}")
    return path.as_posix()


def _collect_route_characters(voicelines: object, conversations: object) -> set[str]:
    """Collect every speaker and target that needs a static character page."""
    names: dict[str, str] = {}

    def add(value: object) -> None:
        if not isinstance(value, str):
            return
        name = value.strip()
        if not name or name.casefold() == "self":
            return
        names.setdefault(name.casefold(), name)

    if isinstance(voicelines, dict):
        for speaker, targets in voicelines.items():
            add(speaker)
            if isinstance(targets, dict):
                for target, target_data in targets.items():
                    if isinstance(target_data, dict):
                        add(target)

    conversation_list = (
        conversations.get("conversations", [])
        if isinstance(conversations, dict)
        else []
    )
    if isinstance(conversation_list, list):
        for conversation in conversation_list:
            if not isinstance(conversation, dict):
                continue
            add(conversation.get("character1"))
            add(conversation.get("character2"))
            speakers = conversation.get("speakers", [])
            if isinstance(speakers, list):
                for speaker in speakers:
                    add(speaker)
            lines = conversation.get("lines", [])
            if isinstance(lines, list):
                for line in lines:
                    if isinstance(line, dict):
                        add(line.get("speaker"))
    return set(names.values())


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON object key: {key}")
        result[key] = value
    return result


def load_json(path: Path) -> object:
    try:
        return json.loads(
            path.read_text(encoding="utf-8-sig"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except Exception as exc:
        raise BaselineError(f"Invalid JSON in {path}: {exc}") from exc


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(_serialize_json(value), encoding="utf-8")
    os.replace(temporary, path)


def _serialize_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def _write_json_if_changed(path: Path, value: object) -> bool:
    serialized = _serialize_json(value)
    if path.is_file():
        try:
            if path.read_text(encoding="utf-8-sig") == serialized:
                return False
        except OSError:
            pass
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(serialized, encoding="utf-8")
    os.replace(temporary, path)
    return True


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class BaselineSettings:
    source_dir: Path
    transcript_repo: Path
    data_dir: Path
    version_id: str = "deadlock-base"
    label: str = "Historical baseline"
    game: str = "deadlock"
    model: str = DEFAULT_MODEL
    api_key: str | None = None
    transcription_vocabulary: Path | None = None
    predefined_transcripts: Path | None = None
    transcribe_missing: bool = True
    workers: int = 4
    initialize_git: bool = True
    include_audio: bool = True


@dataclass(frozen=True)
class BaselineResult:
    preview_root: Path
    publish_source: Path
    transcript_repo: Path
    categories_path: Path
    database_path: Path
    preview_version_id: str
    voiceline_count: int
    conversation_line_count: int
    missing_transcripts: int
    audio_count: int


def refresh_preview_categories(
    *,
    source_dir: Path,
    transcript_repo: Path,
    data_dir: Path,
    version_id: str,
    game: str = "deadlock",
    progress: Progress = print,
) -> Path:
    """Validate and refresh category files without re-indexing any audio."""
    source = source_dir.resolve()
    voiceline_path = _find_source_file(source, ("all_voicelines.json", "voicelines.json"))
    conversation_path = _find_source_file(source, ("all_conversations.json", "conversations.json"))
    voicelines = load_json(voiceline_path)
    conversations = load_json(conversation_path)
    if not isinstance(voicelines, dict) or not isinstance(conversations, dict):
        raise BaselineError("Source voiceline or conversation JSON has an unsupported structure.")
    characters = {str(key) for key in voicelines.keys()}
    for conversation in conversations.get("conversations", []):
        if isinstance(conversation, dict):
            characters.update(str(value) for value in conversation.get("speakers", []) if value)
    config_root = transcript_repo.resolve() / "config" / game
    default_path = config_root / "categories.json"
    override_path = config_root / "versions" / version_id / "categories.json"
    if not default_path.is_file() or not override_path.is_file():
        raise BaselineError("Category configuration is missing. Create the baseline first.")
    default_payload = load_json(default_path)
    override_payload = load_json(override_path)
    for name, payload in (("game default", default_payload), ("version override", override_payload)):
        errors, warnings = validate_categories(payload, characters)
        for warning in warnings:
            progress(f"Category warning ({name}): {warning}")
        if errors:
            raise BaselineError(f"Invalid {name} categories: " + " ".join(errors))
    preview_id = f"preview-{version_id}"
    preview_root = data_dir.resolve() / "preview-content"
    game_root = preview_root / game
    version_root = game_root / "versions" / preview_id
    if not (game_root / "manifest.json").is_file():
        raise BaselineError("Generated preview is missing. Create the baseline first.")
    write_json(game_root / "categories.json", default_payload)
    write_json(version_root / "categories.json", override_payload)
    publish_source = data_dir.resolve() / "generated" / version_id
    if publish_source.is_dir():
        write_json(publish_source / "categories.json", override_payload)
    progress("Category preview refreshed without re-indexing audio or transcripts.")
    return override_path


class AudioIndex:
    def __init__(self, root: Path):
        self.root = root
        self.by_name: dict[str, list[Path]] = {}
        self.hashes: dict[Path, str] = {}
        self.durations: dict[Path, float | None] = {}
        if root.is_dir():
            for path in root.rglob("*"):
                if path.is_file() and path.suffix.lower() in {".mp3", ".wav", ".ogg", ".m4a"}:
                    self.by_name.setdefault(path.name.lower(), []).append(path)

    def resolve(self, filename: str) -> Path | None:
        normalized = _normalize_audio_key(filename)
        if not normalized:
            return None
        direct = self.root.joinpath(*normalized.split("/"))
        if direct.is_file():
            return direct
        candidates = self.by_name.get(Path(normalized).name.lower(), [])
        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates[0]
        hashes = {self.hash(path) for path in candidates}
        if len(hashes) == 1:
            return candidates[0]
        raise BaselineError(f"Multiple different audio files share the basename {filename!r}.")

    def hash(self, path: Path | None) -> str | None:
        if path is None:
            return None
        if path not in self.hashes:
            self.hashes[path] = sha256_file(path)
        return self.hashes[path]

    def duration(self, path: Path | None) -> float | None:
        """Return audio duration in seconds, rounded to milliseconds."""
        if path is None:
            return None
        if path not in self.durations:
            duration: float | None = None
            try:
                audio = MutagenFile(path)
                value = float(audio.info.length) if audio is not None and audio.info else 0.0
                if math.isfinite(value) and value > 0:
                    duration = round(value, 3)
            except (OSError, ValueError, AttributeError, MutagenError):
                duration = None
            self.durations[path] = duration
        return self.durations[path]


def _find_source_file(root: Path, names: Iterable[str]) -> Path:
    for name in names:
        candidate = root / name
        if candidate.is_file():
            return candidate
    raise BaselineError(f"None of these required files exist in {root}: {', '.join(names)}")


def _find_audio_root(root: Path) -> Path:
    for name in ("Audio", "audio", "DeadlockAudio"):
        candidate = root / name
        if candidate.is_dir():
            # Direct VPK extraction retains Source 2's sounds/vo wrapper.  JSON
            # keys and CDN audio keys are relative to the voice root itself.
            voice_root = candidate / "sounds" / "vo"
            if voice_root.is_dir():
                return voice_root
            return candidate
    raise BaselineError(f"No Audio, audio, or DeadlockAudio folder exists in {root}.")


def _line_id(entry: dict[str, object]) -> str:
    value = entry.get("voiceline_id") or entry.get("lineId")
    if isinstance(value, str) and value.strip():
        return value.strip()
    filename = str(entry.get("filename") or "")
    return Path(filename).stem


def _text(entry: dict[str, object]) -> str:
    value = entry.get("transcription")
    if not isinstance(value, str):
        value = entry.get("text")
    return value.strip() if isinstance(value, str) else ""


def _is_effort_recording(filename: str) -> bool:
    """Return whether an audio key identifies a non-verbal effort recording."""
    stem = PurePosixPath(filename.replace("\\", "/")).stem.casefold()
    return bool(re.search(r"(?:^|[_-])efforts?(?:[_-]|$)", stem))


def _is_non_speech_recording(filename: str) -> bool:
    """Return whether an audio key is deterministically non-verbal."""
    path = PurePosixPath(filename.replace("\\", "/").casefold())
    stem = path.stem
    parts = set(path.parts[:-1])
    return (
        "emote" in parts
        or "sfx" in parts
        or bool(re.search(r"(?:^|[_-])pain(?:[_-]|$)", stem))
        or bool(re.search(r"(?:^|[_-])sfx(?:[_-]|$)", stem))
    )


def _walk_voicelines(node: object, path: tuple[str, ...] = ()):
    if isinstance(node, dict):
        if isinstance(node.get("filename"), str):
            yield path, node
            return
        for key, value in node.items():
            yield from _walk_voicelines(value, path + (str(key),))
    elif isinstance(node, list):
        for value in node:
            yield from _walk_voicelines(value, path)


def _transcript_path(repo: Path, filename: str) -> Path:
    """Return the readable transcript path for one normalized audio path."""
    normalized = _normalize_audio_key(filename)
    if not normalized:
        raise BaselineError("A transcript cannot be stored without an audio filename.")
    # Keep the audio suffix so foo.mp3 and foo.wav cannot share one JSON path.
    relative = PurePosixPath(f"{normalized}.json")
    return repo / "transcripts" / Path(*relative.parts)


def _normalize_transcript_hash(value: object, origin: Path) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value.casefold()):
        raise BaselineError(f"Transcript revision has an invalid SHA-256 value in {origin}.")
    return value.casefold()


def _normalize_transcript_revision(value: object, origin: Path) -> dict[str, object]:
    if not isinstance(value, dict):
        raise BaselineError(f"Transcript revision must be an object in {origin}.")
    text = value.get("text")
    source = value.get("source")
    if not isinstance(text, str):
        raise BaselineError(f"Transcript revision text must be a string in {origin}.")
    if source not in TRANSCRIPT_SOURCES:
        raise BaselineError(
            "Transcript revision source must be generated, official, manual, "
            f"{SKIPPED_EFFORT_SOURCE}, or {SKIPPED_NON_SPEECH_SOURCE} in {origin}."
        )
    revision: dict[str, object] = {
        "sha256": _normalize_transcript_hash(value.get("sha256"), origin),
        "text": text,
        "source": source,
    }
    if source in {"generated", SKIPPED_NON_SPEECH_SOURCE}:
        model = value.get("model")
        if model is not None and not isinstance(model, str):
            raise BaselineError(f"Transcript revision model must be a string in {origin}.")
        if model:
            revision["model"] = model
    return revision


def _revision_for_hash(
    document: dict[str, object], audio_hash: str | None
) -> dict[str, object] | None:
    for revision in document["revisions"]:
        if isinstance(revision, dict) and revision.get("sha256") == audio_hash:
            return revision
    return None


def _merge_loaded_revision(
    document: dict[str, object],
    candidate: dict[str, object],
    *,
    origin: Path,
) -> None:
    """Merge legacy or already-migrated data without losing human corrections."""
    existing = _revision_for_hash(document, candidate.get("sha256"))
    if existing is None:
        document["revisions"].append(candidate)
        return
    existing_text = str(existing.get("text") or "").strip()
    candidate_text = str(candidate.get("text") or "").strip()
    if not existing_text and candidate_text:
        existing.clear()
        existing.update(candidate)
        return
    if not candidate_text or existing_text == candidate_text:
        if TRANSCRIPT_SOURCE_PRIORITY.get(
            str(candidate.get("source")), -1
        ) > TRANSCRIPT_SOURCE_PRIORITY.get(
            str(existing.get("source")), -1
        ):
            existing.clear()
            existing.update(candidate)
        return
    existing_priority = TRANSCRIPT_SOURCE_PRIORITY.get(
        str(existing.get("source")), -1
    )
    candidate_priority = TRANSCRIPT_SOURCE_PRIORITY.get(
        str(candidate.get("source")), -1
    )
    if candidate_priority > existing_priority:
        existing.clear()
        existing.update(candidate)
    elif (
        candidate_priority
        == existing_priority
        == TRANSCRIPT_SOURCE_PRIORITY["manual"]
    ):
        raise BaselineError(
            f"Conflicting manual transcripts exist for {document['filename']!r} "
            f"and SHA-256 {candidate.get('sha256')!r}; conflict found in {origin}."
        )


def _get_transcript_document(
    documents: dict[str, dict[str, object]], filename: str
) -> dict[str, object]:
    normalized = _normalize_audio_key(filename)
    if not normalized:
        raise BaselineError("A transcript cannot be stored without an audio filename.")
    key = normalized.casefold()
    document = documents.get(key)
    if document is None:
        document = {
            "schemaVersion": TRANSCRIPT_SCHEMA_VERSION,
            "filename": normalized,
            "revisions": [],
        }
        documents[key] = document
    return document


def _load_transcript_documents(
    repo: Path,
) -> tuple[dict[str, dict[str, object]], list[Path], dict[str, str]]:
    documents: dict[str, dict[str, object]] = {}
    existing_serialized: dict[str, str] = {}
    transcript_root = repo / "transcripts"
    if transcript_root.is_dir():
        for path in sorted(transcript_root.rglob("*.json")):
            payload = load_json(path)
            if (
                not isinstance(payload, dict)
                or payload.get("schemaVersion") != TRANSCRIPT_SCHEMA_VERSION
                or not isinstance(payload.get("filename"), str)
                or not isinstance(payload.get("revisions"), list)
            ):
                raise BaselineError(f"Transcript file has an unsupported structure: {path}")
            filename = _normalize_audio_key(payload["filename"])
            expected_relative = f"{filename}.json"
            actual_relative = path.relative_to(transcript_root).as_posix()
            if actual_relative.casefold() != expected_relative.casefold():
                raise BaselineError(
                    f"Transcript file {path} must mirror its audio path at "
                    f"{_transcript_path(repo, filename)}."
                )
            existing_serialized[filename.casefold()] = _serialize_json(payload)
            document = _get_transcript_document(documents, filename)
            for value in payload["revisions"]:
                _merge_loaded_revision(
                    document,
                    _normalize_transcript_revision(value, path),
                    origin=path,
                )

    legacy_paths: list[Path] = []
    for legacy_root in (repo / "voicelines", repo / "conversations"):
        if not legacy_root.is_dir():
            continue
        for path in sorted(legacy_root.rglob("*.json")):
            payload = load_json(path)
            if not isinstance(payload, dict) or not isinstance(payload.get("lines"), list):
                raise BaselineError(f"Legacy transcript file has an unsupported structure: {path}")
            legacy_paths.append(path)
            for line in payload["lines"]:
                if not isinstance(line, dict):
                    continue
                filename = _normalize_audio_key(str(line.get("filename") or ""))
                if not filename:
                    raise BaselineError(f"Legacy transcript line has no filename in {path}.")
                source = line.get("source", "generated")
                revision_value: dict[str, object] = {
                    "sha256": line.get("audioSha256"),
                    "text": line.get("text", ""),
                    "source": source,
                }
                if source == "generated" and line.get("model"):
                    revision_value["model"] = line["model"]
                document = _get_transcript_document(documents, filename)
                _merge_loaded_revision(
                    document,
                    _normalize_transcript_revision(revision_value, path),
                    origin=path,
                )
    return documents, legacy_paths, existing_serialized


def _new_transcript_revision(
    entry: dict[str, object], audio_hash: str | None
) -> dict[str, object]:
    result: dict[str, object] = {
        "sha256": audio_hash,
        "text": _text(entry),
    }
    if entry.get("officialtranscription"):
        result["source"] = "official"
    elif _is_effort_recording(str(entry.get("filename") or "")):
        result["text"] = ""
        result["source"] = SKIPPED_EFFORT_SOURCE
    elif _is_non_speech_recording(str(entry.get("filename") or "")):
        result["text"] = ""
        result["source"] = SKIPPED_NON_SPEECH_SOURCE
    else:
        result["source"] = "generated"
        result["model"] = DEFAULT_MODEL
    return result


def _write_transcript_documents(
    repo: Path,
    documents: dict[str, dict[str, object]],
    existing_serialized: dict[str, str] | None = None,
) -> int:
    changed = 0
    existing_serialized = existing_serialized or {}
    for document in sorted(
        documents.values(), key=lambda value: str(value["filename"]).casefold()
    ):
        filename = str(document["filename"])
        if existing_serialized.get(filename.casefold()) == _serialize_json(document):
            continue
        changed += _write_json_if_changed(_transcript_path(repo, filename), document)
    return changed


def _write_transcript_document(
    repo: Path,
    document: dict[str, object],
) -> bool:
    """Atomically checkpoint one per-audio transcript document."""
    return _write_json_if_changed(
        _transcript_path(repo, str(document["filename"])),
        document,
    )


def _remove_migrated_legacy_files(repo: Path, legacy_paths: list[Path]) -> None:
    for path in legacy_paths:
        path.unlink()
    for legacy_root in (repo / "voicelines", repo / "conversations"):
        if not legacy_root.is_dir():
            continue
        for directory in sorted(
            (path for path in legacy_root.rglob("*") if path.is_dir()),
            key=lambda path: len(path.parts),
            reverse=True,
        ):
            try:
                directory.rmdir()
            except OSError:
                pass
        try:
            legacy_root.rmdir()
        except OSError:
            pass


def validate_categories(payload: object, characters: set[str]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(payload, dict):
        return ["categories.json must contain an object."], warnings
    if payload.get("schemaVersion") != 1:
        errors.append("schemaVersion must be 1.")
    default = payload.get("defaultCategory")
    categories = payload.get("categories")
    if not isinstance(default, str) or not default:
        errors.append("defaultCategory must be a non-empty string.")
    if not isinstance(categories, list):
        errors.append("categories must be an array.")
        return errors, warnings
    names: set[str] = set()
    assigned: dict[str, str] = {}
    for index, category in enumerate(categories):
        if not isinstance(category, dict):
            errors.append(f"categories[{index}] must be an object.")
            continue
        name = category.get("name")
        if not isinstance(name, str) or not name:
            errors.append(f"categories[{index}].name must be a non-empty string.")
            continue
        if name in names:
            errors.append(f"Duplicate category name: {name}")
        names.add(name)
        members = category.get("characters")
        if not isinstance(members, list) or any(not isinstance(item, str) for item in members):
            errors.append(f"Category {name!r} must contain a string-array characters field.")
            continue
        if not members:
            warnings.append(f"Category {name!r} is empty.")
        for character in members:
            if character in assigned:
                errors.append(
                    f"Character {character!r} is assigned to both {assigned[character]!r} and {name!r}."
                )
            assigned[character] = name
            if character not in characters:
                warnings.append(f"Category {name!r} references unknown character {character!r}.")
    if isinstance(default, str) and default not in names:
        errors.append("defaultCategory must name one of the visible categories.")
    unassigned = sorted(characters - set(assigned))
    if unassigned:
        warnings.append(
            f"{len(unassigned)} character(s) will use {default!r}: " + ", ".join(unassigned[:20])
        )
    return errors, warnings


def validate_character_names(payload: object, game: str) -> list[str]:
    errors: list[str] = []
    if not isinstance(payload, dict):
        return ["character-names.json must contain an object."]
    if payload.get("schemaVersion") != 1:
        errors.append("character-names.json schemaVersion must be 1.")
    if payload.get("game") != game:
        errors.append(f"character-names.json game must be {game!r}.")
    names = payload.get("names")
    if not isinstance(names, dict) or not names:
        errors.append("character-names.json names must be a non-empty object.")
        return errors
    normalized: set[str] = set()
    for key, display_name in names.items():
        if not isinstance(key, str) or not key.strip():
            errors.append("character-names.json contains an empty character key.")
            continue
        normalized_key = " ".join(key.strip().casefold().split())
        if normalized_key in normalized:
            errors.append(f"character-names.json contains a duplicate key: {key!r}.")
        normalized.add(normalized_key)
        if not isinstance(display_name, str) or not display_name.strip():
            errors.append(
                f"character-names.json display name for {key!r} must be a non-empty string."
            )
    return errors


def _default_character_display_name(value: str) -> str:
    return " ".join(part.capitalize() for part in value.replace("_", " ").split())


def _safe_replace_directory(path: Path, allowed_parent: Path) -> None:
    resolved = path.resolve()
    parent = allowed_parent.resolve()
    if parent not in resolved.parents or resolved == parent:
        raise BaselineError(f"Refusing to replace preview directory outside {parent}: {resolved}")
    if resolved.exists():
        shutil.rmtree(resolved)
    resolved.mkdir(parents=True)


def _link_or_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        destination.unlink()
    try:
        os.link(source, destination)
    except OSError:
        shutil.copy2(source, destination)


def _shared_audio_key(audio_hash: str) -> str:
    return f"sha256/{audio_hash[:2]}/{audio_hash}.mp3"


def _ensure_shared_audio(source: Path, destination: Path, audio_hash: str) -> None:
    """Create one immutable local object for an audio hash and verify reuse."""
    if destination.is_file():
        if destination.stat().st_size == source.stat().st_size and sha256_file(destination) == audio_hash:
            return
        raise BaselineError(f"Shared audio object is corrupt or has a hash collision: {destination}")
    _link_or_copy(source, destination)


def _copy_tree(source: Path, destination: Path) -> None:
    if not source.is_dir():
        return
    for path in source.rglob("*"):
        if path.is_file():
            _link_or_copy(path, destination / path.relative_to(source))


def _initialize_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    if not (repo / ".git").exists():
        try:
            subprocess.run(["git", "init", str(repo)], check=True, capture_output=True, text=True)
        except (OSError, subprocess.CalledProcessError) as exc:
            raise BaselineError(f"Could not initialize transcript Git repository: {exc}") from exc


def _write_repo_support(repo: Path) -> None:
    readme = repo / "README.md"
    if not readme.exists():
        readme.write_text(
            "# Deadlock Transcripts\n\n"
            "Human-readable transcript and content configuration used to generate VLViewer data.\n\n"
            "Audio transcripts are stored below `transcripts/` at paths that mirror the audio files.\n"
            "Each JSON file retains one revision for each distinct audio SHA-256 value.\n\n"
            "Edit a revision's `text`, set `source` to `manual`, remove `model`, preview locally, then commit.\n",
            encoding="utf-8",
        )
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "VLViewer transcript file",
        "type": "object",
        "required": ["schemaVersion", "filename", "revisions"],
        "additionalProperties": False,
        "properties": {
            "schemaVersion": {"const": TRANSCRIPT_SCHEMA_VERSION},
            "filename": {"type": "string", "minLength": 1},
            "revisions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["sha256", "text", "source"],
                    "additionalProperties": False,
                    "properties": {
                        "sha256": {
                            "oneOf": [
                                {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                                {"type": "null"},
                            ]
                        },
                        "text": {"type": "string"},
                        "source": {
                            "enum": [
                                "generated",
                                "official",
                                "manual",
                                SKIPPED_EFFORT_SOURCE,
                                SKIPPED_NON_SPEECH_SOURCE,
                            ]
                        },
                        "model": {"type": "string", "minLength": 1},
                    },
                },
            },
        },
    }
    write_json(repo / "schema.json", schema)
    legacy_glossary = repo / "glossary.txt"
    if legacy_glossary.is_file():
        legacy_glossary.unlink()


def build_transcription_prompt(vocabulary_path: Path) -> str:
    """Load structured per-game vocabulary and attach it as prompt context."""
    path = vocabulary_path.expanduser().resolve()
    if not path.is_file():
        raise BaselineError(f"Transcription vocabulary does not exist: {path}")
    payload = load_json(path)
    if not isinstance(payload, dict) or not payload:
        raise BaselineError(
            f"Transcription vocabulary must contain a non-empty JSON object: {path}"
        )
    for category, values in payload.items():
        if not isinstance(category, str) or not category.strip():
            raise BaselineError(
                f"Transcription vocabulary contains an empty category name: {path}"
            )
        if (
            not isinstance(values, list)
            or any(not isinstance(value, str) or not value.strip() for value in values)
        ):
            raise BaselineError(
                f"Transcription vocabulary category {category!r} must contain "
                f"only non-empty strings: {path}"
            )
    vocabulary_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return (
        "Transcribe this Deadlock voice line in English exactly. Preserve all spoken words. "
        "Do not add commentary or quotation marks. The following JSON contains authoritative "
        "Deadlock spellings, terminology, and transcription guidelines. Follow it when applicable: "
        f"{vocabulary_json}"
    )


def _open_database(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS versions (
          id TEXT PRIMARY KEY, game TEXT NOT NULL, label TEXT NOT NULL,
          is_baseline INTEGER NOT NULL, imported_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS version_assets (
          version_id TEXT NOT NULL, kind TEXT NOT NULL, line_id TEXT NOT NULL,
          audio_sha256 TEXT, filename TEXT NOT NULL, speaker TEXT,
          PRIMARY KEY (version_id, kind, line_id, audio_sha256)
        );
        CREATE INDEX IF NOT EXISTS version_assets_recording
          ON version_assets(line_id, audio_sha256);
        """
    )
    return connection


def create_baseline(settings: BaselineSettings, progress: Progress = print) -> BaselineResult:
    source = settings.source_dir.resolve()
    if not source.is_dir():
        raise BaselineError(f"Exported version folder does not exist: {source}")
    if not VERSION_RE.fullmatch(settings.version_id):
        raise BaselineError("Version ID must contain only lowercase letters, digits, and hyphens.")
    if not VERSION_RE.fullmatch(settings.game):
        raise BaselineError("Game ID must contain only lowercase letters, digits, and hyphens.")

    conversation_path = _find_source_file(source, ("all_conversations.json", "conversations.json"))
    voiceline_path = _find_source_file(source, ("all_voicelines.json", "voicelines.json"))
    audio_root = _find_audio_root(source)
    conversations = load_json(conversation_path)
    voicelines = load_json(voiceline_path)
    if not isinstance(conversations, dict) or not isinstance(conversations.get("conversations"), list):
        raise BaselineError(f"Unsupported conversations structure: {conversation_path}")
    if not isinstance(voicelines, dict):
        raise BaselineError(f"Unsupported voicelines structure: {voiceline_path}")

    predefined_catalog: PredefinedTranscriptCatalog | None = None
    if settings.predefined_transcripts is not None:
        try:
            predefined_catalog = load_predefined_transcripts(
                settings.predefined_transcripts
            )
        except PredefinedTranscriptError as exc:
            raise BaselineError(str(exc)) from exc
        progress(
            f"Loaded {predefined_catalog.total_rows} predefined transcript rows; "
            f"accepted {predefined_catalog.accepted_rows} safe official transcripts; "
            f"skipped {predefined_catalog.skipped_conflicts} conflicting rows."
        )

    repo = settings.transcript_repo.resolve()
    data_dir = settings.data_dir.resolve()
    if settings.initialize_git:
        _initialize_repo(repo)
    else:
        repo.mkdir(parents=True, exist_ok=True)
    _write_repo_support(repo)
    audio_index = AudioIndex(audio_root)
    progress(f"Indexed {sum(len(value) for value in audio_index.by_name.values())} audio files.")

    (
        transcript_documents,
        legacy_transcript_paths,
        existing_transcript_json,
    ) = _load_transcript_documents(repo)
    transcript_by_audio: dict[tuple[str, str | None], dict[str, object]] = {}
    document_by_revision_id: dict[int, dict[str, object]] = {}
    current_transcript_keys: set[tuple[str, str | None]] = set()
    referenced_audio_by_key: dict[str, tuple[str, Path]] = {}
    voice_records: list[tuple[str, dict[str, object], Path | None]] = []
    missing_by_audio: dict[
        tuple[str, str | None], tuple[dict[str, object], Path]
    ] = {}
    skipped_effort_keys: set[tuple[str, str | None]] = set()
    skipped_non_speech_keys: set[tuple[str, str | None]] = set()
    predefined_matched_paths: set[str] = set()
    predefined_applied_keys: set[tuple[str, str | None]] = set()

    def remember_audio(filename: str, path: Path | None) -> None:
        if path is None:
            return
        key = _normalize_audio_key(filename)
        if not key:
            return
        lookup = key.casefold()
        previous = referenced_audio_by_key.get(lookup)
        if previous and previous[1] != path:
            previous_hash = audio_index.hash(previous[1])
            current_hash = audio_index.hash(path)
            if previous_hash != current_hash:
                raise BaselineError(
                    f"Different audio files resolve to the same output key {key!r}."
                )
            return
        referenced_audio_by_key[lookup] = (key, path)

    def resolve_transcript(
        entry: dict[str, object], audio_path: Path | None, audio_hash: str | None
    ) -> tuple[dict[str, object], dict[str, object]]:
        filename = _normalize_audio_key(str(entry.get("filename") or ""))
        document = _get_transcript_document(transcript_documents, filename)
        revision = _revision_for_hash(document, audio_hash)
        if revision is None:
            revision = _new_transcript_revision(entry, audio_hash)
            document["revisions"].append(revision)
        key = (filename.casefold(), audio_hash)
        predefined_text = (
            predefined_catalog.transcripts.get(filename.casefold())
            if predefined_catalog is not None
            else None
        )
        if predefined_text is not None:
            predefined_matched_paths.add(filename.casefold())
        if _is_effort_recording(filename):
            source = revision.get("source")
            has_curated_text = source in {"manual", "official"} and bool(
                str(revision.get("text") or "").strip()
            )
            if not has_curated_text:
                revision["text"] = ""
                revision["source"] = SKIPPED_EFFORT_SOURCE
                revision.pop("model", None)
                skipped_effort_keys.add(key)
        elif _is_non_speech_recording(filename):
            source = revision.get("source")
            has_curated_text = source in {"manual", "official"} and bool(
                str(revision.get("text") or "").strip()
            )
            if not has_curated_text:
                revision["text"] = ""
                revision["source"] = SKIPPED_NON_SPEECH_SOURCE
                revision.pop("model", None)
                skipped_non_speech_keys.add(key)
        if (
            predefined_text is not None
            and not str(revision.get("text") or "").strip()
            and revision.get("source") != "manual"
        ):
            revision["text"] = predefined_text
            revision["source"] = "official"
            revision.pop("model", None)
            skipped_effort_keys.discard(key)
            skipped_non_speech_keys.discard(key)
            predefined_applied_keys.add(key)
        transcript_by_audio[key] = revision
        document_by_revision_id[id(revision)] = document
        current_transcript_keys.add(key)
        if (
            not str(revision.get("text") or "").strip()
            and revision.get("source") not in TERMINAL_BLANK_SOURCES
            and audio_path is not None
        ):
            missing_by_audio[key] = (revision, audio_path)
        content_record: dict[str, object] = {
            "lineId": _line_id(entry),
            "audioSha256": audio_hash,
            "filename": filename,
        }
        return revision, content_record

    for path, entry in _walk_voicelines(voicelines):
        if not path:
            continue
        speaker = path[0]
        filename = str(entry.get("filename") or "")
        audio_path = audio_index.resolve(filename)
        remember_audio(filename, audio_path)
        audio_hash = audio_index.hash(audio_path)
        _revision, content_record = resolve_transcript(entry, audio_path, audio_hash)
        voice_records.append((speaker, content_record, audio_path))

    conversation_records: list[tuple[str, dict[str, object], Path | None]] = []
    for conversation in conversations["conversations"]:
        if not isinstance(conversation, dict):
            continue
        conversation_id = str(conversation.get("conversation_id") or conversation.get("conversationId") or "")
        if not conversation_id:
            raise BaselineError("A conversation is missing conversation_id.")
        for source_line in conversation.get("lines", []):
            if not isinstance(source_line, dict):
                continue
            normalized_line = dict(source_line)
            if not _line_id(normalized_line):
                normalized_line["lineId"] = (
                    f"{conversation_id}-part-{normalized_line.get('part', 0)}-"
                    f"variation-{normalized_line.get('variation', 1)}-"
                    f"{normalized_line.get('speaker', 'unknown')}"
                )
            filename = str(normalized_line.get("filename") or "")
            audio_path = audio_index.resolve(filename)
            remember_audio(filename, audio_path)
            audio_hash = audio_index.hash(audio_path)
            _revision, content_record = resolve_transcript(
                normalized_line, audio_path, audio_hash
            )
            conversation_records.append(
                (str(source_line.get("speaker") or "unknown"), content_record, audio_path)
            )

    if predefined_catalog is not None:
        already_filled = len(predefined_matched_paths) - len(
            {filename for filename, _audio_hash in predefined_applied_keys}
        )
        unmatched = predefined_catalog.accepted_rows - len(predefined_matched_paths)
        progress(
            f"Applied {len(predefined_applied_keys)} predefined official transcripts; "
            f"{already_filled} matching recordings already had text; "
            f"{unmatched} accepted entries did not occur in this version."
        )

    if skipped_effort_keys:
        progress(
            f"Skipped transcription for {len(skipped_effort_keys)} effort recordings."
        )
    if skipped_non_speech_keys:
        progress(
            "Skipped transcription for "
            f"{len(skipped_non_speech_keys)} non-speech recordings."
        )

    # Reuse a transcript when the exact audio bytes already have one. If the
    # same hash has conflicting text, leave it unresolved rather than guessing.
    known_by_hash: dict[str, dict[str, object]] = {}
    conflicting_hashes: set[str] = set()
    for document in transcript_documents.values():
        for transcript in document["revisions"]:
            digest = transcript.get("sha256")
            text_value = str(transcript.get("text") or "").strip()
            if not isinstance(digest, str) or not text_value:
                continue
            previous = known_by_hash.get(digest)
            if previous and str(previous.get("text") or "").strip() != text_value:
                conflicting_hashes.add(digest)
            else:
                known_by_hash[digest] = transcript
    unresolved: list[tuple[dict[str, object], Path]] = []
    for transcript, path in missing_by_audio.values():
        digest = transcript.get("sha256")
        reusable = known_by_hash.get(str(digest)) if digest not in conflicting_hashes else None
        if reusable:
            transcript["text"] = reusable.get("text", "")
            transcript["source"] = reusable.get("source", "generated")
            if transcript["source"] == "generated" and reusable.get("model"):
                transcript["model"] = reusable["model"]
        else:
            unresolved.append((transcript, path))
    missing = unresolved

    if missing and settings.transcribe_missing:
        if not settings.api_key:
            raise BaselineError(
                f"{len(missing)} recordings need transcription, but no OpenAI API key is configured."
            )
        if settings.transcription_vocabulary is None:
            raise BaselineError(
                "Missing transcription vocabulary configuration for OpenAI transcription."
            )
        prompt_text = build_transcription_prompt(settings.transcription_vocabulary)
        progress(f"Transcribing {len(missing)} missing recordings with {settings.model}.")
        unique: dict[str, tuple[Path, list[dict[str, object]]]] = {}
        for entry, path in missing:
            digest = str(entry.get("sha256") or path.resolve())
            unique.setdefault(digest, (path, []))[1].append(entry)
        with ThreadPoolExecutor(max_workers=max(1, settings.workers)) as executor:
            futures = {
                executor.submit(
                    transcribe_audio, path, api_key=settings.api_key,
                    model=settings.model, prompt=prompt_text, progress=progress,
                ): (path, entries)
                for path, entries in unique.values()
            }
            completed = 0
            checkpointed_files = 0
            blank_results = 0
            for future in as_completed(futures):
                path, entries = futures[future]
                text = future.result()
                if not text:
                    blank_results += 1
                affected_documents: dict[int, dict[str, object]] = {}
                for entry in entries:
                    entry["text"] = text
                    if text:
                        entry["source"] = "generated"
                    else:
                        entry["source"] = SKIPPED_NON_SPEECH_SOURCE
                    entry["model"] = settings.model
                    document = document_by_revision_id[id(entry)]
                    affected_documents[id(document)] = document
                for document in affected_documents.values():
                    checkpointed_files += int(_write_transcript_document(repo, document))
                completed += 1
                if completed % 25 == 0 or completed == len(futures):
                    progress(
                        f"Transcribed {completed}/{len(futures)} unique recordings; "
                        f"checkpointed {checkpointed_files} transcript files; "
                        f"accepted {blank_results} blank non-speech results."
                    )
    # Persist exact-audio reuse and API results. Legacy files are removed only
    # after every migrated per-audio document has been written successfully.
    changed_transcript_files = _write_transcript_documents(
        repo, transcript_documents, existing_transcript_json
    )
    if changed_transcript_files:
        progress(f"Wrote {changed_transcript_files} changed transcript files.")
    if legacy_transcript_paths:
        _remove_migrated_legacy_files(repo, legacy_transcript_paths)
        progress(
            f"Migrated {len(legacy_transcript_paths)} legacy transcript files "
            "into unified per-audio JSON files."
        )

    def apply_text(node: object) -> object:
        if isinstance(node, dict):
            if isinstance(node.get("filename"), str):
                filename = _normalize_audio_key(node["filename"])
                audio_path = audio_index.resolve(filename)
                audio_hash = audio_index.hash(audio_path)
                key = (filename.casefold(), audio_hash)
                transcript = transcript_by_audio.get(key)
                result = dict(node)
                if audio_hash:
                    result["audioKey"] = _shared_audio_key(audio_hash)
                duration = audio_index.duration(audio_path)
                if duration is not None:
                    result["duration"] = duration
                else:
                    result.pop("duration", None)
                if transcript:
                    result["transcription"] = transcript.get("text", "")
                    result.pop("officialtranscription", None)
                    if transcript.get("source") == "official":
                        result["officialtranscription"] = True
                return result
            return {key: apply_text(value) for key, value in node.items()}
        if isinstance(node, list):
            return [apply_text(value) for value in node]
        return node

    generated_voicelines = apply_text(voicelines)
    generated_conversations = apply_text(conversations)
    characters = _collect_route_characters(voicelines, conversations)

    config_root = repo / "config" / settings.game
    character_names_path = config_root / "character-names.json"
    if not character_names_path.is_file():
        write_json(character_names_path, {
            "schemaVersion": 1,
            "game": settings.game,
            "names": {
                character: _default_character_display_name(character)
                for character in sorted(characters, key=str.casefold)
            },
        })
    character_names_payload = load_json(character_names_path)
    character_name_errors = validate_character_names(
        character_names_payload, settings.game
    )
    if character_name_errors:
        raise BaselineError("Invalid character names: " + " ".join(character_name_errors))

    default_categories = config_root / "categories.json"
    if not default_categories.is_file():
        write_json(default_categories, {
            "schemaVersion": 1,
            "defaultCategory": "Characters",
            "categories": [{"name": "Characters", "characters": []}],
        })
    override_categories = config_root / "versions" / settings.version_id / "categories.json"
    if not override_categories.is_file():
        source_categories = source / "categories.json"
        payload = load_json(source_categories) if source_categories.is_file() else load_json(default_categories)
        write_json(override_categories, payload)
    category_payload = load_json(override_categories)
    errors, warnings = validate_categories(category_payload, characters)
    for warning in warnings:
        progress(f"Category warning: {warning}")
    if errors:
        raise BaselineError("Invalid categories: " + " ".join(errors))

    preview_version_id = f"preview-{settings.version_id}"
    preview_root = data_dir / "preview-content"
    preview_root.mkdir(parents=True, exist_ok=True)
    game_root = preview_root / settings.game
    version_root = game_root / "versions" / preview_version_id
    game_root.mkdir(parents=True, exist_ok=True)
    _safe_replace_directory(version_root, preview_root)
    base_url = "http://127.0.0.1:8787"
    now = datetime.now(timezone.utc).isoformat()
    write_json(game_root / "categories.json", load_json(default_categories))
    write_json(game_root / "character-names.json", character_names_payload)
    route_characters = sorted(characters, key=lambda item: (item.casefold(), item))
    write_json(version_root / "categories.json", category_payload)
    write_json(version_root / "conversations.json", generated_conversations)
    write_json(version_root / "voicelines.json", generated_voicelines)

    coverage_source = source / "coverage.json"
    if coverage_source.is_file():
        _link_or_copy(coverage_source, version_root / "coverage.json")
    else:
        write_json(version_root / "coverage.json", {
            "summary": {
                "matched_in_voicelines": len(voice_records),
                "matched_in_conversations": len(conversation_records),
                "referenced_audio": len(referenced_audio_by_key),
            }
        })

    for source_name, target_name in (
        ("Localization", "localization"),
        ("FanLocalization", "fan-localization"),
    ):
        _copy_tree(source / source_name, version_root / target_name)
    _copy_tree(source / "IconPacks" / "default", version_root / "icons" / "default")

    referenced_audio = sorted(
        referenced_audio_by_key.values(), key=lambda item: item[0].casefold()
    )
    shared_audio_root = data_dir / "shared-audio" / settings.game
    if settings.include_audio:
        for _audio_key, path in referenced_audio:
            audio_hash = audio_index.hash(path)
            if not audio_hash:
                continue
            relative_shared_path = Path(*_shared_audio_key(audio_hash).split("/"))
            canonical_path = shared_audio_root / relative_shared_path
            _ensure_shared_audio(path, canonical_path, audio_hash)
            _ensure_shared_audio(
                canonical_path,
                game_root / "audio" / relative_shared_path,
                audio_hash,
            )

    # Produce the current-layout folder consumed by the existing production
    # publisher. Files are hard-linked when possible, so this does not create a
    # second multi-gigabyte audio copy.
    publish_source = data_dir / "generated" / settings.version_id
    _safe_replace_directory(publish_source, data_dir)
    write_json(publish_source / "all_conversations.json", generated_conversations)
    write_json(publish_source / "all_voicelines.json", generated_voicelines)
    write_json(publish_source / "categories.json", category_payload)
    write_json(publish_source / "character-names.json", character_names_payload)
    if coverage_source.is_file():
        _link_or_copy(coverage_source, publish_source / "coverage.json")
    else:
        _link_or_copy(version_root / "coverage.json", publish_source / "coverage.json")
    for source_name in ("Localization", "FanLocalization"):
        _copy_tree(source / source_name, publish_source / source_name)
    _copy_tree(source / "IconPacks" / "default", publish_source / "IconPacks" / "default")
    if settings.include_audio:
        for _audio_key, path in referenced_audio:
            audio_hash = audio_index.hash(path)
            if not audio_hash:
                continue
            relative_shared_path = Path(*_shared_audio_key(audio_hash).split("/"))
            canonical_path = shared_audio_root / relative_shared_path
            _ensure_shared_audio(
                canonical_path,
                publish_source / "SharedAudio" / relative_shared_path,
                audio_hash,
            )

    entry = {
        "id": preview_version_id,
        "label": f"Preview: {settings.label}",
        "publishedAt": now,
        "updatedAt": now,
        "contentRevision": 1,
        "hidden": False,
        "conversationUrl": f"{base_url}/{settings.game}/versions/{preview_version_id}/conversations.json",
        "voiceLineUrl": f"{base_url}/{settings.game}/versions/{preview_version_id}/voicelines.json",
        "audioBaseUrl": f"{base_url}/{settings.game}/versions/{preview_version_id}/audio/",
        "coverageUrl": f"{base_url}/{settings.game}/versions/{preview_version_id}/coverage.json",
        "categoriesUrl": f"{base_url}/{settings.game}/versions/{preview_version_id}/categories.json",
    }
    optional_urls = (
        (version_root / "localization" / "manifest.json", "localizationManifestUrl", "localization/manifest.json"),
        (version_root / "fan-localization" / "manifest.json", "fanLocalizationManifestUrl", "fan-localization/manifest.json"),
        (version_root / "icons" / "default" / "manifest.json", "iconOverridesUrl", "icons/default/manifest.json"),
    )
    for path, field, suffix in optional_urls:
        if path.is_file():
            entry[field] = f"{base_url}/{settings.game}/versions/{preview_version_id}/{suffix}"
    existing_manifest: dict[str, object] = {}
    manifest_path = game_root / "manifest.json"
    if manifest_path.is_file():
        loaded_manifest = load_json(manifest_path)
        if isinstance(loaded_manifest, dict):
            existing_manifest = loaded_manifest
    existing_entries = existing_manifest.get("versions", [])
    retained_entries = [
        value for value in existing_entries
        if isinstance(value, dict)
        and value.get("id") != preview_version_id
        and isinstance(value.get("id"), str)
        and (game_root / "versions" / str(value["id"])).is_dir()
    ] if isinstance(existing_entries, list) else []
    manifest_entries = [entry, *retained_entries]

    existing_characters: dict[str, object] = {}
    characters_path = game_root / "characters.json"
    if characters_path.is_file():
        loaded_characters = load_json(characters_path)
        if isinstance(loaded_characters, dict):
            existing_characters = loaded_characters
    old_version_characters = existing_characters.get("versions", {})
    version_characters = {
        str(value["id"]): list(old_version_characters.get(str(value["id"]), []))
        for value in retained_entries
        if isinstance(old_version_characters, dict)
        and isinstance(old_version_characters.get(str(value["id"])), list)
    }
    version_characters = {preview_version_id: route_characters, **version_characters}
    all_route_characters: dict[str, str] = {}
    for values in version_characters.values():
        for value in values:
            if isinstance(value, str) and value.strip():
                all_route_characters.setdefault(value.casefold(), value)
    write_json(characters_path, {
        "schemaVersion": 1,
        "game": settings.game,
        "updatedAt": now,
        "characters": sorted(all_route_characters.values(), key=lambda item: (item.casefold(), item)),
        "versions": version_characters,
    })

    write_json(game_root / "manifest.json", {
        "schemaVersion": 1,
        "game": settings.game,
        "latestVersion": preview_version_id,
        "defaultCategoriesUrl": f"{base_url}/{settings.game}/categories.json",
        "charactersUrl": f"{base_url}/{settings.game}/characters.json",
        "characterNamesUrl": f"{base_url}/{settings.game}/character-names.json",
        "sharedAudioBaseUrl": f"{base_url}/{settings.game}/audio/",
        "preview": True,
        "versions": manifest_entries,
    })

    database_path = data_dir / "historical-content.sqlite3"
    with closing(_open_database(database_path)) as database:
        database.execute(
            "INSERT OR REPLACE INTO versions(id, game, label, is_baseline, imported_at) VALUES(?, ?, ?, 1, ?)",
            (settings.version_id, settings.game, settings.label, now),
        )
        database.execute("DELETE FROM version_assets WHERE version_id = ?", (settings.version_id,))
        for kind, records in (("voiceline", voice_records), ("conversation", conversation_records)):
            for speaker, line, audio_path in records:
                database.execute(
                    "INSERT OR REPLACE INTO version_assets(version_id, kind, line_id, audio_sha256, filename, speaker) VALUES(?, ?, ?, ?, ?, ?)",
                    (
                        settings.version_id, kind, line.get("lineId"),
                        line.get("audioSha256"), line.get("filename", ""), speaker,
                    ),
                )
        database.commit()

    local_catalog = register_local_version(
        data_dir,
        settings.game,
        settings.version_id,
        settings.label,
    )
    rebuild_local_preview_manifest(data_dir, settings.game, local_catalog)
    recalculate_version_statuses(data_dir, settings.game, local_catalog, progress)

    remaining_missing = sum(
        1
        for key in current_transcript_keys
        if not str(transcript_by_audio[key].get("text") or "").strip()
    )
    progress(f"Preview content generated at {preview_root}")
    return BaselineResult(
        preview_root=preview_root,
        publish_source=publish_source,
        transcript_repo=repo,
        categories_path=override_categories,
        database_path=database_path,
        preview_version_id=preview_version_id,
        voiceline_count=len(voice_records),
        conversation_line_count=len(conversation_records),
        missing_transcripts=remaining_missing,
        audio_count=len(referenced_audio),
    )
