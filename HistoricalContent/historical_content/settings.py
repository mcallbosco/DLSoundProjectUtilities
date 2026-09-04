"""Bundled resources and persistent operator settings for repository installations."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .transcription import DEFAULT_MODEL


APP_DIR = Path(__file__).resolve().parents[1]
REPOSITORY_DIR = APP_DIR.parent
DEFAULTS_DIR = Path(__file__).resolve().parent / "defaults"
CONFIG_PATH = APP_DIR / "config.json"
CREDENTIAL_PATH = APP_DIR / "credentials.dpapi"
PUBLISHER_DIR = APP_DIR / "publisher-state"
PUBLISHER_CONFIG_PATH = PUBLISHER_DIR / "config.json"
PUBLISHER_CREDENTIAL_PATH = PUBLISHER_DIR / "credentials.dpapi"
PUBLISHER_STATE_DIR = PUBLISHER_DIR / ".state"
LEGACY_WEBSITE_DIR = REPOSITORY_DIR.parent / "ConvoWebsite" / "convowebsite"


def _sibling_repository(name: str) -> Path:
    for parent in REPOSITORY_DIR.parents:
        candidate = parent / name
        if (candidate / ".git").exists():
            return candidate
    return REPOSITORY_DIR.parent / name


DEFAULT_WEBSITE_DIR = _sibling_repository("VLViewer")
SEED_FILES = {
    "characterMappings": "character_mappings.json",
    "topicAliases": "topic_mappings.json",
    "voicelineGroups": "voiceline_groups.json",
    "conversationOverrides": "conversation_overrides.json",
    "transcriptionVocabulary": "deadlock_vocabulary.json",
}
DEFAULTS = {
    "vpkPath": "",
    "source2viewerBinary": "",
    "transcriptRepo": str(_sibling_repository("Deadlock-Transcriptions")),
    "dataDir": "D:/VLViewerHistoricalData",
    "workerDir": str(REPOSITORY_DIR / "ContentDeliveryWorker"),
    "websiteDir": str(DEFAULT_WEBSITE_DIR),
    "versionId": "deadlock-base",
    "label": "Historical baseline",
    "game": "deadlock",
    "model": DEFAULT_MODEL,
    "workers": 4,
    "transcribeMissing": True,
    "includeAudio": True,
    "includePhantom": True,
    "extractLocalization": True,
    "extractIcons": True,
    "extractNameImages": True,
    "nameImageMaxHeight": 512,
    "extractionThreads": 8,
    "forceReextract": False,
    **{key: str(DEFAULTS_DIR / filename) for key, filename in SEED_FILES.items()},
    "predefinedTranscripts": "",
}


def _read_settings(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _same_path(value: object, path: Path) -> bool:
    return os.path.normcase(os.path.normpath(str(value))) == os.path.normcase(os.path.normpath(str(path)))


def load_config() -> dict[str, object]:
    result = {**DEFAULTS, **_read_settings(CONFIG_PATH)}
    for key, filename in SEED_FILES.items():
        if _same_path(result.get(key), REPOSITORY_DIR / "Assets" / filename):
            result[key] = str(DEFAULTS_DIR / filename)
    if _same_path(result.get("websiteDir"), LEGACY_WEBSITE_DIR):
        result["websiteDir"] = str(DEFAULT_WEBSITE_DIR)

    legacy = _read_settings(REPOSITORY_DIR / "AllInOne" / "config.json")
    for key, old_key in (("source2viewerBinary", "source2viewer_binary"), ("vpkPath", "vpk_path")):
        result[key] = result.get(key) or legacy.get(old_key, "")
    return result


def _copy_missing(source: Path, destination: Path) -> None:
    if destination.exists() or not source.is_file():
        return
    data = source.read_bytes()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(data)
    try:
        if temporary.read_bytes() != data:
            raise OSError(f"Could not verify migrated settings: {destination}")
        if not destination.exists():
            temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def migrate_publisher_state(app_dir: Path = APP_DIR) -> None:
    """Copy legacy publisher state once, preserving originals and existing files."""
    destination = app_dir / "publisher-state"
    marker = destination / "migration-v1.complete"
    if marker.is_file():
        return
    source = app_dir.parent / "ContentPublisher"
    destination.mkdir(parents=True, exist_ok=True)
    for name in ("config.json", "credentials.dpapi"):
        _copy_missing(source / name, destination / name)
    # Hash caches are derived data; an unreadable cache is rebuilt on demand.
    try:
        for path in (source / ".state").rglob("*"):
            if path.is_file():
                try:
                    _copy_missing(path, destination / path.relative_to(source))
                except OSError:
                    continue
    except OSError:
        pass
    # Remember completion so "Forget credentials" cannot resurrect the old key.
    marker.touch()
