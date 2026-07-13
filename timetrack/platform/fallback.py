"""Best-effort backend for unsupported platforms (e.g. macOS, headless).

Returns no active window and zero idle time. This keeps the tracker running
(recording nothing rather than crashing) on platforms without a dedicated
backend, and is also handy in CI / headless environments.
"""

from __future__ import annotations

from .common import ActiveWindow

BACKEND_NAME = "fallback"


def get_active_window() -> ActiveWindow | None:
    return None


def get_idle_seconds() -> float:
    return 0.0
