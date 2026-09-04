"""Audio indexing, immutable shared storage, and the generated SQLite index."""

from __future__ import annotations

import hashlib
import math
import os
import re
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path, PurePosixPath
from typing import Iterable

from ..errors import BaselineError


def normalize_audio_key(filename: str) -> str:
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


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
        normalized = normalize_audio_key(filename)
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
        from mutagen import File as MutagenFile, MutagenError

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


def find_audio_root(root: Path) -> Path:
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


def replace_directory(path: Path, allowed_parent: Path) -> None:
    resolved = path.resolve()
    parent = allowed_parent.resolve()
    if parent not in resolved.parents or resolved == parent:
        raise BaselineError(f"Refusing to replace preview directory outside {parent}: {resolved}")
    if resolved.exists():
        shutil.rmtree(resolved)
    resolved.mkdir(parents=True)


def link_or_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        destination.unlink()
    try:
        os.link(source, destination)
    except OSError:
        shutil.copy2(source, destination)


def shared_audio_key(audio_hash: str) -> str:
    return f"sha256/{audio_hash[:2]}/{audio_hash}.mp3"


def ensure_shared_audio(source: Path, destination: Path, audio_hash: str) -> None:
    """Create one immutable local object for an audio hash and verify reuse."""
    if destination.is_file():
        if destination.stat().st_size == source.stat().st_size and sha256_file(destination) == audio_hash:
            return
        raise BaselineError(f"Shared audio object is corrupt or has a hash collision: {destination}")
    link_or_copy(source, destination)


def copy_tree(source: Path, destination: Path) -> None:
    if not source.is_dir():
        return
    for path in source.rglob("*"):
        if path.is_file():
            link_or_copy(path, destination / path.relative_to(source))


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


def write_version_index(
    path: Path,
    *,
    version_id: str,
    game: str,
    label: str,
    imported_at: str,
    records_by_kind: Iterable[tuple[str, Iterable[tuple[str, dict[str, object], Path | None]]]],
) -> None:
    """Replace one official version's index while preserving all other versions."""
    with closing(_open_database(path)) as database:
        database.execute(
            "INSERT OR REPLACE INTO versions(id, game, label, is_baseline, imported_at) VALUES(?, ?, ?, 1, ?)",
            (version_id, game, label, imported_at),
        )
        database.execute("DELETE FROM version_assets WHERE version_id = ?", (version_id,))
        database.executemany(
            "INSERT OR REPLACE INTO version_assets(version_id, kind, line_id, audio_sha256, filename, speaker) VALUES(?, ?, ?, ?, ?, ?)",
            (
                (version_id, kind, line.get("lineId"), line.get("audioSha256"), line.get("filename", ""), speaker)
                for kind, records in records_by_kind
                for speaker, line, _audio_path in records
            ),
        )
        database.commit()
