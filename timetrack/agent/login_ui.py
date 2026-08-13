"""First-run login window for the employee agent.

Frozen .deb builds ship their own Python (3.11) and cannot safely import the
host's PyGObject ``gi`` (built for system Python 3.8/3.10/3.12). Prefer
**Tkinter** for the sign-in dialog; fall back to GTK only for non-frozen
dev installs, then CLI.
"""

from __future__ import annotations

import os
import sys
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Callable

from .client import ServerClient
from .config import AgentConfig, DEFAULT_SERVER_URL, save_agent_config


def _is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False)) or hasattr(sys, "_MEIPASS")


def _logo_path() -> str | None:
    env = os.environ.get("TIMEFORGE_ICON")
    candidates = [
        Path(env) if env else None,
        Path("/usr/share/icons/hicolor/128x128/apps/timeforge.png"),
        Path("/usr/share/icons/hicolor/256x256/apps/timeforge.png"),
        Path(__file__).resolve().parent / "assets" / "timeforge-mark.png",
    ]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.insert(
            1, Path(meipass) / "timetrack" / "agent" / "assets" / "timeforge-mark.png"
        )
    for p in candidates:
        if p is not None and p.is_file():
            return str(p)
    return None


def _server_url(cfg: AgentConfig) -> str:
    return (cfg.server_url or DEFAULT_SERVER_URL).rstrip("/")


def _save_login(cfg: AgentConfig, server: str, data: dict) -> None:
    cfg.server_url = server
    cfg.api_token = str(data["api_token"])
    save_agent_config(cfg)


def show_login(
    cfg: AgentConfig,
    *,
    on_success: Callable[[AgentConfig], None] | None = None,
) -> bool:
    """Show modal login. Returns True if signed in (config saved)."""
    # Frozen packages: never touch system gi (ABI mismatch → circular import).
    if not _is_frozen():
        if _try_gtk_login(cfg, on_success=on_success):
            return True
    if _tk_login(cfg, on_success=on_success):
        return True
    return _cli_login(cfg)


def _try_gtk_login(
    cfg: AgentConfig,
    *,
    on_success: Callable[[AgentConfig], None] | None = None,
) -> bool:
    """Dev / non-frozen only. Returns True if login completed via GTK."""
    try:
        dist = "/usr/lib/python3/dist-packages"
        if dist not in sys.path and os.path.isdir(dist):
            sys.path.append(dist)
        import gi

        gi.require_version("Gtk", "3.0")
        gi.require_version("GdkPixbuf", "2.0")
        from gi.repository import GdkPixbuf, Gtk
    except Exception:
        return False

    result = {"ok": False}
    server = _server_url(cfg)

    win = Gtk.Window(title="timeforge — Sign in")
    win.set_default_size(400, 480)
    win.set_resizable(False)
    win.set_position(Gtk.WindowPosition.CENTER)

    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
    box.set_margin_top(28)
    box.set_margin_bottom(28)
    box.set_margin_start(28)
    box.set_margin_end(28)
    win.add(box)

    logo = _logo_path()
    if logo:
        try:
            pix = GdkPixbuf.Pixbuf.new_from_file_at_scale(logo, 64, 64, True)
            box.pack_start(Gtk.Image.new_from_pixbuf(pix), False, False, 0)
        except Exception:
            pass

    title = Gtk.Label(label="timeforge")
    title.set_markup('<span size="xx-large" weight="bold">timeforge</span>')
    box.pack_start(title, False, False, 0)
    box.pack_start(Gtk.Label(label="Sign in with your work account"), False, False, 0)

    user_entry = Gtk.Entry()
    user_entry.set_placeholder_text("Username")
    pass_entry = Gtk.Entry()
    pass_entry.set_placeholder_text("Password")
    pass_entry.set_visibility(False)
    box.pack_start(user_entry, False, False, 0)
    box.pack_start(pass_entry, False, False, 0)

    err = Gtk.Label(label="")
    err.set_line_wrap(True)
    box.pack_start(err, False, False, 0)
    btn = Gtk.Button(label="Sign in & start tracking")
    box.pack_start(btn, False, False, 0)

    def do_login(*_args) -> None:
        username = user_entry.get_text().strip()
        password = pass_entry.get_text()
        if not username or not password:
            err.set_text("Enter your username and password.")
            return
        btn.set_sensitive(False)
        client = ServerClient(server, "")
        data = client.login(username, password) or {}
        if not data.get("api_token"):
            err.set_text(str(data.get("error") or "Sign-in failed"))
            btn.set_sensitive(True)
            return
        _save_login(cfg, server, data)
        result["ok"] = True
        win.destroy()
        Gtk.main_quit()
        if on_success:
            on_success(cfg)

    btn.connect("clicked", do_login)
    pass_entry.connect("activate", do_login)
    win.connect("destroy", lambda *_: Gtk.main_quit())
    win.show_all()
    user_entry.grab_focus()
    Gtk.main()
    return bool(result["ok"])


