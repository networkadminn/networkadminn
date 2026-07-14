"""Shared data structures for platform backends."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ActiveWindow:
    """A snapshot of the currently focused window."""

    app: str
    """Application / executable name, e.g. ``firefox`` or ``Code.exe``."""

    title: str
    """The window title text (may be empty)."""

    pid: int | None = None
    """Process id owning the window, when it can be determined."""


__all__ = ["ActiveWindow"]
