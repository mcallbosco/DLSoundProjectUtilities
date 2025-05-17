# DLUpdateComparison.py

## Overview

`DLUpdateComparison.py` is a GUI tool for comparing two voice line files. It identifies new or updated voice lines in the "after" file compared to the "before" file. The output marks each changed line with either `ADDED` or `UPDATED` at the end of the line.

## Output Format

- Lines ending with **ADDED**: The voice line is new in the "after" file (not present in the "before" file).
- Lines ending with **UPDATED**: The voice line exists in both files, but the CRC value has changed.

Example output:
```
sounds/vo/character/line1.ogg CRC:123456 ADDED
sounds/vo/character/line2.ogg CRC:654321 UPDATED
```

## How It Works

1. **File Selection**: Use the GUI to select the "before" and "after" files.
2. **Comparison**: Click "Compare Voice Lines" to find new or updated lines.
3. **Output**: Results are shown in the output log and can be saved to a file.

## Main Methods and Classes

### `parse_file(filepath)`
Parses a file to extract voice line paths and their CRCs. Returns a dictionary mapping paths to CRCs.

### `find_changed_voicelines(before_file, after_file)`
Compares two files. Returns a list of lines from the "after" file that are either new or have updated CRCs, appending `ADDED` or `UPDATED` at the end of each line.

### `VoiceLineComparerApp`
Tkinter GUI class for the application. Handles file selection, comparison, output display, and saving results.

- `browse_before_file()`, `browse_after_file()`: File dialogs for selecting input files.
- `set_output_file()`: File dialog for choosing the output file.
- `compare_files()`: Runs the comparison and displays/saves results.

## Usage

Run the script:
```
python DLUpdateComparison.py
```
Use the GUI to select files and view/save the comparison results.