def _tk_login(
    cfg: AgentConfig,
    *,
    on_success: Callable[[AgentConfig], None] | None = None,
) -> bool:
    """Tkinter login — works inside the frozen .deb on Ubuntu 20/22/24."""
    try:
        root = tk.Tk()
    except Exception as exc:
        print(f"[timeforge] Tk login unavailable ({type(exc).__name__}: {exc})")
        return False

    result = {"ok": False}
    server = _server_url(cfg)

    root.title("timeforge — Sign in")
    root.resizable(False, False)
    root.configure(bg="#E8F3EC")

    # Center on screen
    w, h = 400, 460
    root.update_idletasks()
    x = (root.winfo_screenwidth() - w) // 2
    y = (root.winfo_screenheight() - h) // 2
    root.geometry(f"{w}x{h}+{x}+{y}")

    card = tk.Frame(root, bg="#FFFFFF", highlightbackground="#D5E5DB", highlightthickness=1)
    card.place(relx=0.5, rely=0.5, anchor="center", width=340, height=400)

    logo = _logo_path()
    if logo:
        try:
            from PIL import Image, ImageTk

            img = Image.open(logo).convert("RGBA").resize((64, 64))
            photo = ImageTk.PhotoImage(img)
            lab = tk.Label(card, image=photo, bg="#FFFFFF")
            lab.image = photo  # keep ref
            lab.pack(pady=(28, 8))
        except Exception:
            tk.Label(card, text="TF", font=("Sans", 18, "bold"), fg="#0B7A4B", bg="#FFFFFF").pack(
                pady=(28, 8)
            )
    else:
        tk.Label(card, text="TF", font=("Sans", 18, "bold"), fg="#0B7A4B", bg="#FFFFFF").pack(
            pady=(28, 8)
        )

    tk.Label(
        card, text="timeforge", font=("Sans", 20, "bold"), fg="#0F291C", bg="#FFFFFF"
    ).pack()
    tk.Label(
        card,
        text="Sign in with your work account",
        font=("Sans", 10),
        fg="#5F7A6A",
        bg="#FFFFFF",
    ).pack(pady=(4, 16))

    form = tk.Frame(card, bg="#FFFFFF")
    form.pack(fill="x", padx=28)

    tk.Label(form, text="USERNAME", font=("Sans", 8, "bold"), fg="#3D5A4C", bg="#FFFFFF").pack(
        anchor="w"
    )
    user_var = tk.StringVar()
    user_entry = ttk.Entry(form, textvariable=user_var, font=("Sans", 11))
    user_entry.pack(fill="x", ipady=6, pady=(2, 10))

    tk.Label(form, text="PASSWORD", font=("Sans", 8, "bold"), fg="#3D5A4C", bg="#FFFFFF").pack(
        anchor="w"
    )
    pass_var = tk.StringVar()
    pass_entry = ttk.Entry(form, textvariable=pass_var, show="•", font=("Sans", 11))
    pass_entry.pack(fill="x", ipady=6, pady=(2, 8))

    err_var = tk.StringVar()
    err_lab = tk.Label(
        form, textvariable=err_var, font=("Sans", 9), fg="#C0392B", bg="#FFFFFF", wraplength=280
    )
    err_lab.pack(anchor="w", pady=(0, 6))

    status = {"busy": False}

    def do_login(_event=None) -> None:
        if status["busy"]:
            return
        username = user_var.get().strip()
        password = pass_var.get()
        err_var.set("")
        if not username or not password:
            err_var.set("Enter your username and password.")
            return
        status["busy"] = True
        btn.configure(state="disabled", text="Signing in…")
        root.update_idletasks()
        try:
            client = ServerClient(server, "")
            data = client.login(username, password) or {}
        except Exception as exc:
            err_var.set(f"Could not reach server: {exc}")
            status["busy"] = False
            btn.configure(state="normal", text="Sign in & start tracking")
            return
        if not data.get("api_token"):
            err_var.set(str(data.get("error") or "Sign-in failed — check credentials"))
            status["busy"] = False
            btn.configure(state="normal", text="Sign in & start tracking")
            return
        _save_login(cfg, server, data)
        result["ok"] = True
        if on_success:
            on_success(cfg)
        root.destroy()

    btn = tk.Button(
        form,
        text="Sign in & start tracking",
        command=do_login,
        bg="#0B7A4B",
        fg="#FFFFFF",
        activebackground="#09663F",
        activeforeground="#FFFFFF",
        font=("Sans", 11, "bold"),
        relief="flat",
        cursor="hand2",
        pady=10,
    )
    btn.pack(fill="x", pady=(4, 8))

    tk.Label(
        form,
        text="Same username and password as the web dashboard",
        font=("Sans", 8),
        fg="#8FA89A",
        bg="#FFFFFF",
    ).pack()

    pass_entry.bind("<Return>", do_login)
    user_entry.bind("<Return>", lambda e: pass_entry.focus_set())
    user_entry.focus_set()

    root.protocol("WM_DELETE_WINDOW", root.destroy)
    try:
        root.mainloop()
    except Exception as exc:
        print(f"[timeforge] Tk login failed ({exc})")
        return False
    return bool(result["ok"])


def _cli_login(cfg: AgentConfig) -> bool:
    """Fallback when no GUI toolkit is available."""
    print("timeforge — Sign in")
    print("(GUI login unavailable — type credentials below)")
    try:
        username = input("Username: ").strip()
        import getpass

        password = getpass.getpass("Password: ")
    except (EOFError, KeyboardInterrupt):
        return False
    url = _server_url(cfg)
    client = ServerClient(url, "")
    data = client.login(username, password) or {}
    if not data.get("api_token"):
        print(data.get("error") or "Sign-in failed")
        return False
    _save_login(cfg, url, data)
    print(f"Signed in as {data.get('name') or username}. Config saved.")
    return True


__all__ = ["show_login"]
