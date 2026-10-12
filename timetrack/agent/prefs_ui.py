"""Preferences window opened from the tray menu."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time
from typing import TYPE_CHECKING

from .. import __version__
from . import autostart
from .config import LOCK_PATH, ensure_data_dirs

if TYPE_CHECKING:
    from .agent import Agent

_lock = threading.Lock()
_spawning = False


def _prefs_lock_path() -> str:
    ensure_data_dirs()
    return str(LOCK_PATH.parent / "preferences.lock")


def _prefs_already_open() -> bool:
    path = _prefs_lock_path()
    try:
        pid = int(open(path, encoding="utf-8").read().strip())
    except (OSError, ValueError):
        return False
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            kernel32.CloseHandle(handle)
            return False
        kernel32.CloseHandle(handle)
        return int(code.value) == STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _spawn_prefs_command() -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, "preferences"]
    agent_bin = shutil.which("esstracker-agent")
    if agent_bin:
        return [agent_bin, "preferences"]
    return [sys.executable, "-m", "timetrack.agent", "preferences"]


def open_preferences(agent: Agent) -> None:
    """Open preferences in a child process (Tk must run on that process main thread)."""
    global _spawning
    with _lock:
        if _spawning or _prefs_already_open():
            return
        _spawning = True
    try:
        cmd = _spawn_prefs_command()
        if agent.config.config_path:
            cmd.extend(["-c", agent.config.config_path])
        subprocess.Popen(
            cmd,
            env=os.environ.copy(),
            start_new_session=True,
            close_fds=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
    except Exception as exc:
        print(f"[esstracker] could not open preferences: {exc!r}")
    finally:
        with _lock:
            _spawning = False


def run_preferences_window(agent: Agent) -> None:
    """Run the Tk window on the main thread (``esstracker-agent preferences``)."""
    path = _prefs_lock_path()
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(str(os.getpid()))
    except OSError as exc:
        print(f"[esstracker] preferences lock failed: {exc!r}")
        return
    try:
        _window(agent)
    except Exception as exc:
        print(f"[esstracker] preferences failed: {exc!r}")
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def _window(agent: Agent) -> None:
    import tkinter as tk

    bg, fg, muted, green, red = "#ffffff", "#0f172a", "#64748b", "#1f9d6c", "#dc2626"
    root = tk.Tk()
    root.title("esstracker Preferences")
    root.resizable(False, False)
    root.configure(bg=bg)
    root.attributes("-topmost", True)

    frame = tk.Frame(root, padx=22, pady=18, bg=bg)
    frame.pack(fill="both", expand=True)

    def heading(text: str) -> None:
        tk.Label(frame, text=text, font=("Segoe UI", 11, "bold"), bg=bg, fg=fg).pack(
            anchor="w", pady=(10, 4)
        )

    def info(text: str) -> tk.Label:
        lbl = tk.Label(frame, text=text, font=("Segoe UI", 10), bg=bg, fg=muted, justify="left")
        lbl.pack(anchor="w")
        return lbl

    tk.Label(frame, text="Preferences", font=("Segoe UI", 14, "bold"), bg=bg, fg=fg).pack(anchor="w")

    heading("Account")
    info(f"User: {agent.display_name or agent.username or '—'}")
    info(f"Organization: {agent.company_name or '—'}")
    info(f"Server: {agent.config.server_url}")
    info(f"App version: {__version__}")

    heading("Startup")
    startup_var = tk.BooleanVar(value=autostart.is_enabled())
    startup_msg = info("")

    def toggle_startup() -> None:
        try:
            autostart.set_enabled(startup_var.get())
            startup_msg.config(text="Saved.", fg=green)
        except Exception as exc:
            startup_var.set(autostart.is_enabled())
            startup_msg.config(text=f"Could not change: {exc}", fg=red)

    tk.Checkbutton(
        frame, text="Launch esstracker when I log in", variable=startup_var,
        command=toggle_startup, bg=bg, fg=fg, activebackground=bg, selectcolor=bg,
        font=("Segoe UI", 10), state="normal" if autostart.is_supported() else "disabled",
    ).pack(anchor="w", before=startup_msg)

    heading("Connection")
    conn_msg = info("Online" if agent.online else "Offline")
    conn_msg.config(fg=green if agent.online else red)
    update_msg = info("")

    def ping_now() -> None:
        conn_msg.config(text="Checking…", fg=muted)

        def work() -> None:
            t0 = time.monotonic()
            data = agent.client.ping()
            ms = int((time.monotonic() - t0) * 1000)
            if data:
                text, color = f"Connected · {ms} ms", green
            else:
                text, color = "Server not reachable — check your internet", red
            root.after(0, lambda: conn_msg.config(text=text, fg=color))

        threading.Thread(target=work, daemon=True).start()

    def check_updates() -> None:
        update_msg.config(text="Checking for updates…", fg=muted)

        def work() -> None:
            data = agent.client.ping()
            upd = (data or {}).get("client_update")
            if data is None:
                text, color = "Could not reach the server.", red
            elif upd:
                text, color = f"Version {upd.get('latest')} is available.", green
                from .update_ui import maybe_prompt_update

                maybe_prompt_update(
                    upd,
                    server_url=agent.config.server_url,
                    state_dir=os.path.dirname(os.path.abspath(agent.config.buffer_path)),
                    force=True,
                )
            else:
                text, color = "You have the latest version.", green
            root.after(0, lambda: update_msg.config(text=text, fg=color))

        threading.Thread(target=work, daemon=True).start()

    row = tk.Frame(frame, bg=bg)
    row.pack(anchor="w", pady=(8, 0), before=update_msg)
    tk.Button(row, text="Test connection", command=ping_now, width=15).pack(side="left", padx=(0, 8))
    tk.Button(row, text="Check for updates", command=check_updates, width=16).pack(side="left")

    tk.Button(
        frame, text="Close", width=10, command=root.destroy,
        bg=green, fg="#ffffff", activebackground="#178a5d", relief="flat",
    ).pack(anchor="e", pady=(16, 0))

    root.update_idletasks()
    w, h = root.winfo_width(), root.winfo_height()
    root.geometry(f"+{(root.winfo_screenwidth() - w) // 2}+{(root.winfo_screenheight() - h) // 3}")
    root.mainloop()


__all__ = ["open_preferences", "run_preferences_window"]
