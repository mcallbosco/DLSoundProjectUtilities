"""Prepare this repository installation and start Historical Content."""

from __future__ import annotations

import importlib.util
from importlib import metadata
import shutil
import subprocess
import sys
from pathlib import Path


APP_DIR = Path(__file__).resolve().parent
REPOSITORY_DIR = APP_DIR.parent


def main() -> None:
    try:
        metadata.distribution("vlviewer-historical-content")
    except metadata.PackageNotFoundError:
        installed = False
    else:
        installed = True
    if not installed or any(importlib.util.find_spec(name) is None for name in ("openai", "boto3", "mutagen")):
        subprocess.check_call([
            sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
            "--editable", str(REPOSITORY_DIR),
        ])
    if not (APP_DIR / "node_modules" / "sharp").is_dir():
        npm = shutil.which("npm.cmd") or shutil.which("npm")
        if not npm:
            raise RuntimeError("Node.js and npm are required to prepare content images.")
        subprocess.check_call([npm, "ci", "--omit=dev"], cwd=APP_DIR)
    subprocess.check_call([sys.executable, str(APP_DIR / "historical_content_gui.py")])


if __name__ == "__main__":
    main()
