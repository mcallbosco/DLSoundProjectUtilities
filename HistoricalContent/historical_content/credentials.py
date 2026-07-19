"""Windows-user-scoped storage for the optional OpenAI API key."""

from __future__ import annotations

import ctypes
import json
import os
import sys
from ctypes import wintypes
from pathlib import Path


class CredentialStoreError(RuntimeError):
    pass


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


_CRYPTPROTECT_UI_FORBIDDEN = 0x01
_DESCRIPTION = "VLViewer Historical Content OpenAI credential"


def is_supported() -> bool:
    return sys.platform == "win32"


def _input_blob(data: bytes) -> tuple[_DataBlob, ctypes.Array]:
    buffer = ctypes.create_string_buffer(data)
    return _DataBlob(
        len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))
    ), buffer


def _protect(data: bytes) -> bytes:
    if not is_supported():
        raise CredentialStoreError("Secure credential saving is supported only on Windows.")
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DataBlob), wintypes.LPCWSTR, ctypes.POINTER(_DataBlob),
        wintypes.LPVOID, wintypes.LPVOID, wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel32.LocalFree.restype = wintypes.HLOCAL
    source, source_buffer = _input_blob(data)
    protected = _DataBlob()
    if not crypt32.CryptProtectData(
        ctypes.byref(source), _DESCRIPTION, None, None, None,
        _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(protected),
    ):
        raise CredentialStoreError(str(ctypes.WinError(ctypes.get_last_error())))
    try:
        return ctypes.string_at(protected.pbData, protected.cbData)
    finally:
        kernel32.LocalFree(protected.pbData)


def _unprotect(data: bytes) -> bytes:
    if not is_supported():
        raise CredentialStoreError("Secure credential saving is supported only on Windows.")
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DataBlob), ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(_DataBlob), wintypes.LPVOID, wintypes.LPVOID,
        wintypes.DWORD, ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel32.LocalFree.restype = wintypes.HLOCAL
    source, source_buffer = _input_blob(data)
    clear = _DataBlob()
    if not crypt32.CryptUnprotectData(
        ctypes.byref(source), None, None, None, None,
        _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(clear),
    ):
        raise CredentialStoreError(str(ctypes.WinError(ctypes.get_last_error())))
    try:
        return ctypes.string_at(clear.pbData, clear.cbData)
    finally:
        kernel32.LocalFree(clear.pbData)


def save_api_key(path: Path, api_key: str) -> None:
    payload = json.dumps({"schemaVersion": 1, "openaiApiKey": api_key}).encode("utf-8")
    encrypted = _protect(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(encrypted)
    os.replace(temporary, path)


def load_saved_api_key(path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(_unprotect(path.read_bytes()).decode("utf-8"))
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

