"""Build a custom, mod-audio-only content version with pinned embedded text.

This importer deliberately contains no speech-to-text integration. Audio must
correlate to a base-version record, but a missing pinned VDF entry is retained
with an empty embedded transcript and reported as a non-blocking warning.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import shutil
import subprocess
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit, urlunsplit

from .json_io import write_json

from .errors import VpkPipelineError
from .extraction.source2viewer import extract_vpk_voice_audio
from .version_catalog import (
    load_cataloged_local_versions,
    rebuild_local_preview_manifest,
    recalculate_version_statuses,
    register_local_version,
)

Progress = Callable[[str], None]
AUDIO_SUFFIXES = {".mp3"}
IDENTIFIER_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
KNOWN_VDF_SUFFIXES = tuple(sorted((
    "_announcer_tutorial",
    "_hero_zipline_3d",
    "_hero_announcer",
    "_ability_3d",
    "_ping_2d",
    "_shopkeeper",
    "_announcer",
    "_hero_3d",
    "_ult_3d",
    "_hero",
    "_idol",
), key=lambda value: (-len(value), value)))
QUOTED_KV_RE = re.compile(r'^"((?:\\.|[^"\\])*)"\s+"((?:\\.|[^"\\])*)"\s*$')


class CustomVoiceModError(RuntimeError):
    """Safe, operator-facing custom voice-mod import failure."""


@dataclass(frozen=True)
class CustomVoiceModSettings:
    data_dir: Path
    game: str
    version_id: str
    label: str
    based_on_version: str
    source2viewer_binary: Path
    mod_vpk_path: Path
    transcript_path: Path
    transcript_metadata_path: Path | None = None
    transcript_repository: str = ""
    transcript_revision: str = ""
    transcript_source_path: str = ""
    expected_transcript_sha256: str = ""
    correlation_overrides_path: Path | None = None
    default_localization_language: str = "russian"
    embedded_transcript_language: str = "russian"
    extraction_threads: int = 8
    force_reextract: bool = False
    replace_existing_local: bool = True

    @property
    def base_source(self) -> Path:
        return self.data_dir.expanduser().resolve() / "generated" / self.based_on_version

    @property
    def output_dir(self) -> Path:
        return self.data_dir.expanduser().resolve() / "generated" / self.version_id

    @property
    def extraction_workspace(self) -> Path:
        return (
            self.data_dir.expanduser().resolve()
            / "workspaces"
            / self.game
            / self.version_id
            / "custom-voice-mod-vpk"
        )


@dataclass(frozen=True)
class TranscriptProvenance:
    """Immutable transcript inputs discovered from its Git checkout."""

    metadata_path: Path
    repository: str
    revision: str
    source_path: str
    sha256: str


@dataclass(frozen=True)
class CustomVoiceModResult:
    output_dir: Path
    extraction_workspace: Path
    preview_version_id: str
    audio_files: int
    voiceline_records: int
    conversation_records: int
    warnings: tuple[dict[str, str], ...]

    @property
    def publishable(self) -> bool:
        # A completed import is safe to publish. Recoverable correlation
        # warnings are preserved in the report, while unsafe inputs fail the
        # import before a result is produced.
        return True


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        raise CustomVoiceModError(f"Invalid JSON in {path}: {exc}") from exc


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_text(repository: Path, *arguments: str) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(repository), *arguments],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError as exc:
        raise CustomVoiceModError(f"Could not run Git while inspecting the transcript: {exc}") from exc
    if completed.returncode:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise CustomVoiceModError(
            f"Could not inspect transcript Git provenance with `git {' '.join(arguments)}`"
            + (f": {detail}" if detail else ".")
        )
    return completed.stdout.strip()


def _public_repository_url(value: str) -> str:
    url = value.strip()
    ssh_match = re.fullmatch(r"git@github\.com:(.+?)(?:\.git)?", url)
    if ssh_match:
        return f"https://github.com/{ssh_match.group(1).removesuffix('.git')}"
    ssh_url_match = re.fullmatch(r"ssh://git@github\.com/(.+?)(?:\.git)?", url)
    if ssh_url_match:
        return f"https://github.com/{ssh_url_match.group(1).removesuffix('.git')}"
    parsed = urlsplit(url)
    if parsed.scheme.casefold() != "https" or not parsed.hostname:
        raise CustomVoiceModError(
            "Transcript Git origin must be a public HTTPS URL or GitHub SSH URL."
        )
    hostname = parsed.hostname.casefold().rstrip(".")
    if hostname == "localhost" or hostname.endswith((".localhost", ".local")):
        raise CustomVoiceModError("Transcript Git origin must not reference a local host.")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        if not address.is_global:
            raise CustomVoiceModError("Transcript Git origin must use a public host.")
    try:
        port = parsed.port
    except ValueError as exc:
        raise CustomVoiceModError("Transcript Git origin contains an invalid port.") from exc
    netloc = hostname if port is None else f"{hostname}:{port}"
    public_url = urlunsplit(("https", netloc, parsed.path, "", "")).removesuffix(".git")
    if not parsed.path.strip("/"):
        raise CustomVoiceModError("Transcript Git origin must identify a repository path.")
    return public_url


def _verify_committed_file(
    repository: Path,
    revision: str,
    source_path: str,
    local_path: Path,
) -> str:
    _git_text(repository, "ls-files", "--error-unmatch", "--", source_path)
    completed = subprocess.run(
        ["git", "-C", str(repository), "diff", "--quiet", "HEAD", "--", source_path],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode == 1:
        raise CustomVoiceModError(
            f"Pinned source has uncommitted changes; commit or restore it first: {local_path}"
        )
    if completed.returncode:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise CustomVoiceModError(
            f"Could not verify the pinned source against Git: {local_path}"
            + (f" ({detail})" if detail else "")
        )
    committed_oid = _git_text(repository, "rev-parse", f"{revision}:{source_path}")
    checkout_oid = _git_text(
        repository,
        "hash-object",
        f"--path={source_path}",
        str(local_path),
    )
    local_hash = _sha256(local_path)
    if checkout_oid != committed_oid:
        raise CustomVoiceModError(
            f"Pinned source content differs from commit {revision}: {local_path}"
        )
    return local_hash


def discover_transcript_provenance(transcript_path: Path) -> TranscriptProvenance:
    """Derive metadata, repository, revision, path, and hash from a clean Git checkout."""
    transcript = transcript_path.expanduser().resolve()
    if not transcript.is_file():
        raise CustomVoiceModError(f"Pinned transcript source does not exist: {transcript}")
    repository_text = _git_text(transcript.parent, "rev-parse", "--show-toplevel")
    repository = Path(repository_text).resolve()
    try:
        source_path = transcript.relative_to(repository).as_posix()
    except ValueError as exc:
        raise CustomVoiceModError(
            f"Pinned transcript is not inside its detected Git repository: {transcript}"
        ) from exc
    metadata_path = transcript.parent / "metadata.json"
    if not metadata_path.is_file():
        raise CustomVoiceModError(
            f"Expected transcript metadata beside the selected file: {metadata_path}"
        )
    metadata_source_path = metadata_path.relative_to(repository).as_posix()
    revision = _git_text(repository, "rev-parse", "HEAD")
    origin = _public_repository_url(_git_text(repository, "remote", "get-url", "origin"))
    transcript_hash = _verify_committed_file(
        repository, revision, source_path, transcript,
    )
    _verify_committed_file(
        repository, revision, metadata_source_path, metadata_path,
    )
    return TranscriptProvenance(
        metadata_path=metadata_path,
        repository=origin,
        revision=revision,
        source_path=source_path,
        sha256=transcript_hash,
    )


def _safe_relative_path(value: str) -> str:
    normalized = value.strip().replace("\\", "/")
    path = PurePosixPath(normalized)
    if (
        not normalized
        or path.is_absolute()
        or any(part in {"", ".", ".."} or ":" in part for part in path.parts)
    ):
        raise CustomVoiceModError(f"Unsafe relative audio path: {value!r}")
    return path.as_posix()


def _normalized_path(value: str) -> str:
    return _safe_relative_path(value).casefold()


def _collapsed_repeated_stem_path(value: str) -> str | None:
    """Return an alias for a filename whose stem was accidentally doubled."""
    path = PurePosixPath(_safe_relative_path(value))
    stem = path.stem
    if len(stem) % 2:
        return None
    midpoint = len(stem) // 2
    if stem[:midpoint].casefold() != stem[midpoint:].casefold():
        return None
    collapsed = path.with_name(stem[:midpoint] + path.suffix)
    return collapsed.as_posix().casefold()


def _decode_vdf(path: Path) -> str:
    raw = path.read_bytes()
    if raw.startswith(b"\xff\xfe"):
        return raw[2:].decode("utf-16le")
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8")
    return raw.decode("utf-8-sig")


def parse_vdf_tokens(path: Path) -> dict[str, str]:
    tokens: dict[str, str] = {}
    for raw_line in _decode_vdf(path).splitlines():
        match = QUOTED_KV_RE.fullmatch(raw_line.strip())
        if not match:
            continue
        key = match.group(1).replace(r'\"', '"').strip().casefold()
        value = match.group(2).replace(r'\"', '"').strip()
        if key:
            # Preserve the top-most definition when a VDF repeats a token.
            tokens.setdefault(key, value)
    return tokens


def _walk_records(value: object) -> Iterable[dict[str, object]]:
    if isinstance(value, dict):
        if isinstance(value.get("filename"), str):
            yield value
            return
        for child in value.values():
            yield from _walk_records(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_records(child)


def _load_overrides(path: Path | None) -> dict[str, dict[str, str]]:
    if path is None:
        return {}
    payload = _read_json(path)
    if not isinstance(payload, dict) or payload.get("schemaVersion") != 1:
        raise CustomVoiceModError("Correlation overrides must be a schemaVersion 1 object.")
    raw_overrides = payload.get("overrides")
    if not isinstance(raw_overrides, dict):
        raise CustomVoiceModError("Correlation overrides must contain an overrides object.")
    overrides: dict[str, dict[str, str]] = {}
    for source, raw_rule in raw_overrides.items():
        if not isinstance(source, str) or not isinstance(raw_rule, dict):
            raise CustomVoiceModError("Every correlation override must map a path to an object.")
        base_filename = raw_rule.get("baseFilename")
        transcript_key = raw_rule.get("transcriptKey")
        if not isinstance(base_filename, str) or not base_filename.strip():
            raise CustomVoiceModError(f"Override {source!r} needs baseFilename.")
        rule = {"baseFilename": _safe_relative_path(base_filename)}
        if isinstance(transcript_key, str) and transcript_key.strip():
            rule["transcriptKey"] = transcript_key.strip().casefold()
        overrides[_normalized_path(source)] = rule
    return overrides


def _resolve_transcript(
    record: dict[str, object],
    tokens: dict[str, str],
    explicit_key: str | None,
) -> tuple[str | None, str, str]:
    if explicit_key:
        text = tokens.get(explicit_key)
        return (text, "override", explicit_key) if text else (None, "missing", explicit_key)

    candidates: list[tuple[str, str]] = []
    voiceline_id = record.get("voiceline_id")
    if isinstance(voiceline_id, str) and voiceline_id.strip():
        candidates.append(("exact", voiceline_id.strip().casefold()))
    filename = str(record.get("filename") or "")
    if filename:
        stem = PurePosixPath(filename.replace("\\", "/")).stem.casefold()
        if stem and all(key != stem for _stage, key in candidates):
            candidates.append(("filename", stem))

    exact = [(stage, key, tokens[key]) for stage, key in candidates if tokens.get(key)]
    if exact:
        unique_text = {text for _stage, _key, text in exact}
        if len(unique_text) == 1:
            return exact[0][2], exact[0][0], exact[0][1]
        # Candidate order is intentional: voiceline_id precedes filename.
        # Keep that top candidate and surface the choice as a warning.
        return exact[0][2], "ambiguous-first", exact[0][1]

    suffix_matches: list[tuple[str, str]] = []
    for _stage, candidate in candidates:
        for suffix in KNOWN_VDF_SUFFIXES:
            key = f"{candidate}{suffix}"
            if tokens.get(key):
                suffix_matches.append((key, tokens[key]))
    unique = list(dict.fromkeys(suffix_matches))
    if len({text for _key, text in unique}) == 1 and unique:
        key, text = unique[0]
        return text, "suffix", key
    if unique:
        key, text = unique[0]
        return text, "ambiguous-first", key
    return None, "missing", ",".join(key for _stage, key in candidates)


def _filter_tree(value: object, embedded_by_record: dict[int, str]) -> object | None:
    if isinstance(value, dict):
        if isinstance(value.get("filename"), str):
            text = embedded_by_record.get(id(value))
            if text is None:
                return None
            result = dict(value)
            result["transcription"] = text
            result.pop("transcript", None)
            result["officialtranscription"] = False
            result.pop("status", None)
            result.pop("audioKey", None)
            result.pop("audioUrl", None)
            if "versionStatus" in result:
                result["versionStatus"] = {}
            return result
        result_dict: dict[str, object] = {}
        for key, child in value.items():
            filtered = _filter_tree(child, embedded_by_record)
            if filtered is not None and filtered != {} and filtered != []:
                result_dict[key] = filtered
        return result_dict
    if isinstance(value, list):
        result_list = []
        for child in value:
            filtered = _filter_tree(child, embedded_by_record)
            if filtered is not None and filtered != {} and filtered != []:
                result_list.append(filtered)
        return result_list
    return value


def _filter_conversations(
    payload: object,
    embedded_by_record: dict[int, str],
) -> tuple[dict[str, object], int]:
    if not isinstance(payload, dict) or not isinstance(payload.get("conversations"), list):
        raise CustomVoiceModError("Base all_conversations.json needs a conversations array.")
    conversations = []
    line_count = 0
    for raw in payload["conversations"]:
        if not isinstance(raw, dict) or not isinstance(raw.get("lines"), list):
            continue
        lines = [
            filtered
            for line in raw["lines"]
            if (filtered := _filter_tree(line, embedded_by_record)) is not None
        ]
        if not lines:
            continue
        conversation = dict(raw)
        conversation["lines"] = lines
        conversation.pop("status", None)
        conversation.pop("versionStatus", None)
        retained_parts = {
            line.get("part")
            for line in lines
            if isinstance(line, dict) and line.get("part") is not None
        }
        missing_parts: list[object] = []
        raw_missing = raw.get("missing_parts")
        if isinstance(raw_missing, list):
            for part in raw_missing:
                if part not in missing_parts:
                    missing_parts.append(part)
        for line in raw["lines"]:
            part = line.get("part") if isinstance(line, dict) else None
            if part is not None and part not in retained_parts and part not in missing_parts:
                missing_parts.append(part)
        conversation["missing_parts"] = missing_parts
        conversation["is_complete"] = (
            raw.get("is_complete") is not False
            and not missing_parts
            and len(lines) == len(raw["lines"])
        )
        # The official generator derives the full participant pair from the
        # conversation filename. Preserve that base metadata even when this
        # mod contains audio for only one side of the conversation.
        speakers = [
            speaker
            for speaker in raw.get("speakers", [])
            if isinstance(speaker, str) and speaker.strip()
        ] if isinstance(raw.get("speakers"), list) else []
        if not speakers:
            for line in lines:
                speaker = line.get("speaker") if isinstance(line, dict) else None
                if isinstance(speaker, str) and speaker not in speakers:
                    speakers.append(speaker)
        conversation["speakers"] = speakers
        conversations.append(conversation)
        line_count += len(lines)
    result = dict(payload)
    result["conversations"] = conversations
    result["total_conversations"] = len(conversations)
    return result, line_count


def _copy_tree(source: Path, destination: Path) -> None:
    if source.is_dir():
        shutil.copytree(source, destination, dirs_exist_ok=True)


def _replace_local_directory(
    staging: Path,
    destination: Path,
    allowed_parent: Path,
) -> Path | None:
    """Replace one directory while retaining its backup until the import commits."""
    resolved_staging = staging.resolve()
    resolved_destination = destination.resolve()
    resolved_parent = allowed_parent.resolve()
    if (
        resolved_staging.parent != resolved_parent
        or resolved_destination.parent != resolved_parent
        or resolved_staging == resolved_destination
    ):
        raise CustomVoiceModError(
            f"Refusing to replace a directory outside {resolved_parent}: {resolved_destination}"
        )
    backup = destination.with_name(destination.name + ".custom-import.backup")
    if backup.exists():
        raise CustomVoiceModError(
            f"A recovery backup from an interrupted import already exists: {backup}. "
            "Restore or remove it before retrying."
        )
    had_destination = destination.exists()
    if destination.exists():
        os.replace(destination, backup)
    try:
        os.replace(staging, destination)
    except Exception:
        if not destination.exists() and backup.exists():
            os.replace(backup, destination)
        raise
    return backup if had_destination else None


def _validate_existing_custom_output(settings: CustomVoiceModSettings) -> None:
    output = settings.output_dir
    backup = output.with_name(output.name + ".custom-import.backup")
    if backup.exists():
        raise CustomVoiceModError(
            f"A recovery backup from an interrupted import exists: {backup}. "
            "Restore or remove it before retrying."
        )
    if not output.exists():
        return
    if not settings.replace_existing_local:
        raise CustomVoiceModError(
            f"Custom output already exists and local replacement is disabled: {output}"
        )
    metadata_path = output / "custom-version.json"
    metadata = _read_json(metadata_path) if metadata_path.is_file() else None
    if not isinstance(metadata, dict) or metadata.get("kind") != "custom":
        raise CustomVoiceModError(
            f"Refusing to replace an existing directory that is not a generated custom version: "
            f"{output}"
        )


def _copy_supporting_resources(base_source: Path, output: Path) -> None:
    for filename in ("categories.json", "character-names.json", "character-names-overlay.json"):
        source = base_source / filename
        if source.is_file():
            shutil.copy2(source, output / filename)
    for folder in ("Localization", "CharacterNameImages"):
        _copy_tree(base_source / folder, output / folder)
    _copy_tree(base_source / "IconPacks" / "default", output / "IconPacks" / "default")


def _transcript_attribution(path: Path | None) -> dict[str, object]:
    if path is None:
        return {}
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise CustomVoiceModError(f"Transcript metadata does not exist: {resolved}")
    payload = _read_json(resolved)
    if not isinstance(payload, dict):
        raise CustomVoiceModError("Transcript metadata must contain a JSON object.")
    allowed = (
        "language", "version", "base_language", "friendly_name", "native_name",
        "credits", "tooltip_text",
    )
    return {key: payload[key] for key in allowed if key in payload}


def _route_characters(voicelines: object, conversations: dict[str, object]) -> list[str]:
    names: dict[str, str] = {}
    if isinstance(voicelines, dict):
        for speaker, targets in voicelines.items():
            if isinstance(speaker, str):
                names.setdefault(speaker.casefold(), speaker)
            if isinstance(targets, dict):
                for target in targets:
                    if isinstance(target, str) and target.casefold() != "self":
                        names.setdefault(target.casefold(), target)
    for conversation in conversations.get("conversations", []):
        if not isinstance(conversation, dict):
            continue
        for field in ("character1", "character2"):
            value = conversation.get(field)
            if isinstance(value, str) and value.strip():
                names.setdefault(value.casefold(), value)
    return sorted(names.values(), key=lambda value: (value.casefold(), value))


def build_custom_voice_mod(
    settings: CustomVoiceModSettings,
    progress: Progress = print,
) -> CustomVoiceModResult:
    """Generate a hidden custom version without invoking speech-to-text."""
    for field, value in (
        ("version_id", settings.version_id),
        ("based_on_version", settings.based_on_version),
        ("game", settings.game),
        ("default_localization_language", settings.default_localization_language),
        ("embedded_transcript_language", settings.embedded_transcript_language),
    ):
        if not IDENTIFIER_RE.fullmatch(value):
            raise CustomVoiceModError(f"Invalid {field}: {value!r}")
    if not settings.label.strip():
        raise CustomVoiceModError("The custom version label cannot be empty.")
    data_dir = settings.data_dir.expanduser().resolve()
    base_source = settings.base_source
    mod_vpk_path = settings.mod_vpk_path.expanduser().resolve()
    transcript_path = settings.transcript_path.expanduser().resolve()
    if settings.version_id == settings.based_on_version:
        raise CustomVoiceModError("The custom version ID must differ from based_on_version.")
    if not base_source.is_dir():
        raise CustomVoiceModError(f"Base generated version does not exist: {base_source}")
    try:
        cataloged_versions = load_cataloged_local_versions(data_dir, settings.game)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise CustomVoiceModError(f"Could not validate the local version catalog: {exc}") from exc
    base_entry = next(
        (
            entry for entry in cataloged_versions
            if entry.get("id") == settings.based_on_version
        ),
        None,
    )
    if base_entry is None:
        raise CustomVoiceModError(
            f"Custom base version is not cataloged locally: {settings.based_on_version!r}."
        )
    if base_entry.get("kind") != "official":
        raise CustomVoiceModError("A custom voice mod must be based on official content.")
    if not mod_vpk_path.is_file() or mod_vpk_path.suffix.casefold() != ".vpk":
        raise CustomVoiceModError(f"Select a valid mod .vpk file: {mod_vpk_path}")
    if not transcript_path.is_file():
        raise CustomVoiceModError(f"Pinned transcript source does not exist: {transcript_path}")
    _validate_existing_custom_output(settings)

    discovered: TranscriptProvenance | None = None
    if (
        settings.transcript_metadata_path is None
        or not settings.transcript_repository.strip()
        or not settings.transcript_revision.strip()
        or not settings.transcript_source_path.strip()
        or not settings.expected_transcript_sha256.strip()
    ):
        discovered = discover_transcript_provenance(transcript_path)
        progress(
            f"Pinned transcript provenance: {discovered.repository} at "
            f"{discovered.revision[:12]} ({discovered.source_path})."
        )
    metadata_path = (
        settings.transcript_metadata_path.expanduser().resolve()
        if settings.transcript_metadata_path is not None
        else discovered.metadata_path if discovered is not None else None
    )
    transcript_repository_value = (
        settings.transcript_repository.strip()
        or (discovered.repository if discovered is not None else "")
    )
    transcript_repository = (
        _public_repository_url(transcript_repository_value)
        if transcript_repository_value
        else ""
    )
    transcript_revision = (
        settings.transcript_revision.strip()
        or (discovered.revision if discovered is not None else "")
    )
    transcript_source_path = (
        settings.transcript_source_path.strip()
        or (discovered.source_path if discovered is not None else "")
    )
    transcript_hash = _sha256(transcript_path)
    expected_hash = (
        settings.expected_transcript_sha256.strip()
        or (discovered.sha256 if discovered is not None else "")
    ).casefold()
    if not expected_hash:
        raise CustomVoiceModError("An expected transcript SHA-256 is required for a pinned import.")
    if not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
        raise CustomVoiceModError("Expected transcript SHA-256 must contain 64 hexadecimal characters.")
    if transcript_hash != expected_hash:
        raise CustomVoiceModError(
            f"Pinned transcript SHA-256 mismatch: expected {expected_hash}, got {transcript_hash}."
        )

    base_voicelines = _read_json(base_source / "all_voicelines.json")
    base_conversations = _read_json(base_source / "all_conversations.json")
    tokens = parse_vdf_tokens(transcript_path)
    if not tokens:
        raise CustomVoiceModError(f"Pinned transcript source contains no VDF tokens: {transcript_path}")
    overrides = _load_overrides(settings.correlation_overrides_path)
    attribution = _transcript_attribution(metadata_path)
    if not isinstance(attribution.get("credits"), list) or not attribution["credits"]:
        raise CustomVoiceModError("Transcript metadata must preserve non-empty source credits.")
    for field_name, field_value in (
        ("transcript_repository", transcript_repository),
        ("transcript_revision", transcript_revision),
        ("transcript_source_path", transcript_source_path),
    ):
        if not field_value.strip():
            raise CustomVoiceModError(f"{field_name} is required for immutable provenance.")

    try:
        extraction = extract_vpk_voice_audio(
            source2viewer_binary=settings.source2viewer_binary,
            vpk_path=mod_vpk_path,
            workspace=settings.extraction_workspace,
            extraction_threads=settings.extraction_threads,
            force_reextract=settings.force_reextract,
            progress=progress,
        )
    except VpkPipelineError as exc:
        raise CustomVoiceModError(str(exc)) from exc
    mod_audio_dir = extraction.audio_dir

    records_by_filename: dict[str, list[dict[str, object]]] = {}
    for record in [*_walk_records(base_voicelines), *_walk_records(base_conversations)]:
        filename = record.get("filename")
        if isinstance(filename, str) and filename.strip():
            records_by_filename.setdefault(_normalized_path(filename), []).append(record)
    repeated_stem_aliases: dict[str, list[str]] = {}
    for base_key in records_by_filename:
        alias = _collapsed_repeated_stem_path(base_key)
        if alias and alias != base_key:
            repeated_stem_aliases.setdefault(alias, []).append(base_key)

    def effective_base_key(base_filename: str) -> tuple[str, bool]:
        requested = _normalized_path(base_filename)
        if requested in records_by_filename:
            return requested, False
        repaired_candidates = sorted(set(repeated_stem_aliases.get(requested, [])))
        return (repaired_candidates[0], True) if repaired_candidates else (requested, False)

    mod_files = sorted(
        path for path in mod_audio_dir.rglob("*")
        if path.is_file() and path.suffix.casefold() in AUDIO_SUFFIXES
    )
    warnings: list[dict[str, str]] = []
    embedded_by_record: dict[int, str] = {}
    matched_audio: dict[str, Path] = {}
    match_counts = {"exact": 0, "filename": 0, "suffix": 0, "override": 0}
    used_transcript_tokens: set[str] = set()
    blocked_audio: set[Path] = set()
    normalized_audio_groups: dict[str, list[Path]] = {}
    base_target_groups: dict[str, list[Path]] = {}
    for audio_path in mod_files:
        relative = _safe_relative_path(audio_path.relative_to(mod_audio_dir).as_posix())
        normalized = relative.casefold()
        normalized_audio_groups.setdefault(normalized, []).append(audio_path)
        override = overrides.get(normalized)
        base_filename = override.get("baseFilename") if override else relative
        resolved_base_key, _repaired = effective_base_key(base_filename)
        base_target_groups.setdefault(resolved_base_key, []).append(audio_path)
    for normalized, paths in normalized_audio_groups.items():
        if len(paths) < 2:
            continue
        ordered = sorted(
            paths,
            key=lambda path: (
                path.relative_to(mod_audio_dir).as_posix().casefold(),
                path.relative_to(mod_audio_dir).as_posix(),
            ),
        )
        selected = ordered[0]
        blocked_audio.update(ordered[1:])
        selected_path = selected.relative_to(mod_audio_dir).as_posix()
        for path in ordered[1:]:
            relative = path.relative_to(mod_audio_dir).as_posix()
            warnings.append({
                "audioPath": relative,
                "candidateId": PurePosixPath(relative).stem,
                "stage": "audio-path-collision",
                "reason": (
                    f"Multiple recordings normalize to the same path {normalized!r}; "
                    f"selected the first recording {selected_path!r}."
                ),
            })
    for base_key, paths in base_target_groups.items():
        candidates = [path for path in paths if path not in blocked_audio]
        unique_paths = {path.resolve() for path in candidates}
        if len(unique_paths) < 2:
            continue
        ordered = sorted(
            candidates,
            key=lambda path: (
                path.relative_to(mod_audio_dir).as_posix().casefold(),
                path.relative_to(mod_audio_dir).as_posix(),
            ),
        )
        selected = ordered[0]
        blocked_audio.update(ordered[1:])
        selected_path = selected.relative_to(mod_audio_dir).as_posix()
        for path in ordered[1:]:
            relative = path.relative_to(mod_audio_dir).as_posix()
            warnings.append({
                "audioPath": relative,
                "candidateId": PurePosixPath(relative).stem,
                "stage": "audio-to-base-ambiguous",
                "reason": (
                    f"Multiple mod recordings target base filename {base_key!r}; "
                    f"selected the first recording {selected_path!r}."
                ),
            })
    present_mod_paths = set(normalized_audio_groups)
    for override_path in sorted(set(overrides) - present_mod_paths):
        warnings.append({
            "audioPath": override_path,
            "candidateId": PurePosixPath(override_path).stem,
            "stage": "unused-override",
            "reason": "Correlation override has no matching mod recording.",
        })

    for audio_path in mod_files:
        if audio_path in blocked_audio:
            continue
        relative = _safe_relative_path(audio_path.relative_to(mod_audio_dir).as_posix())
        normalized = relative.casefold()
        override = overrides.get(normalized)
        base_filename = override.get("baseFilename") if override else relative
        base_key = _normalized_path(base_filename)
        resolved_base_key, repaired = effective_base_key(base_filename)
        records = records_by_filename.get(resolved_base_key, [])
        if repaired:
            warnings.append({
                "audioPath": relative,
                "candidateId": PurePosixPath(relative).stem,
                "stage": "audio-to-base-repaired",
                "reason": (
                    "Base data contains a duplicated filename stem; selected the first "
                    f"matching base path {resolved_base_key!r}."
                ),
            })
        if not records:
            warnings.append({
                "audioPath": relative,
                "candidateId": PurePosixPath(relative).stem,
                "stage": "audio-to-base",
                "reason": f"No base-version record has filename {base_filename!r}.",
            })
            continue
        correlated = 0
        for record in records:
            transcript_record = record
            if resolved_base_key != base_key:
                transcript_record = dict(record)
                transcript_record["filename"] = relative
            text, stage, token_key = _resolve_transcript(
                transcript_record,
                tokens,
                override.get("transcriptKey") if override else None,
            )
            if text is None:
                warnings.append({
                    "audioPath": relative,
                    "candidateId": str(record.get("voiceline_id") or token_key),
                    "stage": f"transcript-{stage}",
                    "reason": (
                        "Pinned Russian transcript token is missing; embedded transcription "
                        "was left blank."
                    ),
                })
                text = ""
            elif stage == "ambiguous-first":
                warnings.append({
                    "audioPath": relative,
                    "candidateId": str(record.get("voiceline_id") or token_key),
                    "stage": "transcript-ambiguous-first",
                    "reason": (
                        "Multiple pinned Russian transcript candidates matched; selected the "
                        f"first candidate {token_key!r}."
                    ),
                })
            embedded_by_record[id(record)] = text
            if text:
                used_transcript_tokens.add(token_key)
            match_counts[stage] = match_counts.get(stage, 0) + 1
            correlated += 1
        if correlated:
            matched_audio[resolved_base_key] = audio_path

    unsupported: list[tuple[str, str]] = []
    ignored_extraction_sidecars = 0
    for path in sorted(mod_audio_dir.rglob("*")):
        if not path.is_file() or path.suffix.casefold() in AUDIO_SUFFIXES:
            continue
        relative = path.relative_to(mod_audio_dir).as_posix()
        if path.suffix.casefold() == ".vsnd" and path.with_suffix(".mp3").is_file():
            ignored_extraction_sidecars += 1
            continue
        reason = (
            "Source2Viewer did not produce the companion MP3 for this VSND file."
            if path.suffix.casefold() == ".vsnd"
            else "Unsupported audio format; convert the recording to MP3 before import."
        )
        unsupported.append((relative, reason))
    for relative, reason in unsupported:
        warnings.append({
            "audioPath": relative,
            "candidateId": PurePosixPath(relative).stem,
            "stage": "audio-format",
            "reason": reason,
        })

    if not matched_audio:
        for warning in warnings:
            progress("WARNING: " + warning["audioPath"] + " — " + warning["reason"])
        raise CustomVoiceModError(
            "No mod MP3 recording could be matched to a playable base-version record."
        )

    filtered_voicelines = _filter_tree(base_voicelines, embedded_by_record)
    if not isinstance(filtered_voicelines, dict):
        raise CustomVoiceModError("Filtered voice-line data is not an object.")
    filtered_conversations, conversation_records = _filter_conversations(
        base_conversations,
        embedded_by_record,
    )
    voiceline_records = sum(1 for _record in _walk_records(filtered_voicelines))

    output_backup: Path | None = None
    preview_backup: Path | None = None
    staging = settings.output_dir.with_name(settings.output_dir.name + ".custom-import.tmp")
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    try:
        write_json(staging / "all_voicelines.json", filtered_voicelines)
        write_json(staging / "all_conversations.json", filtered_conversations)
        _copy_supporting_resources(base_source, staging)
        for normalized, source in matched_audio.items():
            source_name = next(
                str(record.get("filename"))
                for record in records_by_filename[normalized]
                if isinstance(record.get("filename"), str)
            )
            destination = staging / "Audio" / Path(*PurePosixPath(source_name.replace("\\", "/")).parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)

        matched_source_paths = {
            source.relative_to(mod_audio_dir).as_posix()
            for source in matched_audio.values()
        }
        unmatched_by_folder: dict[str, list[str]] = {}
        for warning in warnings:
            audio_path = warning["audioPath"]
            if audio_path in matched_source_paths:
                continue
            folder = str(PurePosixPath(audio_path).parent)
            unmatched_by_folder.setdefault(folder, []).append(audio_path)
        matched_count = len(matched_audio)
        total_count = len(mod_files) + len(unsupported)
        write_json(staging / "coverage.json", {
            "summary": {
                "total_files": total_count,
                "matched_files": matched_count,
                "unmatched_files": max(0, total_count - matched_count),
                "coverage_percentage": (matched_count / total_count * 100) if total_count else 0,
                "matched_in_voicelines": voiceline_records,
                "matched_in_conversations": conversation_records,
            },
            "unmatched_by_folder": unmatched_by_folder,
        })

        transcript_source_dir = staging / "TranscriptSource"
        transcript_source_dir.mkdir()
        shutil.copy2(transcript_path, transcript_source_dir / transcript_path.name)
        if metadata_path:
            shutil.copy2(metadata_path, transcript_source_dir / "metadata.json")
        transcript_source = {
            "repository": transcript_repository,
            "revision": transcript_revision,
            "repositoryPath": transcript_source_path or transcript_path.name,
            "path": transcript_path.name,
            "sha256": transcript_hash,
            "pinVerified": True,
            "size": transcript_path.stat().st_size,
            "entryCount": len(tokens),
            "parser": "vlviewer-vdf-kv-v1",
            "matchCounts": match_counts,
            "attribution": attribution,
        }
        report = {
            "schemaVersion": 1,
            "speechToTextUsed": False,
            "audioSource": {
                "type": "vpk",
                "filename": mod_vpk_path.name,
                "size": mod_vpk_path.stat().st_size,
                "fingerprint": extraction.vpk_fingerprint,
            },
            "publishable": True,
            "warningCount": len(warnings),
            "blockingWarningCount": 0,
            "nonBlockingWarningCount": len(warnings),
            "warnings": warnings,
            "matchCounts": match_counts,
            "audioFileCount": total_count,
            "matchedAudioCount": matched_count,
            "ignoredExtractionSidecarCount": ignored_extraction_sidecars,
            "voicelineRecordCount": voiceline_records,
            "conversationRecordCount": conversation_records,
            "baseRecordCount": sum(len(records) for records in records_by_filename.values()),
            "excludedBaseRecordCount": max(
                0,
                sum(len(records) for records in records_by_filename.values())
                - voiceline_records
                - conversation_records,
            ),
            "multiplyReferencedAudioCount": sum(
                1 for key in matched_audio if len(records_by_filename.get(key, [])) > 1
            ),
            "unusedTranscriptTokenCount": len(set(tokens) - used_transcript_tokens),
            "unusedCorrelationOverrideCount": len(set(overrides) - present_mod_paths),
            "audioCollisionCount": len(blocked_audio),
        }
        write_json(staging / "transcript-source.json", transcript_source)
        write_json(staging / "custom-import-report.json", report)
        custom_metadata = {
            "schemaVersion": 1,
            "kind": "custom",
            "hidden": True,
            "basedOnVersion": settings.based_on_version,
            "defaultLocalizationLanguage": settings.default_localization_language,
            "transcriptMode": "embedded",
            "embeddedTranscriptLanguage": settings.embedded_transcript_language,
            "audioSource": {
                "type": "vpk",
                "filename": mod_vpk_path.name,
                "fingerprint": extraction.vpk_fingerprint,
            },
            "transcriptSource": transcript_source,
            "correlation": {
                "publishable": True,
                "warningCount": len(warnings),
                "blockingWarningCount": 0,
            },
        }
        write_json(staging / "custom-version.json", custom_metadata)

        output_backup = _replace_local_directory(
            staging,
            settings.output_dir,
            settings.output_dir.parent,
        )
    finally:
        if staging.exists():
            shutil.rmtree(staging)

    preview_id = f"preview-{settings.version_id}"
    preview_root = data_dir / "preview-content" / settings.game / "versions" / preview_id
    preview_staging = preview_root.with_name(preview_root.name + ".custom-import.tmp")
    if preview_staging.exists():
        shutil.rmtree(preview_staging)
    preview_staging.mkdir(parents=True)
    try:
        shutil.copy2(
            settings.output_dir / "all_voicelines.json",
            preview_staging / "voicelines.json",
        )
        shutil.copy2(
            settings.output_dir / "all_conversations.json",
            preview_staging / "conversations.json",
        )
        shutil.copy2(settings.output_dir / "coverage.json", preview_staging / "coverage.json")
        if (settings.output_dir / "categories.json").is_file():
            shutil.copy2(
                settings.output_dir / "categories.json",
                preview_staging / "categories.json",
            )
        _copy_tree(settings.output_dir / "Audio", preview_staging / "audio")
        _copy_tree(settings.output_dir / "Localization", preview_staging / "localization")
        _copy_tree(
            settings.output_dir / "CharacterNameImages",
            preview_staging / "character-name-images",
        )
        _copy_tree(
            settings.output_dir / "IconPacks" / "default",
            preview_staging / "icons" / "default",
        )
        preview_backup = _replace_local_directory(
            preview_staging,
            preview_root,
            preview_root.parent,
        )
    finally:
        if preview_staging.exists():
            shutil.rmtree(preview_staging)

    metadata = _read_json(settings.output_dir / "custom-version.json")
    assert isinstance(metadata, dict)
    game_root = data_dir / "preview-content" / settings.game
    manifest_path = game_root / "manifest.json"
    manifest = _read_json(manifest_path) if manifest_path.is_file() else {
        "schemaVersion": 1,
        "game": settings.game,
        "latestVersion": "",
        "versions": [],
    }
    if not isinstance(manifest, dict):
        raise CustomVoiceModError(f"Invalid preview manifest: {manifest_path}")
    existing_entries = manifest.get("versions")
    if not isinstance(existing_entries, list):
        existing_entries = []
    base_url = "http://127.0.0.1:8787"
    public_base = f"{base_url}/{settings.game}/versions/{preview_id}"
    preview_entry: dict[str, object] = {
        "id": preview_id,
        "label": f"Preview: {settings.label}",
        "hidden": True,
        "conversationUrl": f"{public_base}/conversations.json",
        "voiceLineUrl": f"{public_base}/voicelines.json",
        "audioBaseUrl": f"{public_base}/audio/",
        "coverageUrl": f"{public_base}/coverage.json",
        "kind": "custom",
        "basedOnVersion": f"preview-{settings.based_on_version}",
        "defaultLocalizationLanguage": settings.default_localization_language,
        "transcriptMode": "embedded",
        "embeddedTranscriptLanguage": settings.embedded_transcript_language,
        "transcriptSource": metadata.get("transcriptSource", {}),
    }
    for path, field, suffix in (
        (preview_root / "categories.json", "categoriesUrl", "categories.json"),
        (preview_root / "localization" / "manifest.json", "localizationManifestUrl", "localization/manifest.json"),
        (preview_root / "character-name-images" / "manifest.json", "characterNameImagesUrl", "character-name-images/manifest.json"),
        (preview_root / "icons" / "default" / "manifest.json", "iconOverridesUrl", "icons/default/manifest.json"),
    ):
        if path.is_file():
            preview_entry[field] = f"{public_base}/{suffix}"
    manifest["versions"] = [
        preview_entry,
        *[
            value for value in existing_entries
            if isinstance(value, dict) and value.get("id") != preview_id
        ],
    ]
    write_json(manifest_path, manifest)

    catalog = register_local_version(
        data_dir,
        settings.game,
        settings.version_id,
        settings.label,
        metadata=metadata,
    )
    rebuild_local_preview_manifest(data_dir, settings.game, catalog)
    recalculate_version_statuses(data_dir, settings.game, catalog, progress)

    characters_path = game_root / "characters.json"
    existing_characters = _read_json(characters_path) if characters_path.is_file() else {}
    if not isinstance(existing_characters, dict):
        existing_characters = {}
    versions = existing_characters.get("versions")
    if not isinstance(versions, dict):
        versions = {}
    versions[preview_id] = _route_characters(filtered_voicelines, filtered_conversations)
    all_characters = sorted({
        character
        for values in versions.values()
        if isinstance(values, list)
        for character in values
        if isinstance(character, str)
    }, key=lambda value: (value.casefold(), value))
    write_json(characters_path, {
        "schemaVersion": 1,
        "game": settings.game,
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "characters": all_characters,
        "versions": versions,
    })

    for backup in (preview_backup, output_backup):
        if backup is not None and backup.exists():
            shutil.rmtree(backup)

    for warning in warnings:
        progress(
            "WARNING: " + warning["audioPath"] + " — " + warning["reason"]
        )
    progress(
        f"Generated custom version {settings.version_id!r}: {len(matched_audio):,} audio file(s), "
        f"{voiceline_records:,} voiceline record(s), {conversation_records:,} conversation "
        f"line(s), {len(warnings):,} non-blocking warning(s). Speech-to-text was not used."
    )
    return CustomVoiceModResult(
        output_dir=settings.output_dir,
        extraction_workspace=extraction.workspace,
        preview_version_id=preview_id,
        audio_files=len(matched_audio),
        voiceline_records=voiceline_records,
        conversation_records=conversation_records,
        warnings=tuple(warnings),
    )
