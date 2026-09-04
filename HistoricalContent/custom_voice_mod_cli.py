#!/usr/bin/env python3
"""Import a pinned-script custom voice mod without speech-to-text."""

from __future__ import annotations

import argparse
from pathlib import Path

try:
    from .historical_content.custom_voice_mod import (
        CustomVoiceModError,
        CustomVoiceModSettings,
        build_custom_voice_mod,
    )
except ImportError:
    from historical_content.custom_voice_mod import (
        CustomVoiceModError,
        CustomVoiceModSettings,
        build_custom_voice_mod,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build a mod-audio-only custom VLViewer version from a voice VPK and pinned "
            "VDF/TXT script. This command never invokes speech-to-text."
        )
    )
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--game", default="deadlock")
    parser.add_argument("--version", required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--based-on-version", required=True)
    parser.add_argument("--source2viewer", required=True, type=Path)
    parser.add_argument("--mod-vpk", required=True, type=Path)
    parser.add_argument("--extraction-threads", type=int, default=8)
    parser.add_argument("--force-reextract", action="store_true")
    parser.add_argument("--transcript", required=True, type=Path)
    parser.add_argument(
        "--transcript-metadata",
        type=Path,
        help="Optional override; defaults to metadata.json beside the tracked transcript.",
    )
    parser.add_argument("--transcript-repository")
    parser.add_argument("--transcript-revision")
    parser.add_argument("--transcript-source-path")
    parser.add_argument("--expected-transcript-sha256")
    parser.add_argument("--correlation-overrides", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = build_custom_voice_mod(CustomVoiceModSettings(
            data_dir=args.data_dir,
            game=args.game,
            version_id=args.version,
            label=args.label,
            based_on_version=args.based_on_version,
            source2viewer_binary=args.source2viewer,
            mod_vpk_path=args.mod_vpk,
            transcript_path=args.transcript,
            transcript_metadata_path=args.transcript_metadata,
            transcript_repository=args.transcript_repository or "",
            transcript_revision=args.transcript_revision or "",
            transcript_source_path=args.transcript_source_path or "",
            expected_transcript_sha256=args.expected_transcript_sha256 or "",
            correlation_overrides_path=args.correlation_overrides,
            extraction_threads=args.extraction_threads,
            force_reextract=args.force_reextract,
        ))
    except CustomVoiceModError as exc:
        print(f"ERROR: {exc}")
        return 2
    if result.warnings:
        print(
            f"Generated publishable output with {len(result.warnings)} non-blocking "
            "correlation warning(s). See custom-import-report.json."
        )
        return 0
    print(f"Publishable custom source generated at {result.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
