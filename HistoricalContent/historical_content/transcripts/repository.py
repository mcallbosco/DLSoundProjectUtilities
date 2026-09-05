"""Transcript revision authority, per-audio persistence, and legacy migration."""

from __future__ import annotations

import re
import subprocess
import unicodedata
from pathlib import Path, PurePosixPath

from ..errors import BaselineError
from ..generation.storage import normalize_audio_key
from ..json_io import load_json, serialize_json, write_json, write_json_if_changed
from ..transcription import DEFAULT_MODEL


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
TRANSCRIPT_SCHEMA_VERSION = 3
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
    "manual": 2,
    "official": 3,
}


def entry_text(entry: dict[str, object]) -> str:
    value = entry.get("transcription")
    if not isinstance(value, str):
        value = entry.get("text")
    return value.strip() if isinstance(value, str) else ""


def is_effort_recording(filename: str) -> bool:
    """Return whether an audio key identifies a non-verbal effort recording."""
    stem = PurePosixPath(filename.replace("\\", "/")).stem.casefold()
    return bool(re.search(r"(?:^|[_-])efforts?(?:[_-]|$)", stem))


def is_non_speech_recording(filename: str) -> bool:
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


def _transcript_path(repo: Path, filename: str) -> Path:
    """Return the readable transcript path for one normalized audio path."""
    normalized = normalize_audio_key(filename)
    if not normalized:
        raise BaselineError("A transcript cannot be stored without an audio filename.")
    # Keep the audio suffix so foo.mp3 and foo.wav cannot share one JSON path.
    relative = PurePosixPath(f"{normalized}.json")
    return repo / "transcripts" / Path(*relative.parts)


def _normalize_transcript_hashes(value: object, origin: Path) -> list[str]:
    if not isinstance(value, list):
        raise BaselineError(f"Transcript revision SHA-256 value must be an array in {origin}.")
    hashes: list[str] = []
    seen: set[str] = set()
    for candidate in value:
        if not isinstance(candidate, str) or not SHA256_RE.fullmatch(candidate.casefold()):
            raise BaselineError(f"Transcript revision has an invalid SHA-256 value in {origin}.")
        digest = candidate.casefold()
        if digest in seen:
            raise BaselineError(f"Transcript revision has a duplicate SHA-256 value in {origin}.")
        seen.add(digest)
        hashes.append(digest)
    return sorted(hashes)


def _transcript_match_key(text: str) -> str:
    """Return the deliberately broad key used to share equivalent subtitles."""
    return "".join(
        character
        for character in text.casefold()
        if not character.isspace()
        and not unicodedata.category(character).startswith("P")
    )


def _transcript_group_key(revision: dict[str, object]) -> tuple[str, str]:
    text = str(revision.get("text") or "")
    source = str(revision.get("source") or "")
    if not text.strip() and source in TERMINAL_BLANK_SOURCES:
        return "terminal-blank", source
    return "text", _transcript_match_key(text)


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
        "sha256": _normalize_transcript_hashes(value.get("sha256"), origin),
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


def revision_for_hash(
    document: dict[str, object], audio_hash: str | None
) -> dict[str, object] | None:
    for revision in document["revisions"]:
        if not isinstance(revision, dict):
            continue
        hashes = revision.get("sha256")
        if audio_hash is None and hashes == []:
            return revision
        if isinstance(audio_hash, str) and isinstance(hashes, list) and audio_hash in hashes:
            return revision
    return None


def _merge_loaded_revision(
    document: dict[str, object],
    candidate: dict[str, object],
    *,
    origin: Path,
) -> None:
    """Merge legacy or already-migrated data without losing human corrections."""
    candidate_hashes = candidate.get("sha256")
    if not isinstance(candidate_hashes, list):
        raise BaselineError(f"Transcript revision has invalid hashes in {origin}.")
    matches = {
        id(existing): existing
        for digest in candidate_hashes or [None]
        if (existing := revision_for_hash(document, digest)) is not None
    }
    if len(matches) > 1:
        raise BaselineError(
            f"Transcript hashes resolve to multiple revisions for {document['filename']!r} in {origin}."
        )
    existing = next(iter(matches.values()), None)
    if existing is None:
        document["revisions"].append(candidate)
        return
    existing_hashes = existing.get("sha256")
    if not isinstance(existing_hashes, list):
        raise BaselineError(f"Transcript revision has invalid hashes in {origin}.")
    merged_hashes = sorted(set(existing_hashes) | set(candidate_hashes))
    existing["sha256"] = merged_hashes
    existing_text = str(existing.get("text") or "").strip()
    candidate_text = str(candidate.get("text") or "").strip()
    if not existing_text and candidate_text:
        existing.clear()
        existing.update(candidate)
        existing["sha256"] = merged_hashes
        return
    if (
        not candidate_text
        or existing_text == candidate_text
        or _transcript_match_key(existing_text) == _transcript_match_key(candidate_text)
    ):
        if TRANSCRIPT_SOURCE_PRIORITY.get(
            str(candidate.get("source")), -1
        ) > TRANSCRIPT_SOURCE_PRIORITY.get(
            str(existing.get("source")), -1
        ):
            existing.clear()
            existing.update(candidate)
            existing["sha256"] = merged_hashes
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
        existing["sha256"] = merged_hashes
    elif (
        candidate_priority
        == existing_priority
        == TRANSCRIPT_SOURCE_PRIORITY["manual"]
    ):
        raise BaselineError(
            f"Conflicting manual transcripts exist for {document['filename']!r} "
            f"and SHA-256 {candidate_hashes!r}; conflict found in {origin}."
        )


