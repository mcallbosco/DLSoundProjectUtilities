#!/usr/bin/env python3
"""Install missing publisher dependencies, then launch the Tkinter GUI."""

from __future__ import annotations

try:
    from .dependencies import install_requirements, missing_modules
except ImportError:
    from dependencies import install_requirements, missing_modules


def main() -> int:
    missing = missing_modules()
    if missing:
        print("Missing publisher modules: " + ", ".join(missing))
        try:
            install_requirements()
        except Exception as exc:
            print(f"Could not install publisher requirements: {exc}")
            return 1

    try:
        from .publisher_gui import PublisherGUI
    except ImportError:
        from publisher_gui import PublisherGUI
    PublisherGUI().mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

