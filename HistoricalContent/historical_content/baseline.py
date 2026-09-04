"""Create a baseline transcript repository and local CDN preview from an export."""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

from .errors import BaselineError
from .generation.storage import (
    AudioIndex,
    copy_tree,
    ensure_shared_audio,
    find_audio_root,
    link_or_copy,
    normalize_audio_key,
    replace_directory,
    sha256_file,
    shared_audio_key,
    write_version_index,
)
from .json_io import load_json, write_json
from .transcripts.repository import (
    SKIPPED_EFFORT_SOURCE,
    SKIPPED_NON_SPEECH_SOURCE,
    TERMINAL_BLANK_SOURCES,
    checkpoint_document,
    entry_text,
    get_document,
    initialize_repo,
    is_effort_recording,
    is_non_speech_recording,
    load_documents,
    new_revision,
    remove_migrated_legacy_files,
    revision_for_hash,
    write_documents,
    write_repo_support,
)
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


def _find_source_file(root: Path, names: Iterable[str]) -> Path:
    for name in names:
        candidate = root / name
        if candidate.is_file():
            return candidate
    raise BaselineError(f"None of these required files exist in {root}: {', '.join(names)}")


def _line_id(entry: dict[str, object]) -> str:
    value = entry.get("voiceline_id") or entry.get("lineId")
    if isinstance(value, str) and value.strip():
        return value.strip()
    filename = str(entry.get("filename") or "")
    return Path(filename).stem


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
    audio_root = find_audio_root(source)
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
        initialize_repo(repo)
    else:
        repo.mkdir(parents=True, exist_ok=True)
    write_repo_support(repo)
    audio_index = AudioIndex(audio_root)
    progress(f"Indexed {sum(len(value) for value in audio_index.by_name.values())} audio files.")

    (
        transcript_documents,
        legacy_transcript_paths,
        existing_transcript_json,
    ) = load_documents(repo)
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
        key = normalize_audio_key(filename)
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
        filename = normalize_audio_key(str(entry.get("filename") or ""))
        document = get_document(transcript_documents, filename)
        revision = revision_for_hash(document, audio_hash)
        if revision is None:
            revision = new_revision(entry, audio_hash)
            document["revisions"].append(revision)
        key = (filename.casefold(), audio_hash)
        predefined_text = (
            predefined_catalog.transcripts.get(filename.casefold())
            if predefined_catalog is not None
            else None
        )
        if predefined_text is not None:
            predefined_matched_paths.add(filename.casefold())
        official_text = entry_text(entry).strip() if entry.get("officialtranscription") else ""
        if official_text:
            # VDF text is authoritative for a real matched recording. Promote
            # any existing revision in place so the transcript repository and
            # published line no longer disagree. This intentionally supersedes
            # manual text once Valve supplies an official transcription.
            revision["text"] = official_text
            revision["source"] = "official"
            revision.pop("model", None)
            skipped_effort_keys.discard(key)
            skipped_non_speech_keys.discard(key)
        if is_effort_recording(filename):
            source = revision.get("source")
            has_curated_revision = source == "manual" or (
                source == "official"
                and bool(str(revision.get("text") or "").strip())
            )
            if not has_curated_revision:
                revision["text"] = ""
                revision["source"] = SKIPPED_EFFORT_SOURCE
                revision.pop("model", None)
                skipped_effort_keys.add(key)
        elif is_non_speech_recording(filename):
            source = revision.get("source")
            has_curated_revision = source == "manual" or (
                source == "official"
                and bool(str(revision.get("text") or "").strip())
            )
            if not has_curated_revision:
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
            and revision.get("source") != "manual"
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
        filename = normalize_audio_key(str(entry.get("filename") or ""))
        if not filename:
            if entry.get("is_phantom") is True:
                continue
            identifier = str(entry.get("voiceline_id") or _line_id(entry) or "unknown")
            raise BaselineError(
                f"Voiceline {identifier!r} has no audio filename and is not marked "
                "as a phantom line."
            )
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
            filename = normalize_audio_key(
                str(normalized_line.get("filename") or "")
            )
            if not filename:
                if normalized_line.get("is_phantom") is True:
                    continue
                raise BaselineError(
                    f"Conversation {conversation_id!r} has a line without an audio "
                    "filename that is not marked as a phantom line."
                )
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
            text_value = str(transcript.get("text") or "").strip()
            hashes = transcript.get("sha256")
            if not isinstance(hashes, list) or not text_value:
                continue
            for digest in hashes:
                if not isinstance(digest, str):
                    continue
                previous = known_by_hash.get(digest)
                if previous and str(previous.get("text") or "").strip() != text_value:
                    conflicting_hashes.add(digest)
                else:
                    known_by_hash[digest] = transcript
    unresolved: list[tuple[dict[str, object], Path]] = []
    for transcript, path in missing_by_audio.values():
        hashes = transcript.get("sha256")
        digest = hashes[0] if isinstance(hashes, list) and hashes else None
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
            hashes = entry.get("sha256")
            digest = str(hashes[0] if isinstance(hashes, list) and hashes else path.resolve())
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
                    checkpointed_files += int(checkpoint_document(repo, document))
                completed += 1
                if completed % 25 == 0 or completed == len(futures):
                    progress(
                        f"Transcribed {completed}/{len(futures)} unique recordings; "
                        f"checkpointed {checkpointed_files} transcript files; "
                        f"accepted {blank_results} blank non-speech results."
                    )
    # Persist exact-audio reuse and API results. Legacy files are removed only
    # after every migrated per-audio document has been written successfully.
    changed_transcript_files = write_documents(
        repo, transcript_documents, existing_transcript_json
    )
    if changed_transcript_files:
        progress(f"Wrote {changed_transcript_files} changed transcript files.")
    if legacy_transcript_paths:
        remove_migrated_legacy_files(repo, legacy_transcript_paths)
        progress(
            f"Migrated {len(legacy_transcript_paths)} legacy transcript files "
            "into unified per-audio JSON files."
        )

    def apply_text(node: object) -> object:
        if isinstance(node, dict):
            if isinstance(node.get("filename"), str):
                filename = normalize_audio_key(node["filename"])
                if not filename:
                    result = dict(node)
                    result.pop("audioKey", None)
                    result.pop("duration", None)
                    return result
                audio_path = audio_index.resolve(filename)
                audio_hash = audio_index.hash(audio_path)
                key = (filename.casefold(), audio_hash)
                transcript = transcript_by_audio.get(key)
                result = dict(node)
                if audio_hash:
                    result["audioKey"] = shared_audio_key(audio_hash)
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

    version_character_names_path = (
        config_root / "versions" / settings.version_id / "character-names.json"
    )
    version_character_names_payload: object | None = None
    if version_character_names_path.is_file():
        version_character_names_payload = load_json(version_character_names_path)
        version_character_name_errors = validate_character_names(
            version_character_names_payload,
            settings.game,
        )
        if version_character_name_errors:
            raise BaselineError(
                "Invalid version character names: "
                + " ".join(version_character_name_errors)
            )

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
    replace_directory(version_root, preview_root)
    base_url = "http://127.0.0.1:8787"
    now = datetime.now(timezone.utc).isoformat()
    write_json(game_root / "categories.json", load_json(default_categories))
    write_json(game_root / "character-names.json", character_names_payload)
    route_characters = sorted(characters, key=lambda item: (item.casefold(), item))
    write_json(version_root / "categories.json", category_payload)
    if version_character_names_payload is not None:
        write_json(
            version_root / "character-names.json",
            version_character_names_payload,
        )
    write_json(version_root / "conversations.json", generated_conversations)
    write_json(version_root / "voicelines.json", generated_voicelines)

    coverage_source = source / "coverage.json"
    if coverage_source.is_file():
        link_or_copy(coverage_source, version_root / "coverage.json")
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
        ("CharacterNameImages", "character-name-images"),
        ("CharacterSelectBackgrounds", "character-select-backgrounds"),
    ):
        copy_tree(source / source_name, version_root / target_name)
    copy_tree(source / "IconPacks" / "default", version_root / "icons" / "default")

    referenced_audio = sorted(
        referenced_audio_by_key.values(), key=lambda item: item[0].casefold()
    )
    shared_audio_root = data_dir / "shared-audio" / settings.game
    if settings.include_audio:
        for _audio_key, path in referenced_audio:
            audio_hash = audio_index.hash(path)
            if not audio_hash:
                continue
            relative_shared_path = Path(*shared_audio_key(audio_hash).split("/"))
            canonical_path = shared_audio_root / relative_shared_path
            ensure_shared_audio(path, canonical_path, audio_hash)
            ensure_shared_audio(
                canonical_path,
                game_root / "audio" / relative_shared_path,
                audio_hash,
            )

    # Produce the current-layout folder consumed by the existing production
    # publisher. Files are hard-linked when possible, so this does not create a
    # second multi-gigabyte audio copy.
    publish_source = data_dir / "generated" / settings.version_id
    replace_directory(publish_source, data_dir)
    write_json(publish_source / "all_conversations.json", generated_conversations)
    write_json(publish_source / "all_voicelines.json", generated_voicelines)
    write_json(publish_source / "categories.json", category_payload)
    write_json(publish_source / "character-names.json", character_names_payload)
    if version_character_names_payload is not None:
        write_json(
            publish_source / "character-names-overlay.json",
            version_character_names_payload,
        )
    if coverage_source.is_file():
        link_or_copy(coverage_source, publish_source / "coverage.json")
    else:
        link_or_copy(version_root / "coverage.json", publish_source / "coverage.json")
    for source_name in (
        "Localization",
        "FanLocalization",
        "CharacterNameImages",
        "CharacterSelectBackgrounds",
    ):
        copy_tree(source / source_name, publish_source / source_name)
    copy_tree(source / "IconPacks" / "default", publish_source / "IconPacks" / "default")
    if settings.include_audio:
        for _audio_key, path in referenced_audio:
            audio_hash = audio_index.hash(path)
            if not audio_hash:
                continue
            relative_shared_path = Path(*shared_audio_key(audio_hash).split("/"))
            canonical_path = shared_audio_root / relative_shared_path
            ensure_shared_audio(
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
        (
            version_root / "character-names.json",
            "characterNamesUrl",
            "character-names.json",
        ),
        (version_root / "localization" / "manifest.json", "localizationManifestUrl", "localization/manifest.json"),
        (version_root / "fan-localization" / "manifest.json", "fanLocalizationManifestUrl", "fan-localization/manifest.json"),
        (version_root / "icons" / "default" / "manifest.json", "iconOverridesUrl", "icons/default/manifest.json"),
        (
            version_root / "character-name-images" / "manifest.json",
            "characterNameImagesUrl",
            "character-name-images/manifest.json",
        ),
        (
            version_root / "character-select-backgrounds" / "manifest.json",
            "characterSelectBackgroundsUrl",
            "character-select-backgrounds/manifest.json",
        ),
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
    write_version_index(
        database_path,
        version_id=settings.version_id,
        game=settings.game,
        label=settings.label,
        imported_at=now,
        records_by_kind=(("voiceline", voice_records), ("conversation", conversation_records)),
    )

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
