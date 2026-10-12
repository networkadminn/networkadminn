"""Footer brand tray flyout — dark panel, all items, Euclidee powered-by footer."""

from __future__ import annotations

import json
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from ..analytics import humanize

if TYPE_CHECKING:
    from .tray import AgentTray

_lock = threading.Lock()
_open = False
_root: Any = None

# Footer brand (dark) palette
BG = "#12151c"
CARD = "#1a1f28"
INK = "#eef2f7"
MUTED = "#94a3b8"
ACCENT = "#1a9b63"
ACCENT_DARK = "#0b7a4b"
FOOTER_BG = "#0a1f14"
FOOTER_LINE = "#1e3d2a"
WARN = "#f59e0b"
BLUE = "#3b82f6"
PANEL_W = 360


def open_tray_flyout(tray: AgentTray) -> None:
    global _open, _root
    with _lock:
        if _root is not None:
            try:
                _root.after(0, lambda: _raise_window(tray))
                return
            except Exception:
                _root = None
        if _open:
            return
        _open = True

    def _run() -> None:
        global _open, _root
        try:
            _root = _build(tray)
            _root.mainloop()
        except Exception as exc:
            print(f"[esstracker] tray flyout failed: {exc!r}")
        finally:
            with _lock:
                _open = False
                _root = None

    threading.Thread(target=_run, daemon=True, name="esstracker-flyout").start()


