"""Run agent windows (login, update prompt, idle countdown) in a child process.

The tray owns the main thread of the agent process. Tk and GTK windows opened
from other threads crash on macOS (AppKit allows windows on the main thread only)
and can fight the tray's own GTK loop on Linux. A short-lived child process runs
the window on *its* main thread instead, on every platform.
"""

from __future__ import annotations

import os
import subprocess
import sys


def agent_command(*args: str, config_path: str | None = None) -> list[str]:
    """Command line that re-invokes this agent with ``args``.

    ``-c`` is a top-level option, so it must come before the sub-command.
    """
    if getattr(sys, "frozen", False):
        cmd = [sys.executable]
    else:
        cmd = [sys.executable, "-m", "timetrack.agent"]
    if config_path:
        cmd += ["-c", config_path]
    return cmd + list(args)


def _popen_kwargs() -> dict:
    kwargs: dict = {
        "env": os.environ.copy(),
        "stdin": subprocess.DEVNULL,
        "close_fds": True,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return kwargs


def run_ui(*args: str, config_path: str | None = None, timeout: float | None = None) -> int:
    """Run an agent UI sub-command and wait for it; returns its exit code (-1 on error)."""
    try:
        proc = subprocess.run(
            agent_command(*args, config_path=config_path),
            timeout=timeout,
            check=False,
            **_popen_kwargs(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"[esstracker] could not start {args[:1]} window: {exc!r}")
        return -1
    return int(proc.returncode)


def spawn_ui(*args: str, config_path: str | None = None) -> subprocess.Popen | None:
    """Start an agent UI sub-command without waiting."""
    try:
        return subprocess.Popen(agent_command(*args, config_path=config_path), **_popen_kwargs())
    except OSError as exc:
        print(f"[esstracker] could not start {args[:1]} window: {exc!r}")
        return None


__all__ = ["agent_command", "run_ui", "spawn_ui"]
