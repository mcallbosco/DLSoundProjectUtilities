"""Windows-user-scoped storage for the optional OpenAI API key."""

from __future__ import annotations

import json
import os
from pathlib import Path

from .protected_data import CredentialStoreError, is_supported as is_supported, protect, unprotect


_DESCRIPTION = 'VLViewer Historical Content OpenAI credential'


def save_api_key(path: Path, api_key: str) -> None:
    payload = json.dumps({"schemaVersion": 1, "openaiApiKey": api_key}).encode("utf-8")
    encrypted = protect(payload, _DESCRIPTION)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(encrypted)
    os.replace(temporary, path)


def load_saved_api_key(path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(unprotect(path.read_bytes()).decode("utf-8"))
    except CredentialStoreError:
        raise
    except Exception as exc:
        raise CredentialStoreError(f"Saved credential is invalid: {exc}") from exc
    value = payload.get("openaiApiKey") if payload.get("schemaVersion") == 1 else None
    return value if isinstance(value, str) and value else None


def delete_saved_api_key(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def resolve_api_key(explicit: str | None, credential_path: Path | None = None) -> str | None:
    if explicit:
        return explicit.strip() or None
    environment = os.environ.get("OPENAI_API_KEY", "").strip()
    if environment:
        return environment
    if credential_path:
        saved = load_saved_api_key(credential_path)
        if saved:
            return saved
    legacy = Path.home() / ".open_ai_key"
    if legacy.is_file():
        value = legacy.read_text(encoding="utf-8").strip()
        if value:
            return value
    return None

