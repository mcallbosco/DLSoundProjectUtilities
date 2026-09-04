#!/usr/bin/env python3
"""Command-line entrypoint for baseline generation and local preview."""

from __future__ import annotations

import argparse
from pathlib import Path

from .baseline import (
    BaselineSettings, create_baseline, load_json, refresh_preview_categories,
    validate_categories,
)
from .credentials import resolve_api_key
from .preview import seed_preview
from .transcription import DEFAULT_MODEL, SUPPORTED_MODELS


from .settings import CREDENTIAL_PATH, DEFAULTS_DIR


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Generate a VLViewer historical baseline.")
    subparsers = result.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create", help="Create/regenerate the baseline and preview tree.")
    create.add_argument("source_dir", type=Path, help="Prepared version source folder.")
    create.add_argument("--transcript-repo", type=Path, required=True)
    create.add_argument("--data-dir", type=Path, required=True)
    create.add_argument("--version", default="deadlock-base")
    create.add_argument("--label", default="Historical baseline")
    create.add_argument("--game", default="deadlock")
    create.add_argument("--model", choices=SUPPORTED_MODELS, default=DEFAULT_MODEL)
    create.add_argument(
        "--transcription-vocabulary",
        type=Path,
        default=DEFAULTS_DIR / "deadlock_vocabulary.json",
    )
    create.add_argument(
        "--predefined-transcripts",
        type=Path,
        help="Optional CSV of official transcripts to apply before OpenAI.",
    )
    create.add_argument("--workers", type=int, default=4)
    create.add_argument("--no-transcribe", action="store_true")
    create.add_argument("--no-audio", action="store_true", help="Skip audio in the preview tree.")
    create.add_argument("--no-git-init", action="store_true")
    seed = subparsers.add_parser("seed-preview", help="Seed generated content into isolated local R2.")
    seed.add_argument("preview_root", type=Path)
    seed.add_argument("--worker-dir", type=Path, required=True)
    categories = subparsers.add_parser("validate-categories")
    categories.add_argument("categories", type=Path)
    categories.add_argument("--characters", type=Path, help="Optional JSON string-array of valid IDs.")
    refresh = subparsers.add_parser("refresh-categories", help="Refresh preview categories without re-indexing audio.")
    refresh.add_argument("source_dir", type=Path)
    refresh.add_argument("--transcript-repo", type=Path, required=True)
    refresh.add_argument("--data-dir", type=Path, required=True)
    refresh.add_argument("--version", default="deadlock-base")
    refresh.add_argument("--game", default="deadlock")
    return result


def main() -> int:
    args = parser().parse_args()
    if args.command == "seed-preview":
        seed_preview(args.worker_dir.resolve(), args.preview_root.resolve())
        return 0
    if args.command == "validate-categories":
        characters = set()
        if args.characters:
            payload = load_json(args.characters)
            if not isinstance(payload, list):
                raise SystemExit("--characters must contain a JSON array.")
            characters = {str(value) for value in payload}
        errors, warnings = validate_categories(load_json(args.categories), characters)
        for warning in warnings:
            print(f"WARNING: {warning}")
        for error in errors:
            print(f"ERROR: {error}")
        return 1 if errors else 0
    if args.command == "refresh-categories":
        path = refresh_preview_categories(
            source_dir=args.source_dir,
            transcript_repo=args.transcript_repo,
            data_dir=args.data_dir,
            version_id=args.version,
            game=args.game,
        )
        print(f"Categories refreshed: {path}")
        return 0
    api_key = resolve_api_key(None, CREDENTIAL_PATH)
    settings = BaselineSettings(
        source_dir=args.source_dir,
        transcript_repo=args.transcript_repo,
        data_dir=args.data_dir,
        version_id=args.version,
        label=args.label,
        game=args.game,
        model=args.model,
        api_key=api_key,
        transcription_vocabulary=args.transcription_vocabulary,
        predefined_transcripts=args.predefined_transcripts,
        transcribe_missing=not args.no_transcribe,
        workers=args.workers,
        initialize_git=not args.no_git_init,
        include_audio=not args.no_audio,
    )
    result = create_baseline(settings)
    print(f"Preview version: {result.preview_version_id}")
    print(f"Preview content: {result.preview_root}")
    print(f"Publisher source: {result.publish_source}")
    print(f"Categories: {result.categories_path}")
    print(f"Transcript repository: {result.transcript_repo}")
    print(f"Missing transcripts: {result.missing_transcripts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
