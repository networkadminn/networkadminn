"""Detect the Microsoft Store (MSIX) build of the Windows agent.

Inside a package Windows owns installation: the Store delivers updates and the
manifest's startup task handles launch at sign-in, so the agent must not run its
own installer or write the HKCU Run key (package registry writes are virtualized).
"""

from __future__ import annotations

import sys
from functools import lru_cache

_APPMODEL_ERROR_NO_PACKAGE = 15700
STORE_UPDATES_URI = "ms-windows-store://downloadsandupdates"
STARTUP_SETTINGS_URI = "ms-settings:startupapps"


@lru_cache(maxsize=1)
def is_packaged() -> bool:
    """True when this process runs from an installed MSIX package."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        length = ctypes.c_uint32(0)
        rc = ctypes.windll.kernel32.GetCurrentPackageFullName(ctypes.byref(length), None)
    except Exception:
        return False
    return rc != _APPMODEL_ERROR_NO_PACKAGE


__all__ = ["STARTUP_SETTINGS_URI", "STORE_UPDATES_URI", "is_packaged"]
