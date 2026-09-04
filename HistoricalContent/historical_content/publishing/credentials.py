"""Windows-user-scoped encrypted credential storage for the publisher GUI."""

from __future__ import annotations

import json
import os
from pathlib import Path

from ..protected_data import CredentialStoreError, is_supported as is_supported, protect, unprotect


_DESCRIPTION = 'VLViewer Content Publisher credentials'


def save_credentials(path: Path, credentials: dict[str, str]) -> None:
    payload = {
        "schemaVersion": 1,
        "credentials": {
            key: str(credentials.get(key, ""))
            for key in ("r2_access_key_id", "r2_secret_access_key", "cloudflare_api_token")
        },
    }
    encrypted = protect(json.dumps(payload).encode("utf-8"), _DESCRIPTION)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(encrypted)
    os.replace(temporary, path)


def load_credentials(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(unprotect(path.read_bytes()).decode("utf-8"))
    except CredentialStoreError:
        raise
    except Exception as exc:
        raise CredentialStoreError(f"Saved credentials are invalid: {exc}") from exc
    if payload.get("schemaVersion") != 1 or not isinstance(payload.get("credentials"), dict):
        raise CredentialStoreError("Saved credentials use an unsupported format.")
    return {
        key: value
        for key, value in payload["credentials"].items()
        if key in {"r2_access_key_id", "r2_secret_access_key", "cloudflare_api_token"}
        and isinstance(value, str)
    }


def delete_credentials(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
