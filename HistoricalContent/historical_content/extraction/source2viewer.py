"""Run Source2Viewer and reuse isolated VPK voice-audio extractions."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ..errors import VpkPipelineError
from ..json_io import write_json
from ..parsing.common import read_json

Progress = Callable[[str], None]
AUDIO_SUFFIXES = {".mp3", ".wav", ".ogg", ".m4a"}


@dataclass(frozen=True)
class VpkVoiceAudioResult:
    """One isolated, reusable `sounds/vo` extraction from a VPK."""

    audio_dir: Path
    workspace: Path
    state_path: Path
    audio_count: int
    vpk_fingerprint: dict[str, object]
    reused: bool


def quick_vpk_fingerprint(path: Path) -> dict[str, object]:
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


def replace_directory(path: Path, parent: Path) -> None:
    resolved = path.resolve()
    allowed = parent.resolve()
    if resolved == allowed or allowed not in resolved.parents:
        raise VpkPipelineError(
            f"Refusing to replace a path outside {allowed}: {resolved}"
        )
    if resolved.exists():
        shutil.rmtree(resolved)
    resolved.mkdir(parents=True, exist_ok=True)


def run_source2viewer(
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
        "-i",
        str(vpk),
        "-o",
        str(output),
        "-f",
        file_filter,
        "-d",
        "--threads",
        str(threads),
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
    tail: deque[str] = deque(maxlen=15)
    for line in process.stdout or ():
        text = line.strip()
        if text:
            tail.append(text)
    return_code = process.wait()
    if return_code:
        detail = "\n".join(tail)
        raise VpkPipelineError(
            f"Source2Viewer exited with status {return_code} while extracting {file_filter}."
            + (f"\n{detail}" if detail else "")
        )


def find_audio_dir(audio_root: Path) -> Path:
    preferred = audio_root / "sounds" / "vo"
    if preferred.is_dir() and any(
        path.suffix.casefold() in AUDIO_SUFFIXES for path in preferred.rglob("*")
    ):
        return preferred
    candidates: list[tuple[int, Path]] = []
    for path in audio_root.rglob("*"):
        if path.is_dir() and path.name.casefold() == "vo":
            count = sum(
                item.suffix.casefold() in AUDIO_SUFFIXES for item in path.rglob("*")
            )
            if count:
                candidates.append((count, path))
    if candidates:
        return max(candidates, key=lambda item: item[0])[1]
    raise VpkPipelineError(
        f"Source2Viewer did not produce a voice-audio directory under {audio_root}."
    )


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
    fingerprint = quick_vpk_fingerprint(vpk)
    old_state = read_json(state_path) if state_path.is_file() else {}
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
        replace_directory(audio_root, resolved_workspace)
        run_source2viewer(
            binary,
            vpk,
            audio_root,
            "sounds/vo",
            extraction_threads,
            progress,
        )
        write_json(
            state_path,
            {
                "schemaVersion": 1,
                "vpkPath": str(vpk),
                "vpkFingerprint": fingerprint,
                "extractionComplete": True,
                "updatedAt": datetime.now().isoformat(),  # noqa: DTZ005 - preserve cache timestamps
            },
        )
    else:
        progress(
            "VPK fingerprint is unchanged; reusing the isolated voice-audio extraction."
        )

    audio_dir = find_audio_dir(audio_root)
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


def find_game_root(vpk: Path) -> Path | None:
    for root in vpk.parents:
        candidate = root / "game" / "citadel"
        if candidate.is_dir() and (candidate / vpk.name).is_file():
            return root
    return None
