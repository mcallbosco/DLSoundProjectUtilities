#!/usr/bin/env python3
"""
Delete non-verbal sound files (effort/pain sounds) from a folder.

These files typically don't have meaningful transcriptions and can be safely removed
if you don't need them for your project.
"""

import os
import argparse
from pathlib import Path

# Patterns that indicate non-verbal sounds
NONVERBAL_PATTERNS = [
    "_effort_dash_",
    "_effort_general_",
    "_effort_melee_big_",
    "_effort_melee_small_",
    "_pain_akira_laser_",
    "_pain_big_",
    "_pain_death_",
    "_pain_low_health_",
    "_pain_small_",
]


def find_nonverbal_files(folder_path, recursive=False):
    """Find all files matching non-verbal patterns in a folder."""
    matches = []
    folder = Path(folder_path)

    if recursive:
        files = folder.rglob("*")
    else:
        files = folder.glob("*")

    for file_path in files:
        if not file_path.is_file():
            continue
        filename_lower = file_path.name.lower()
        for pattern in NONVERBAL_PATTERNS:
            if pattern in filename_lower:
                matches.append(file_path)
                break

    return matches


def delete_nonverbal_files(folder_path, recursive=False, dry_run=True):
    """
    Delete files matching non-verbal patterns.

    Args:
        folder_path: Path to the folder to search
        recursive: Whether to search subdirectories
        dry_run: If True, only print what would be deleted without actually deleting

    Returns:
        Tuple of (files_found, files_deleted)
    """
    files = find_nonverbal_files(folder_path, recursive)

    if not files:
        print("No non-verbal files found.")
        return 0, 0

    print(f"Found {len(files)} non-verbal files:")
    for f in files:
        print(f"  {f}")

    if dry_run:
        print(f"\nDry run: {len(files)} files would be deleted.")
        print("Run with --delete to actually delete these files.")
        return len(files), 0

    deleted = 0
    for f in files:
        try:
            os.remove(f)
            deleted += 1
        except Exception as e:
            print(f"Error deleting {f}: {e}")

    print(f"\nDeleted {deleted}/{len(files)} files.")
    return len(files), deleted


def main():
    parser = argparse.ArgumentParser(
        description="Delete non-verbal sound files (effort/pain sounds) from a folder"
    )
    parser.add_argument(
        "folder",
        help="Path to the folder to search for non-verbal files"
    )
    parser.add_argument(
        "-r", "--recursive",
        action="store_true",
        help="Search subdirectories recursively"
    )
    parser.add_argument(
        "--delete",
        action="store_true",
        help="Actually delete the files (default is dry run)"
    )
    parser.add_argument(
        "--list-patterns",
        action="store_true",
        help="List the patterns used to identify non-verbal files"
    )

    args = parser.parse_args()

    if args.list_patterns:
        print("Non-verbal file patterns:")
        for pattern in NONVERBAL_PATTERNS:
            print(f"  {pattern}")
        return

    if not os.path.isdir(args.folder):
        print(f"Error: {args.folder} is not a valid directory")
        return

    delete_nonverbal_files(
        args.folder,
        recursive=args.recursive,
        dry_run=not args.delete
    )


if __name__ == "__main__":
    main()