def _compact_transcript_document(document: dict[str, object]) -> dict[str, object]:
    revisions = document.get("revisions")
    if not isinstance(revisions, list):
        raise BaselineError(f"Transcript has no revisions array: {document.get('filename')!r}.")
    grouped: dict[tuple[str, str], list[dict[str, object]]] = {}
    for revision in revisions:
        if not isinstance(revision, dict):
            raise BaselineError(f"Transcript revision must be an object: {document.get('filename')!r}.")
        key = _transcript_group_key(revision)
        grouped.setdefault(key, []).append(revision)

    compacted: list[dict[str, object]] = []
    for members in grouped.values():
        chosen_source = max(
            (str(member.get("source") or "") for member in members),
            key=lambda source: TRANSCRIPT_SOURCE_PRIORITY.get(source, -1),
        )
        candidates = [member for member in members if member.get("source") == chosen_source]
        text_counts: dict[str, int] = {}
        for member in candidates:
            text = str(member.get("text") or "")
            text_counts[text] = text_counts.get(text, 0) + 1
        chosen_text = max(text_counts, key=lambda text: text_counts[text])
        hashes = sorted(
            {
                digest
                for member in members
                for digest in member.get("sha256", [])
                if isinstance(digest, str)
            }
        )
        result: dict[str, object] = {
            "sha256": hashes,
            "text": chosen_text,
            "source": chosen_source,
        }
        if chosen_source in {"generated", SKIPPED_NON_SPEECH_SOURCE}:
            models = {
                str(member["model"])
                for member in candidates
                if isinstance(member.get("model"), str) and member["model"]
            }
            if len(models) == 1:
                result["model"] = next(iter(models))
        compacted.append(result)
    document["revisions"] = compacted
    document["schemaVersion"] = TRANSCRIPT_SCHEMA_VERSION
    return document


def get_document(
    documents: dict[str, dict[str, object]], filename: str
) -> dict[str, object]:
    normalized = normalize_audio_key(filename)
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


def load_documents(
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
            filename = normalize_audio_key(payload["filename"])
            expected_relative = f"{filename}.json"
            actual_relative = path.relative_to(transcript_root).as_posix()
            if actual_relative.casefold() != expected_relative.casefold():
                raise BaselineError(
                    f"Transcript file {path} must mirror its audio path at "
                    f"{_transcript_path(repo, filename)}."
                )
            existing_serialized[filename.casefold()] = serialize_json(payload)
            document = get_document(documents, filename)
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
                filename = normalize_audio_key(str(line.get("filename") or ""))
                if not filename:
                    raise BaselineError(f"Legacy transcript line has no filename in {path}.")
                source = line.get("source", "generated")
                revision_value: dict[str, object] = {
                    "sha256": [line["audioSha256"]] if line.get("audioSha256") else [],
                    "text": line.get("text", ""),
                    "source": source,
                }
                if source == "generated" and line.get("model"):
                    revision_value["model"] = line["model"]
                document = get_document(documents, filename)
                _merge_loaded_revision(
                    document,
                    _normalize_transcript_revision(revision_value, path),
                    origin=path,
                )
    return documents, legacy_paths, existing_serialized


def new_revision(
    entry: dict[str, object], audio_hash: str | None
) -> dict[str, object]:
    result: dict[str, object] = {
        "sha256": [audio_hash] if audio_hash is not None else [],
        "text": entry_text(entry),
    }
    if entry.get("officialtranscription"):
        result["source"] = "official"
    elif is_effort_recording(str(entry.get("filename") or "")):
        result["text"] = ""
        result["source"] = SKIPPED_EFFORT_SOURCE
    elif is_non_speech_recording(str(entry.get("filename") or "")):
        result["text"] = ""
        result["source"] = SKIPPED_NON_SPEECH_SOURCE
    else:
        result["source"] = "generated"
        result["model"] = DEFAULT_MODEL
    return result


def write_documents(
    repo: Path,
    documents: dict[str, dict[str, object]],
    existing_serialized: dict[str, str] | None = None,
) -> int:
    changed = 0
    existing_serialized = existing_serialized or {}
    for document in sorted(
        documents.values(), key=lambda value: str(value["filename"]).casefold()
    ):
        _compact_transcript_document(document)
        filename = str(document["filename"])
        if existing_serialized.get(filename.casefold()) == serialize_json(document):
            continue
        changed += write_json_if_changed(_transcript_path(repo, filename), document)
    return changed


def checkpoint_document(
    repo: Path,
    document: dict[str, object],
) -> bool:
    """Atomically checkpoint one per-audio transcript document."""
    return write_json_if_changed(
        _transcript_path(repo, str(document["filename"])),
        document,
    )


def remove_migrated_legacy_files(repo: Path, legacy_paths: list[Path]) -> None:
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


def initialize_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    if not (repo / ".git").exists():
        try:
            subprocess.run(["git", "init", str(repo)], check=True, capture_output=True, text=True)
        except (OSError, subprocess.CalledProcessError) as exc:
            raise BaselineError(f"Could not initialize transcript Git repository: {exc}") from exc


def write_repo_support(repo: Path) -> None:
    readme = repo / "README.md"
    if not readme.exists():
        readme.write_text(
            "# Deadlock Transcripts\n\n"
            "Human-readable transcript and content configuration used to generate VLViewer data.\n\n"
            "Audio transcripts are stored below `transcripts/` at paths that mirror the audio files.\n"
            "Each JSON file groups equivalent subtitle text and lists every matching audio SHA-256 value.\n\n"
            "Edit a group's `text`, set `source` to `manual`, remove `model`, preview locally, then commit.\n",
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
                            "type": "array",
                            "uniqueItems": True,
                            "items": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
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

