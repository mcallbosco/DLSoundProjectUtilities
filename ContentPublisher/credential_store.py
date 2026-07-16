"""Windows-user-scoped encrypted credential storage for the publisher GUI."""

from __future__ import annotations

import ctypes
import json
import os
import sys
from ctypes import wintypes
from pathlib import Path


class CredentialStoreError(RuntimeError):
    """Raised when saved credentials cannot be protected or recovered."""


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


_CRYPTPROTECT_UI_FORBIDDEN = 0x01
_DESCRIPTION = "VLViewer Content Publisher credentials"


def is_supported() -> bool:
    return sys.platform == "win32"


def _input_blob(data: bytes) -> tuple[_DataBlob, ctypes.Array]:
    buffer = ctypes.create_string_buffer(data)
    blob = _DataBlob(
        len(data),
        ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)),
    )
    return blob, buffer


def _protect(data: bytes) -> bytes:
    if not is_supported():
        raise CredentialStoreError(
            "Encrypted credential saving is currently supported only on Windows."
        )
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        wintypes.LPCWSTR,
        ctypes.POINTER(_DataBlob),
        wintypes.LPVOID,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel32.LocalFree.restype = wintypes.HLOCAL

    source, source_buffer = _input_blob(data)
    protected = _DataBlob()
    if not crypt32.CryptProtectData(
        ctypes.byref(source),
        _DESCRIPTION,
        None,
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(protected),
    ):
        raise CredentialStoreError(str(ctypes.WinError(ctypes.get_last_error())))
    try:
        return ctypes.string_at(protected.pbData, protected.cbData)
    finally:
        kernel32.LocalFree(protected.pbData)


def _unprotect(data: bytes) -> bytes:
    if not is_supported():
        raise CredentialStoreError(
            "Encrypted credential saving is currently supported only on Windows."
        )
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(_DataBlob),
        wintypes.LPVOID,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel32.LocalFree.restype = wintypes.HLOCAL

    source, source_buffer = _input_blob(data)
    clear = _DataBlob()
    if not crypt32.CryptUnprotectData(
        ctypes.byref(source),
        None,
        None,
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(clear),
    ):
        raise CredentialStoreError(str(ctypes.WinError(ctypes.get_last_error())))
    try:
        return ctypes.string_at(clear.pbData, clear.cbData)
    finally:
        kernel32.LocalFree(clear.pbData)


def save_credentials(path: Path, credentials: dict[str, str]) -> None:
    payload = {
        "schemaVersion": 1,
        "credentials": {
            key: str(credentials.get(key, ""))
            for key in ("r2_access_key_id", "r2_secret_access_key", "cloudflare_api_token")
        },
    }
    encrypted = _protect(json.dumps(payload).encode("utf-8"))
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(encrypted)
    os.replace(temporary, path)


def load_credentials(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(_unprotect(path.read_bytes()).decode("utf-8"))
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
