"""Install missing HistoricalContent dependencies before starting the GUI."""

from __future__ import annotations

import importlib.util
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
subprocess.check_call([sys.executable, str(APP_DIR / "historical_content_gui.py")])
