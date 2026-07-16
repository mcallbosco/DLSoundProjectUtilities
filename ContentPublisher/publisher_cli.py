#!/usr/bin/env python3
"""Command-line companion for the VLViewer content publisher GUI."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

try:
    from .publisher import (
        PublisherError,
        PublisherSettings,
        R2Publisher,
        build_publish_plan,
        format_bytes,
        validate_version_source,
    )
except ImportError:
    from publisher import (
        PublisherError,
        PublisherSettings,
        R2Publisher,
        build_publish_plan,
        format_bytes,
        validate_version_source,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate and publish VLViewer content versions.")
    parser.add_argument("source", type=Path, help="Existing website version directory")
    parser.add_argument("--game", required=True, help="Game key, such as deadlock")
    parser.add_argument("--version", required=True, help="Stable user-visible version ID")
    parser.add_argument("--label", required=True, help="Human-readable version label")
    parser.add_argument("--bucket", default="")
    parser.add_argument("--endpoint-url", default="")
    parser.add_argument("--cdn-base-url", default="https://cdn.vlviewer.com")
    parser.add_argument("--zone-id", default="")
    parser.add_argument("--state-dir", type=Path, default=Path(__file__).parent / ".state")
    parser.add_argument("--concurrency", type=int, default=12)
    parser.add_argument("--no-promote", action="store_true")
    parser.add_argument("--hidden", action="store_true", help="Hide this version from normal UI selectors")
    parser.add_argument(
        "command",
        choices=("validate", "plan", "publish"),
        nargs="?",
        default="validate",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    settings = PublisherSettings(
        source_dir=args.source,
        game=args.game,
        version=args.version,
        label=args.label,
        bucket=args.bucket,
        endpoint_url=args.endpoint_url,
        cdn_base_url=args.cdn_base_url,
        zone_id=args.zone_id,
        state_dir=args.state_dir,
        concurrency=args.concurrency,
        promote_to_latest=not args.no_promote,
        hidden=args.hidden,
    )
    try:
        if args.command == "validate":
            report = validate_version_source(settings.source_dir)
            print(
                json.dumps(
                    {
                        "valid": report.valid,
                        "errors": report.errors,
                        "warnings": report.warnings,
                        "files": len(report.files),
                        "bytes": report.total_bytes,
                        "referencedAudio": report.referenced_audio_count,
                        "audioFiles": report.audio_file_count,
                        "orphanAudio": report.orphan_audio_count,
                    },
                    indent=2,
                )
            )
            return 0 if report.valid else 1

        if args.command == "plan" and not settings.bucket:
            plan = build_publish_plan(settings, progress=print)
        else:
            publisher = R2Publisher(settings, print)
            plan = publisher.create_plan()
        print(
            f"Plan: {len(plan.upload_new):,} new, {len(plan.upload_changed_json):,} changed JSON, "
            f"{len(plan.unchanged):,} unchanged, {len(plan.immutable_conflicts):,} binary conflicts "
            f"({format_bytes(sum(item.size for item in plan.upload_records))} to upload)."
        )
        if not plan.can_publish:
            for error in plan.validation.errors:
                print(f"ERROR: {error}")
            for item, _remote in plan.immutable_conflicts:
                print(f"ERROR: immutable binary differs: {item.relative_path}")
            return 1
        if args.command == "publish":
            result = publisher.publish(plan)
            print(json.dumps(result, indent=2))
        return 0
    except PublisherError as exc:
        print(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
