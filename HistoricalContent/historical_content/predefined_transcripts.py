"""Load curated official transcripts exported from game localization data."""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


ACCEPTED_STATUSES = {
    "single_match",
    "multiple_keys_same_transcription",
}
CONFLICT_STATUS = "multiple_conflicting_transcriptions"
REQUIRED_COLUMNS = {
    "file_path",
    "vo_root",
    "file_basename",
    "transcription",
    "localization_key",
    "removed_localization_suffix",
    "match_status",
}


class PredefinedTranscriptError(ValueError):
    """Raised when a predefined transcript CSV is unsafe or malformed."""


@dataclass(frozen=True)
class PredefinedTranscriptCatalog:
    """Validated transcript text keyed by normalized extracted MP3 path."""

    transcripts: dict[str, str]
    total_rows: int
    skipped_conflicts: int

    @property
    def accepted_rows(self) -> int:
        return len(self.transcripts)


def _normalize_source_path(value: str, *, row_number: int) -> tuple[str, str, str]:
    normalized = value.strip().replace("\\", "/")
    path = PurePosixPath(normalized)
    if (
        not normalized
        or path.is_absolute()
        or re.match(r"^[A-Za-z]:", normalized)
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise PredefinedTranscriptError(
            f"Predefined transcript row {row_number} has an unsafe file_path: {value!r}"
        )
    if (
        len(path.parts) < 4
        or path.parts[0].casefold() != "sounds"
        or path.parts[1].casefold() != "vo"
    ):
        raise PredefinedTranscriptError(
            f"Predefined transcript row {row_number} must be under sounds/vo/: {value!r}"
        )
    filename = path.name
    if not filename.casefold().endswith(".vsnd_c"):
        raise PredefinedTranscriptError(
            f"Predefined transcript row {row_number} must identify a .vsnd_c file: "
            f"{value!r}"
        )
    basename = filename[: -len(".vsnd_c")]
    relative_parts = (*path.parts[2:-1], f"{basename}.mp3")
    audio_key = PurePosixPath(*relative_parts).as_posix()
    return audio_key, path.parts[2], basename


def load_predefined_transcripts(path: Path) -> PredefinedTranscriptCatalog:
    """Validate and load safe official transcript matches from ``path``."""
    source = path.expanduser().resolve()
    if not source.is_file():
        raise PredefinedTranscriptError(
            f"Predefined transcript CSV does not exist: {source}"
        )

    transcripts: dict[str, str] = {}
    total_rows = 0
    skipped_conflicts = 0
    try:
        with source.open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            columns = set(reader.fieldnames or ())
            missing_columns = sorted(REQUIRED_COLUMNS - columns)
            if missing_columns:
                raise PredefinedTranscriptError(
                    "Predefined transcript CSV is missing required columns: "
                    + ", ".join(missing_columns)
                )
            for row_number, row in enumerate(reader, start=2):
                total_rows += 1
                status = str(row.get("match_status") or "").strip()
                if status == CONFLICT_STATUS:
                    skipped_conflicts += 1
                    continue
                if status not in ACCEPTED_STATUSES:
                    raise PredefinedTranscriptError(
                        f"Predefined transcript row {row_number} has an unsupported "
                        f"match_status: {status!r}"
                    )

                audio_key, path_root, path_basename = _normalize_source_path(
                    str(row.get("file_path") or ""),
                    row_number=row_number,
                )
                vo_root = str(row.get("vo_root") or "").strip()
                if vo_root.casefold() != path_root.casefold():
                    raise PredefinedTranscriptError(
                        f"Predefined transcript row {row_number} has vo_root "
                        f"{vo_root!r}, but its path uses {path_root!r}."
                    )
                file_basename = str(row.get("file_basename") or "").strip()
                if file_basename.casefold() != path_basename.casefold():
                    raise PredefinedTranscriptError(
                        f"Predefined transcript row {row_number} has file_basename "
                        f"{file_basename!r}, but its path uses {path_basename!r}."
                    )
                text = str(row.get("transcription") or "").strip()
                if not text:
                    raise PredefinedTranscriptError(
                        f"Predefined transcript row {row_number} has no transcription."
                    )
                lookup = audio_key.casefold()
                if lookup in transcripts:
                    raise PredefinedTranscriptError(
                        f"Multiple predefined transcript rows resolve to {audio_key!r}."
                    )
                transcripts[lookup] = text
    except (OSError, csv.Error) as exc:
        raise PredefinedTranscriptError(
            f"Could not read predefined transcript CSV {source}: {exc}"
        ) from exc

    return PredefinedTranscriptCatalog(
        transcripts=transcripts,
        total_rows=total_rows,
        skipped_conflicts=skipped_conflicts,
    )
