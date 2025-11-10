import os
import json
import sys
from typing import Optional


def strip_top_level_text_field(json_path: str) -> bool:
    """
    Remove the top-level "text" field from a JSON file if present.

    Returns True if the file was modified, False otherwise.
    """
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"[WARN] Failed to read JSON from {json_path}: {e}")
        return False

    if not isinstance(data, dict):
        # Only process object-style JSON where a top-level text field would make sense
        return False

    if "text" not in data:
        return False

    # Only remove if it's actually a simple top-level field; leave nested content untouched
    data.pop("text", None)

    try:
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"[INFO] Removed top-level 'text' from: {json_path}")
        return True
    except Exception as e:
        print(f"[ERROR] Failed to write updated JSON to {json_path}: {e}")
        return False


def process_directory(root_dir: str) -> None:
    """
    Walk the given directory recursively and strip top-level 'text'
    from all *.json files.
    """
    if not os.path.isdir(root_dir):
        print(f"[ERROR] Not a directory: {root_dir}")
        return

    total = 0
    modified = 0

    for current_root, _, files in os.walk(root_dir):
        for name in files:
            if not name.lower().endswith(".json"):
                continue
            path = os.path.join(current_root, name)
            total += 1
            if strip_top_level_text_field(path):
                modified += 1

    print(f"[DONE] Scanned {total} JSON files. Modified {modified}.")


def main(argv: Optional[list] = None) -> None:
    if argv is None:
        argv = sys.argv[1:]

    if len(argv) != 1:
        print("Usage: python remove_top_level_text.py /path/to/transcriptions_dir")
        sys.exit(1)

    target_dir = argv[0]
    process_directory(target_dir)


if __name__ == "__main__":
    main()
