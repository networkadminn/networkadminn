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


_popup_proc = None
# Re-offer the same version at most this often, even without "Later" (e.g. the
# user opened the download page but has not installed yet).
REOFFER_SECONDS = 6 * 3600
_offered_at: dict[str, float] = {}


def maybe_prompt_update(
    update: dict | None, *, server_url: str, state_dir: str, force: bool = False
) -> None:
    """Show the update popup (in a child process) if offered and not snoozed.

    The popup is a Tk window; this is called from the tracking thread, and Tk off
    the main thread crashes the macOS app, so it runs as ``esstracker-agent
    update-popup`` instead.
    """
    global _popup_proc
    if not isinstance(update, dict) or not update.get("latest") or not update.get("url"):
        return
    latest = str(update["latest"])
    if not force and _snoozed(state_dir, latest):
        return
    base = server_url.rstrip("/")
    url = base + str(update["url"]) if str(update["url"]).startswith("/") else str(update["url"])
    page = base + str(update.get("page") or "/download")
    if not url.startswith(base + "/"):
        url = page
    with _lock:
        if _popup_proc is not None and _popup_proc.poll() is None:
            return  # already showing
        if not force and time.time() - _offered_at.get(latest, 0.0) < REOFFER_SECONDS:
            return
        _offered_at[latest] = time.time()
        from .ui_process import spawn_ui

        _popup_proc = spawn_ui(
            "update-popup",
            "--latest", latest,
            "--url", url,
            "--page", page,
            "--state-dir", state_dir,
        )


def _same_publisher(installer: str) -> bool:
    """Windows: a signed agent only runs an installer signed by the same publisher.

    Unsigned (dev/CI-without-certificate) builds skip the check, since there is
    nothing to compare against.
    """
    import subprocess

    def subject(path: str) -> tuple[str, str]:
        script = (
            "$s = Get-AuthenticodeSignature -LiteralPath $env:ESS_SIG_PATH; "
            "Write-Output $s.Status; "
            "if ($s.SignerCertificate) { Write-Output $s.SignerCertificate.Subject }"
        )
        env = dict(os.environ, ESS_SIG_PATH=path)
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            timeout=60,
            env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        ).stdout.splitlines()
        status = out[0].strip() if out else ""
        subj = out[1].strip() if len(out) > 1 else ""
        return status, subj

    if not getattr(sys, "frozen", False):
        return True
    try:
        own_status, own_subject = subject(sys.executable)
    except Exception:
        own_status, own_subject = "", ""
    if own_status in ("NotSigned", "UnknownError") and not own_subject:
        return True  # unsigned build: nothing to compare against
    if own_status != "Valid":
        # Could not read our own signature (PowerShell blocked?): don't run the
        # installer; the caller opens the download page instead.
        return False
    try:
        status, subj = subject(installer)
    except Exception:
        return False
    return status == "Valid" and subj == own_subject


def run_popup(latest: str, url: str, page: str, state_dir: str) -> None:
    """The popup window itself (main thread of the ``update-popup`` process)."""
    import queue
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
    # Worker threads never touch Tk; they post results here for the Tk thread.
    results: queue.Queue[str] = queue.Queue()

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
        root.after(200, poll)

    def poll() -> None:
        try:
            outcome = results.get_nowait()
        except queue.Empty:
            root.after(200, poll)
            return
        if outcome != "ok":
            webbrowser.open(page)
        root.destroy()

    def _download_and_run() -> None:
        from .client import urlopen

        try:
            dest = os.path.join(tempfile.gettempdir(), f"esstracker-Setup-{latest}.exe")
            req = urllib.request.Request(
                url, headers={"User-Agent": f"esstracker-Agent/{__version__}"}
            )
            with urlopen(req, timeout=120) as resp, open(dest + ".part", "wb") as fh:
                while True:
                    chunk = resp.read(1 << 16)
                    if not chunk:
                        break
                    fh.write(chunk)
            os.replace(dest + ".part", dest)
            if not _same_publisher(dest):
                print("[esstracker] update rejected: installer signature does not match")
                os.remove(dest)
                results.put("bad-signature")
                return
            # The installer stops this tracker, upgrades it, and offers to relaunch.
            os.startfile(dest)  # type: ignore[attr-defined]
            results.put("ok")
        except Exception as exc:
            print(f"[esstracker] update download failed: {exc!r}")
            results.put("failed")

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
