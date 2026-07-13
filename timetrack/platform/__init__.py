"""Platform abstraction layer.

Exposes two functions regardless of the host OS:

- ``get_active_window()`` -> ActiveWindow | None
- ``get_idle_seconds()`` -> float

The concrete implementation is selected at import time based on the
running platform. Each backend degrades gracefully (returning ``None`` /
``0.0``) when the required OS facilities are unavailable, so the tracker
never crashes just because, say, an X server or a Win32 API is missing.
"""

from __future__ import annotations

import platform as _platform

from .common import ActiveWindow

_system = _platform.system()

if _system == "Windows":
    from . import windows as _backend
elif _system == "Linux":
    from . import linux as _backend
else:  # macOS or anything else -> best-effort stub
    from . import fallback as _backend


def get_active_window() -> ActiveWindow | None:
    """Return details of the currently focused window, or ``None``."""
    return _backend.get_active_window()


def get_idle_seconds() -> float:
    """Return seconds since the last keyboard/mouse input (0.0 if unknown)."""
    return _backend.get_idle_seconds()


def backend_name() -> str:
    """Return the name of the active backend (useful for diagnostics)."""
    return getattr(_backend, "BACKEND_NAME", _system.lower())


__all__ = ["ActiveWindow", "get_active_window", "get_idle_seconds", "backend_name"]
