# DLSoundProject Utilities

## Overview
There are two types of outputs: **Voicelines** and **Conversations**. While conversations are made up of voicelines, they are not in the Voicelines files.

The tools expect all of the VO files as .mp3s within a single folder. This is super easy to do with S2Viewer.

---

## Quick Start

### Running the Batch GUI

To launch the Batch GUI application (This will extract the game files and run the voiceline and conversation utility together), simply run:

```bash
source .venv/bin/activate
python AllInOne/batch_gui.py
```

---

## Troubleshooting

### Segmentation Fault with batch_gui.py (Python 3.14 + Tcl/Tk 9.0 Issue)

**Problem:** When running `batch_gui.py`, you may encounter a segmentation fault:
```
Segmentation fault (core dumped) /path/to/python /path/to/batch_gui.py
```

**Root Cause:** Python 3.14.0 has a known compatibility issue with Tcl/Tk 9.0, particularly when using threading with tkinter (see [Python Issue #141237](https://github.com/python/cpython/issues/141237)). The application crashes immediately on startup.

**Solution:** Use Python 3.12 with Tcl/Tk 8.6 instead.

#### Steps to Fix:

1. **Install Python 3.12** (if not already installed):
   ```bash
   # On Fedora
   sudo dnf install python3.12 python3.12-tkinter
   
   # On Ubuntu/Debian
   sudo apt install python3.12 python3.12-tk
   ```

2. **Verify Python 3.12 uses Tcl/Tk 8.6**:
   ```bash
   python3.12 -c "import tkinter; print('Tcl/Tk version:', tkinter.TclVersion, tkinter.TkVersion)"
   # Should output: Tcl/Tk version: 8.6 8.6
   ```

3. **Backup your existing virtual environment**:
   ```bash
   cd /path/to/DLSoundProjectUtilities
   mv .venv .venv_backup_py314
   ```

4. **Create a new virtual environment with Python 3.12**:
   ```bash
   python3.12 -m venv .venv
   ```

5. **Activate the new environment and reinstall dependencies**:
   ```bash
   source .venv/bin/activate  # On Linux/Mac
   # or
   .venv\Scripts\activate  # On Windows
   
   pip install -r requirements.txt
   # Or if you saved your old packages:
   pip install annotated-types anyio certifi distro h11 httpcore httpx idna jiter openai pydantic pydantic_core pygame sniffio tqdm typing-inspection typing_extensions
   ```

6. **Test the application**:
   ```bash
   python AllInOne/batch_gui.py
   ```

The application should now launch without segmentation faults.

**Note:** Once Python 3.14 + Tcl/Tk 9.0 compatibility issues are resolved upstream, you can upgrade back to Python 3.14. Check the [Python issue tracker](https://github.com/python/cpython/issues/141237) for updates.

---
