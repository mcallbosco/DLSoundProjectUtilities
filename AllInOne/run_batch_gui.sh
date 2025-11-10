#!/usr/bin/env bash
# Script to launch the Source2Viewer Batch GUI with the correct Python environment
# This ensures the virtual environment is activated before running the application

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Get the project root directory (one level up from AllInOne)
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Path to the virtual environment
VENV_PATH="$PROJECT_ROOT/.venv"

# Check if virtual environment exists
if [ ! -d "$VENV_PATH" ]; then
    echo "Error: Virtual environment not found at $VENV_PATH"
    echo "Please create a virtual environment first:"
    echo "  cd $PROJECT_ROOT"
    echo "  python3.12 -m venv .venv"
    echo "  source .venv/bin/activate"
    echo "  pip install -r requirements.txt"
    exit 1
fi

# Check if the activate script exists
if [ ! -f "$VENV_PATH/bin/activate" ]; then
    echo "Error: Virtual environment activation script not found."
    echo "The virtual environment may be corrupted. Please recreate it."
    exit 1
fi

# Activate the virtual environment and run the application
echo "Activating virtual environment..."
source "$VENV_PATH/bin/activate"

# Verify Python version
PYTHON_VERSION=$(python --version 2>&1 | awk '{print $2}')
echo "Using Python $PYTHON_VERSION"

# Check Tcl/Tk version to warn about potential issues
TCL_TK_VERSION=$(python -c "import tkinter; print(f'{tkinter.TclVersion}')" 2>/dev/null)
if [ "$TCL_TK_VERSION" = "9.0" ]; then
    echo "Warning: Tcl/Tk 9.0 detected. This may cause issues with Python 3.14+"
    echo "If you experience crashes, please use Python 3.12 with Tcl/Tk 8.6"
    echo "See README.md for troubleshooting instructions."
    echo ""
fi

# Launch the GUI application
echo "Starting Source2Viewer Batch GUI..."
python "$SCRIPT_DIR/batch_gui.py"

# Capture exit code
EXIT_CODE=$?

if [ $EXIT_CODE -ne 0 ]; then
    echo ""
    echo "Application exited with code $EXIT_CODE"
    if [ $EXIT_CODE -eq 139 ]; then
        echo "Error: Segmentation fault detected!"
        echo "This is likely due to Python 3.14 + Tcl/Tk 9.0 compatibility issues."
        echo "Please see README.md for troubleshooting steps."
    fi
fi

exit $EXIT_CODE
