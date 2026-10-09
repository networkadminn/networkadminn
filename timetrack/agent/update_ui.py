"""Update-available popup for the employee agent.

The server's ping reply carries ``client_update`` when a newer installer is
published. We show one small window per version; "Later" snoozes it for a day.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

from .. import __version__

SNOOZE_SECONDS = 24 * 3600

_lock = threading.Lock()
_showing = False


def _snooze_file(state_dir: str) -> Path:
    return Path(state_dir) / "update_snooze.json"


def _snoozed(state_dir: str, version: str) -> bool:
    try:
        data = json.loads(_snooze_file(state_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return data.get("version") == version and float(data.get("until", 0)) > time.time()


def _snooze(state_dir: str, version: str) -> None:
    try:
        _snooze_file(state_dir).write_text(
            json.dumps({"version": version, "until": time.time() + SNOOZE_SECONDS}),
            encoding="utf-8",
        )
    except OSError:
        pass


def maybe_prompt_update(update: dict | None, *, server_url: str, state_dir: str) -> None:
    """Show the popup in its own thread if an update is offered and not snoozed."""
    global _showing
    if not isinstance(update, dict) or not update.get("latest") or not update.get("url"):
        return
    latest = str(update["latest"])
    if _snoozed(state_dir, latest):
        return
    with _lock:
        if _showing:
            return
        _showing = True
    base = server_url.rstrip("/")
    url = base + str(update["url"]) if str(update["url"]).startswith("/") else str(update["url"])
    page = base + str(update.get("page") or "/download")
    if not url.startswith(base + "/"):
        url = page

    def _run() -> None:
        global _showing
        try:
            _popup(latest, url, page, state_dir)
        except Exception as exc:
            print(f"[esstracker] update popup failed: {exc!r}")
        finally:
            with _lock:
                _showing = False

    threading.Thread(target=_run, daemon=True, name="esstracker-update").start()


def _popup(latest: str, url: str, page: str, state_dir: str) -> None:
    import tkinter as tk

    root = tk.Tk()
    root.title("esstracker update")
    root.resizable(False, False)
    root.attributes("-topmost", True)
    try:
        root.attributes("-toolwindow", True)
    except tk.TclError:
        pass

    frame = tk.Frame(root, padx=22, pady=18, bg="#ffffff")
    frame.pack(fill="both", expand=True)
    tk.Label(
        frame, text="A new version of esstracker is available",
        font=("Segoe UI", 12, "bold"), bg="#ffffff", fg="#0f172a",
    ).pack(anchor="w")
    tk.Label(
        frame, text=f"You have {__version__}. Latest is {latest}.",
        font=("Segoe UI", 10), bg="#ffffff", fg="#475569",
    ).pack(anchor="w", pady=(4, 2))
    status = tk.Label(
        frame, text="Your tracked time is kept during the update.",
        font=("Segoe UI", 9), bg="#ffffff", fg="#64748b",
    )
    status.pack(anchor="w", pady=(0, 12))

    buttons = tk.Frame(frame, bg="#ffffff")
    buttons.pack(anchor="e")

    def later() -> None:
        _snooze(state_dir, latest)
        root.destroy()

    def update_now() -> None:
        if sys.platform != "win32" or not url.lower().endswith(".exe"):
            webbrowser.open(url)
            root.destroy()
            return
        btn_update.config(state="disabled")
        btn_later.config(state="disabled")
        status.config(text="Downloading update…")
        threading.Thread(target=_download_and_run, daemon=True).start()

    def _download_and_run() -> None:
        try:
            dest = os.path.join(tempfile.gettempdir(), f"esstracker-Setup-{latest}.exe")
            req = urllib.request.Request(
                url, headers={"User-Agent": f"esstracker-Agent/{__version__}"}
            )
            with urllib.request.urlopen(req, timeout=120) as resp, open(dest + ".part", "wb") as fh:
                while True:
                    chunk = resp.read(1 << 16)
                    if not chunk:
                        break
                    fh.write(chunk)
            os.replace(dest + ".part", dest)
            # The installer closes this tracker, upgrades it, and offers to relaunch.
            os.startfile(dest)  # type: ignore[attr-defined]
            root.after(0, root.destroy)
        except Exception as exc:
            print(f"[esstracker] update download failed: {exc!r}")
            webbrowser.open(page)
            root.after(0, root.destroy)

    btn_later = tk.Button(buttons, text="Later", width=10, command=later)
    btn_later.pack(side="left", padx=(0, 8))
    btn_update = tk.Button(
        buttons, text="Update now", width=12, command=update_now,
        bg="#1f9d6c", fg="#ffffff", activebackground="#178a5d", relief="flat",
    )
    btn_update.pack(side="left")

    root.protocol("WM_DELETE_WINDOW", later)
    root.update_idletasks()
    w, h = root.winfo_width(), root.winfo_height()
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    root.geometry(f"+{sw - w - 24}+{sh - h - 72}")
    root.mainloop()


__all__ = ["maybe_prompt_update"]
