"""Install missing HistoricalContent dependencies before starting the GUI."""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path


APP_DIR = Path(__file__).resolve().parent
requirements = APP_DIR / "requirements.txt"
if any(importlib.util.find_spec(module) is None for module in ("openai", "boto3")):
    subprocess.check_call([
        sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
        "-r", str(requirements),
    ])
if not (APP_DIR / "node_modules" / "sharp").is_dir():
    npm = shutil.which("npm.cmd") or shutil.which("npm")
    if not npm:
        raise RuntimeError("Node.js and npm are required to prepare character-name images.")
    subprocess.check_call([npm, "install", "--omit=dev"], cwd=APP_DIR)
subprocess.check_call([sys.executable, str(APP_DIR / "historical_content_gui.py")])
