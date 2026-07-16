"""Dependency checks and self-repair for the standalone publisher utility."""

from __future__ import annotations

import importlib
import importlib.util
import subprocess
import sys
from pathlib import Path
from typing import Callable


APP_DIR = Path(__file__).resolve().parent
REQUIREMENTS_PATH = APP_DIR / "requirements.txt"
REQUIRED_MODULES = ("boto3", "botocore")


def missing_modules() -> list[str]:
    return [name for name in REQUIRED_MODULES if importlib.util.find_spec(name) is None]


def install_requirements(progress: Callable[[str], None] = print) -> None:
    if not REQUIREMENTS_PATH.is_file():
        raise RuntimeError(f"Requirements file does not exist: {REQUIREMENTS_PATH}")
    command = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "-r",
        str(REQUIREMENTS_PATH),
    ]
    progress(f"Installing publisher requirements with {sys.executable}...")
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert process.stdout is not None
    for line in process.stdout:
        progress(line.rstrip())
    exit_code = process.wait()
    if exit_code:
        raise RuntimeError(f"pip exited with status {exit_code}.")
    importlib.invalidate_caches()
    still_missing = missing_modules()
    if still_missing:
        raise RuntimeError(
            "Installation completed, but these modules are still unavailable: "
            + ", ".join(still_missing)
        )
    progress("Publisher requirements are installed.")

