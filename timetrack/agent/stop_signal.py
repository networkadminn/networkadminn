"""Graceful "please quit" signal between processes of the same user.

Used by ``esstracker-agent stop`` (the Windows installer runs it before an
upgrade) so the running agent can upload its buffer and tell the server it is
stopping, instead of being force-killed.

- Windows: a named event the agent waits on; the single-instance mutex tells
  when it has exited.
- Linux / macOS: SIGTERM to the PID in ``agent.lock``, but only while that file
  is really locked by a live agent (so a stale PID is never signalled).
"""

from __future__ import annotations

import os
import sys
import threading
import time
from typing import Callable

from .config import LOCK_PATH

STOP_EVENT_NAME = "Local\\esstracker-agent-stop"
MUTEX_NAME = "Local\\esstracker-agent-single-instance"
# Why the agent is asked to stop ("update", "uninstall", "quit"); read by the agent
# so the server can tell an upgrade (no admin email) from a real stop.
REASON_PATH = LOCK_PATH.parent / "stop-reason"
VALID_REASONS = ("quit", "update", "uninstall")


def read_stop_reason(default: str = "quit") -> str:
    """Reason left by ``esstracker-agent stop`` (consumed: the file is removed)."""
    try:
        reason = REASON_PATH.read_text(encoding="utf-8").strip()
        REASON_PATH.unlink()
    except OSError:
        return default
    return reason if reason in VALID_REASONS else default


def _write_reason(reason: str) -> None:
    try:
        REASON_PATH.parent.mkdir(parents=True, exist_ok=True)
        REASON_PATH.write_text(reason if reason in VALID_REASONS else "quit", encoding="utf-8")
    except OSError:
        pass


def watch_for_stop(on_stop: Callable[[], None]) -> None:
    """Windows: call ``on_stop`` (from a daemon thread) when a stop is requested."""
    if sys.platform != "win32":
        return
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    kernel32.CreateEventW.restype = wintypes.HANDLE
    kernel32.ResetEvent.argtypes = [wintypes.HANDLE]
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    # Manual-reset event, initially clear.
    handle = kernel32.CreateEventW(None, True, False, STOP_EVENT_NAME)
    if not handle:
        return
    kernel32.ResetEvent(handle)

    def _wait() -> None:
        INFINITE = 0xFFFFFFFF
        if kernel32.WaitForSingleObject(handle, INFINITE) == 0:  # WAIT_OBJECT_0
            on_stop()

    threading.Thread(target=_wait, daemon=True, name="esstracker-stop-watch").start()


def request_running_agent_stop(timeout: float = 25.0, reason: str = "quit") -> bool:
    """Ask the running agent to exit; True once no agent is running."""
    _write_reason(reason)
    try:
        if sys.platform == "win32":
            return _stop_windows(timeout)
        return _stop_posix(timeout)
    finally:
        # If no agent picked it up (none running / too old), don't leave it for later.
        if REASON_PATH.exists():
            read_stop_reason()


def _stop_windows(timeout: float) -> bool:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    kernel32.OpenEventW.restype = wintypes.HANDLE
    kernel32.OpenMutexW.restype = wintypes.HANDLE
    kernel32.SetEvent.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    EVENT_MODIFY_STATE = 0x0002
    SYNCHRONIZE = 0x00100000

    def _running() -> bool:
        h = kernel32.OpenMutexW(SYNCHRONIZE, False, MUTEX_NAME)
        if h:
            kernel32.CloseHandle(h)
            return True
        return False

    if not _running():
        return True
    event = kernel32.OpenEventW(EVENT_MODIFY_STATE, False, STOP_EVENT_NAME)
    if not event:
        return False  # an older agent without the stop watcher
    kernel32.SetEvent(event)
    kernel32.CloseHandle(event)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _running():
            return True
        time.sleep(0.25)
    return not _running()


def _stop_posix(timeout: float) -> bool:
    import fcntl
    import signal

    try:
        fh = open(LOCK_PATH, "a+", encoding="utf-8")
    except OSError:
        return True
    with fh:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            pass  # held by the running agent
        else:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            return True  # nobody holds the lock: not running
        fh.seek(0)
        try:
            pid = int(fh.read().strip() or 0)
        except ValueError:
            pid = 0
    if pid <= 0 or pid == os.getpid():
        return False
    try:
        # The agent's SIGTERM handler reads the reason file (see read_stop_reason).
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return True
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            return True
        time.sleep(0.25)
    return False


__all__ = ["request_running_agent_stop", "watch_for_stop"]
