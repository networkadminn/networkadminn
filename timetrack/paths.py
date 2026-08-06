"""Resource path helpers (source tree + PyInstaller frozen builds)."""

from __future__ import annotations

import sys
from pathlib import Path


def package_dir(*parts: str) -> Path:
    """Return a directory under the ``timetrack`` package.

    When frozen with PyInstaller, resources live under ``sys._MEIPASS/timetrack``.
    """
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "timetrack" / Path(*parts)
    return Path(__file__).resolve().parent.joinpath(*parts)
