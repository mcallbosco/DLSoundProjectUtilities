"""Compatibility entry point; install the repository with pip install -e . first."""

from historical_content.publishing.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