def _fetch_tray_day_snapshot(agent) -> dict | None:
    try:
        data = agent.client.fetch_tray_day()
        if data and data.get("ok"):
            return data
    except AttributeError:
        pass
    except Exception as exc:
        print(f"[esstracker] tray-day via client failed: {exc!r}")

    base = (agent.config.server_url or "").rstrip("/")
    token = agent.config.api_token or ""
    if not base or not token:
        return None
    req = urllib.request.Request(
        f"{base}/api/v1/tray-day",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": "esstracker-Agent/tray-flyout",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data if data.get("ok") else None
    except urllib.error.HTTPError as exc:
        print(f"[esstracker] tray-day HTTP {exc.code}")
        return None
    except (urllib.error.URLError, OSError, ValueError) as exc:
        print(f"[esstracker] tray-day network error: {exc!r}")
        return None


def _place_near_tray(root) -> None:
    root.update_idletasks()
    w = max(root.winfo_width(), PANEL_W)
    h = root.winfo_height()
    px = root.winfo_pointerx()
    py = root.winfo_pointery()
    sw = root.winfo_screenwidth()
    sh = root.winfo_screenheight()
    x = max(8, min(px - w + 32, sw - w - 8))
    y = max(8, py - h - 12)
    if y + h > sh - 8:
        y = max(8, sh - h - 56)
    root.geometry(f"{w}x{h}+{x}+{y}")


def _raise_window(tray: AgentTray) -> None:
    if _root is None:
        return
    _root.deiconify()
    _place_near_tray(_root)
    _root.lift()
    _root.attributes("-topmost", True)
    if hasattr(_root, "_refresh"):
        _root._refresh()
    _root.after(200, lambda: _root.attributes("-topmost", False))


def _asset_path(name: str) -> Path | None:
    meipass = getattr(sys, "_MEIPASS", None)
    here = Path(__file__).resolve().parent
    candidates = [
        here / "assets" / name,
        here.parent.parent / "server" / "static" / "brand" / name,
        Path(meipass) / "timetrack" / "agent" / "assets" / name if meipass else None,
        Path(meipass) / "timetrack" / "server" / "static" / "brand" / name if meipass else None,
    ]
    for p in candidates:
        if p is not None and p.is_file():
            return p
    return None


def _load_photo(path: Path | None, size: tuple[int, int]):
    if path is None:
        return None
    try:
        from PIL import Image, ImageTk

        img = Image.open(path).convert("RGBA")
        img = img.resize(size, Image.Resampling.LANCZOS)
        return ImageTk.PhotoImage(img)
    except Exception:
        return None


def _build(tray: AgentTray):
    import tkinter as tk

    agent = tray.agent
    root = tk.Tk()
    root.title("esstracker")
    root.configure(bg=BG)
    root.resizable(False, False)
    root.overrideredirect(True)
    root.attributes("-topmost", True)

    ess_photo = _load_photo(_asset_path("ess-mark.png"), (36, 36))
    euc_photo = _load_photo(_asset_path("euclidee-logo.png"), (260, 56))

    shell = tk.Frame(root, bg=FOOTER_LINE, highlightthickness=0)
    shell.pack(fill="both", expand=True, padx=1, pady=1)
    body = tk.Frame(shell, bg=BG, padx=0, pady=0)
    body.pack(fill="both", expand=True)

    state: dict[str, Any] = {"day": None}

    # --- header ---
    head = tk.Frame(body, bg=CARD, padx=14, pady=12)
    head.pack(fill="x")
    head_row = tk.Frame(head, bg=CARD)
    head_row.pack(fill="x")
    if ess_photo:
        tk.Label(head_row, image=ess_photo, bg=CARD).pack(side="left")
        root._ess_photo = ess_photo
    tk.Label(
        head_row,
        text="esstracker",
        font=("Segoe UI", 14, "bold"),
        fg=INK,
        bg=CARD,
    ).pack(side="left", padx=(10, 0))
    tk.Button(
        head_row,
        text="×",
        command=root.destroy,
        font=("Segoe UI", 13),
        fg=MUTED,
        bg=CARD,
        activebackground=CARD,
        relief="flat",
        bd=0,
        padx=6,
        cursor="hand2",
    ).pack(side="right")
    widgets: dict[str, tk.Label] = {}
    pill_frame = tk.Frame(head_row, bg=CARD)
    pill_frame.pack(side="right", padx=(0, 6))
    widgets["pill"] = tk.Label(
        pill_frame,
        text="Tracking",
        font=("Segoe UI", 8, "bold"),
        fg=INK,
        bg=ACCENT,
        padx=10,
        pady=3,
    )
    widgets["pill"].pack()

    # --- scrollable menu ---
    canvas = tk.Canvas(body, bg=BG, highlightthickness=0, width=PANEL_W - 4)
    scroll = tk.Frame(body, bg=BG)
    menu = tk.Frame(canvas, bg=BG, padx=12, pady=8)
    menu.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.create_window((0, 0), window=menu, anchor="nw", width=PANEL_W - 28)
    canvas.pack(side="left", fill="both", expand=True)
    scroll.pack()

    def row(
        parent,
        icon: str,
        text: str,
        *,
        command: Callable[[], None] | None = None,
        check: tk.BooleanVar | None = None,
        accent_row: bool = False,
    ) -> tk.Frame:
        bg = "#243044" if accent_row else BG
        fr = tk.Frame(parent, bg=bg, pady=6, padx=8)
        fr.pack(fill="x", pady=1)
        tk.Label(fr, text=icon, font=("Segoe UI", 11), fg=ACCENT, bg=bg, width=2).pack(side="left")
        if check is not None:
            tk.Checkbutton(
                fr,
                text=text,
                variable=check,
                command=command,
                bg=bg,
                fg=INK,
                activebackground=bg,
                selectcolor=bg,
                font=("Segoe UI", 10),
            ).pack(side="left", anchor="w")
        elif command is not None:
            btn = tk.Button(
                fr,
                text=text,
                command=command,
                bg=bg,
                fg=INK,
                activebackground="#2d3748",
                activeforeground=INK,
                relief="flat",
                anchor="w",
                font=("Segoe UI", 10),
                cursor="hand2",
            )
            btn.pack(side="left", fill="x", expand=True)
        else:
            tk.Label(fr, text=text, font=("Segoe UI", 10), fg=INK, bg=bg, anchor="w").pack(
                side="left", fill="x", expand=True
            )
        return fr

    def dashed_rule(parent) -> None:
        tk.Frame(parent, bg="#334155", height=1).pack(fill="x", pady=10, padx=4)

    private_var = tk.BooleanVar(value=agent.private)

    def refresh_pill() -> None:
        if agent.user_offline:
            widgets["pill"].config(text="Offline", bg="#64748b")
        elif agent.private:
            widgets["pill"].config(text="Private", bg=WARN)
        elif agent.online:
            widgets["pill"].config(text="Tracking", bg=ACCENT)
        else:
            widgets["pill"].config(text="Offline", bg="#64748b")

    def toggle_private() -> None:
        tray._toggle_private()
        private_var.set(agent.private)
        refresh_pill()
        refresh_lines()

    def status_text() -> str:
        if agent.user_offline:
            return "Offline (idle)"
        if agent.private:
            return "Private Time ON"
        if agent.online:
            return "Tracking"
        return "Offline"

    def role_text() -> str:
        r = agent.role or "—"
        if agent.online:
            r += f" · Shots {'ON' if agent.shots_enabled else 'OFF'}"
        return r

    def now_text() -> str:
        app, title = agent._last_window if hasattr(agent, "_last_window") else ("", "")
        if not app:
            return "—"
        line = f"{app}"
        if title:
            line += f" — {title[:50]}"
        return line[:72]

    info_labels: dict[str, tk.Label] = {}

    def info_row(parent, icon: str, key: str, initial: str) -> None:
        fr = tk.Frame(parent, bg=BG, pady=5, padx=8)
        fr.pack(fill="x")
        tk.Label(fr, text=icon, font=("Segoe UI", 11), fg=ACCENT, bg=BG, width=2).pack(side="left")
        lbl = tk.Label(fr, text=initial, font=("Segoe UI", 10), fg=INK, bg=BG, anchor="w", wraplength=280)
        lbl.pack(side="left", fill="x", expand=True)
        info_labels[key] = lbl

    info_row(menu, "●", "status", f"Status: {status_text()}")
    info_row(menu, "◉", "user", f"User: {agent.display_name or agent.username or '—'}")
    info_row(menu, "◇", "org", f"Org: {agent.company_name or '—'}")
    info_row(menu, "◎", "role", f"Role: {role_text()}")
    info_row(menu, "▣", "now", f"Now: {now_text()}")

    day_extra = tk.Label(
        menu,
        text="",
        font=("Segoe UI", 9),
        fg=MUTED,
        bg=BG,
        anchor="w",
        wraplength=320,
        justify="left",
    )
    day_extra.pack(fill="x", padx=8, pady=(4, 0))

    dashed_rule(menu)

    row(
        menu,
        "☐",
        "Private Time",
        check=private_var,
        command=toggle_private,
    )

    def open_my_day() -> None:
        tray._open_dashboard()

    open_row = tk.Frame(menu, bg=ACCENT_DARK, pady=8, padx=8)
    open_row.pack(fill="x", pady=(6, 4))
    tk.Button(
        open_row,
        text="  Open My Day",
        command=open_my_day,
        bg=ACCENT_DARK,
        fg=INK,
        activebackground=ACCENT,
        activeforeground=INK,
        relief="flat",
        font=("Segoe UI", 11, "bold"),
        anchor="w",
        cursor="hand2",
    ).pack(side="left", fill="x", expand=True)
    tk.Label(open_row, text="↗", font=("Segoe UI", 11), fg=INK, bg=ACCENT_DARK).pack(side="right")

    row(menu, "↻", "Sync now", command=lambda: (tray._flush_now(), refresh_all()))
    row(menu, "⚙", "Preferences…", command=tray._open_preferences)
    dashed_rule(menu)
    if agent.tray_logout_allowed:
        row(menu, "⏻", "Sign out", command=tray._sign_out)
    if agent.tray_quit_allowed:
        row(menu, "⏹", "Quit", command=tray._menu_quit)

    # --- footer brand ---
    foot = tk.Frame(body, bg=FOOTER_BG, pady=14, padx=12)
    foot.pack(fill="x", side="bottom")
    if euc_photo:
        tk.Label(foot, image=euc_photo, bg=FOOTER_BG).pack()
        root._euc_photo = euc_photo
    else:
        tk.Label(
            foot,
            text="EUCLIDEE",
            font=("Segoe UI", 11, "bold"),
            fg=ACCENT,
            bg=FOOTER_BG,
        ).pack()
    tk.Label(
        foot,
        text="Powered by Euclidee Software Solutions",
        font=("Segoe UI", 9),
        fg=MUTED,
        bg=FOOTER_BG,
    ).pack(pady=(8, 0))

    def refresh_lines() -> None:
        info_labels["status"].config(text=f"Status: {status_text()}")
        info_labels["user"].config(text=f"User: {agent.display_name or agent.username or '—'}")
        info_labels["org"].config(text=f"Org: {agent.company_name or '—'}")
        info_labels["role"].config(text=f"Role: {role_text()}")
        info_labels["now"].config(text=f"Now: {now_text()}")
        private_var.set(agent.private)
        refresh_pill()

    def apply_day(day: dict | None) -> None:
        state["day"] = day
        refresh_lines()
        if not day:
            day_extra.config(text="")
            return
        prod = day.get("dt_productivity")
        eff = day.get("dt_effectiveness")
        desk = humanize(day.get("desktime_seconds") or 0)
        arr = day.get("arrived") or "—"
        day_extra.config(
            text=(
                f"Today · Arrived {arr} · Desk {desk} · "
                f"Productivity {prod}% · Effectiveness {eff}%"
            )
        )

    def refresh_all() -> None:
        refresh_lines()
        data = _fetch_tray_day_snapshot(agent)
        root.after(0, lambda: apply_day(data))

    def fetch_async() -> None:
        data = _fetch_tray_day_snapshot(agent)
        root.after(0, lambda: apply_day(data))

    root._refresh = refresh_all
    refresh_pill()
    refresh_lines()
    threading.Thread(target=fetch_async, daemon=True).start()

    root.update_idletasks()
    root.geometry(f"{PANEL_W}x{min(root.winfo_reqheight(), 720)}")
    _place_near_tray(root)
    root.protocol("WM_DELETE_WINDOW", root.destroy)
    return root


__all__ = ["open_tray_flyout"]
