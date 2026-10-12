"""DeskTime-style system tray for the employee agent.

Shows tracking status, employee name, company, Private Time toggle,
and shortcuts to open the dashboard / quit.

Linux / GNOME notes
-------------------
- Classic XEmbed (pystray xorg) often fails to dock under GNOME — menu appears broken.
- AppIndicator works on Ubuntu GNOME; the menu opens on *left-click* (right-click is a no-op).
- AppIndicator/GTK need system ``gi`` (PyGObject). Venvs usually lack it, so we add
  ``/usr/lib/python3/dist-packages`` to ``sys.path`` before importing pystray.
"""

from __future__ import annotations

import os
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from .agent import Agent

_DIST_PATHS = (
    "/usr/lib/python3/dist-packages",
    "/usr/lib/python3.12/dist-packages",
    "/usr/lib/python3.11/dist-packages",
    "/usr/lib/python3.10/dist-packages",
    "/usr/lib/python3.8/dist-packages",
)


def _patch_frozen_pystray_menu() -> None:
    """PyInstaller: pystray.Menu.items mis-detects MenuItem as a dynamic factory."""
    if not getattr(sys, "frozen", False):
        return
    try:
        from pystray import _base

        def _items(self):
            return self._items

        _base.Menu.items = property(_items)
    except Exception:
        pass


def _ensure_system_gi() -> None:
    """Make system PyGObject (gi) importable from a *venv* matching system Python.

    Never do this for frozen binaries — host ``gi`` is built for a different
    Python ABI and causes ``cannot import name '_gi'`` circular import crashes.
    """
    if getattr(sys, "frozen", False) or hasattr(sys, "_MEIPASS"):
        return
    for path in _DIST_PATHS:
        if path not in sys.path and os.path.isdir(path):
            sys.path.append(path)


def _lanczos():
    """Pillow 9-compatible LANCZOS filter."""
    from PIL import Image

    return getattr(getattr(Image, "Resampling", Image), "LANCZOS", Image.LANCZOS)


_ESS_MARK: object | None = None


def _ess_mark_path() -> Path | None:
    env = os.environ.get("ESSTRACKER_ICON")
    candidates = [
        Path(env) if env else None,
        Path("/usr/share/icons/hicolor/64x64/apps/esstracker.png"),
        Path("/usr/share/icons/hicolor/48x48/apps/esstracker.png"),
        Path("/usr/share/pixmaps/esstracker.png"),
        Path(__file__).resolve().parent / "assets" / "ess-mark.png",
    ]
    # PyInstaller onefile extracts to _MEIPASS
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.insert(
            1, Path(meipass) / "timetrack" / "agent" / "assets" / "ess-mark.png"
        )
    for p in candidates:
        if p is not None and p.is_file():
            return p
    return None


def _load_ess_base():
    """Load ESS logo once (RGBA)."""
    global _ESS_MARK
    if _ESS_MARK is not None:
        return _ESS_MARK
    from PIL import Image

    path = _ess_mark_path()
    if path is None:
        _ESS_MARK = False
        return None
    try:
        img = Image.open(path).convert("RGBA")
        img = img.resize((64, 64), _lanczos())
        _ESS_MARK = img
        return img
    except Exception:
        _ESS_MARK = False
        return None


def _make_icon(color: tuple[int, int, int], private: bool = False):
    """Round ESS mark with colored status ring."""
    from PIL import Image, ImageDraw

    base = _load_ess_base()
    size = 64
    if base is not None and base is not False:
        img = base.copy().resize((size, size), _lanczos())
        draw = ImageDraw.Draw(img)
        # Status ring on the circle edge
        draw.ellipse((1, 1, size - 2, size - 2), outline=color + (255,), width=3)
        if private:
            draw.ellipse((42, 42, 60, 60), fill=(245, 158, 11, 255))
            draw.ellipse((46, 46, 56, 56), fill=(255, 255, 255, 230))
        return img

    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((2, 2, 61, 61), fill=color + (255,))
    draw.ellipse((12, 12, 51, 51), fill=(255, 255, 255, 235))
    draw.text((18, 20), "ESS", fill=color + (255,))
    if private:
        draw.ellipse((40, 40, 58, 58), fill=(245, 158, 11, 255))
    return img


def tray_available() -> bool:
    """True when Pillow + a usable pystray backend can load."""
    try:
        from PIL import Image  # noqa: F401

        _ensure_system_gi()
        saved = os.environ.pop("PYSTRAY_BACKEND", None)
        try:
            return _pick_backend() is not None
        finally:
            if saved is not None:
                os.environ["PYSTRAY_BACKEND"] = saved
    except Exception:
        return False


def _pick_backend() -> str | None:
    """Choose a tray backend that imports without crashing.

    Windows uses the win32 notification-area backend.
    Ubuntu notes
    ------------
    - AppIndicator needs system ``gi`` matching the runtime Python. A frozen
      binary built on 24.04 may not load 20.04/22.04 PyGObject — then we fall
      back to the X11 ``xorg`` backend (works on Xorg sessions).
    - On GNOME/Wayland try AppIndicator first; always keep ``xorg`` as a last resort.
    """
    if sys.platform == "win32":
        preferred = os.environ.get("PYSTRAY_BACKEND", "win32")
        os.environ["PYSTRAY_BACKEND"] = preferred
        try:
            for mod in list(sys.modules):
                if mod == "pystray" or mod.startswith("pystray."):
                    del sys.modules[mod]
            import pystray

            _ = pystray.Icon
            return preferred
        except Exception as exc:
            print(f"[esstracker] tray backend {preferred!r} unavailable: {exc!r}")
            os.environ.pop("PYSTRAY_BACKEND", None)
            return None

    _ensure_system_gi()
    desktop = (os.environ.get("XDG_CURRENT_DESKTOP") or "").lower()
    session = (os.environ.get("XDG_SESSION_TYPE") or "").lower()
    order: list[str] = []
    preferred = os.environ.get("PYSTRAY_BACKEND")
    if preferred:
        order.append(preferred)
    if "gnome" in desktop or session == "wayland":
        order.extend(["appindicator", "gtk", "xorg"])
    else:
        # Xfce / MATE / Cinnamon / plain X11 — xorg is most portable across Ubuntu releases
        order.extend(["xorg", "appindicator", "gtk"])
    seen: set[str] = set()
    for backend in order:
        if backend in seen:
            continue
        seen.add(backend)
        os.environ["PYSTRAY_BACKEND"] = backend
        try:
            for mod in list(sys.modules):
                if mod == "pystray" or mod.startswith("pystray."):
                    del sys.modules[mod]
            import pystray

            _ = pystray.Icon
            return backend
        except Exception as exc:
            print(f"[esstracker] tray backend {backend!r} unavailable: {exc!r}")
            continue
    os.environ.pop("PYSTRAY_BACKEND", None)
    return None


class AgentTray:
    """Runs pystray in the main thread; agent loop runs elsewhere."""

    def __init__(self, agent: Agent):
        self.agent = agent
        self._icon = None
        self._backend = ""

    def _status_color(self) -> tuple[int, int, int]:
        if self.agent.user_offline or not self.agent.online:
            return (148, 163, 184)  # Offline grey
        if self.agent.private:
            return (245, 158, 11)
        return (11, 122, 75)  # ESS Tracker green

    def _title(self) -> str:
        a = self.agent
        if a.user_offline:
            state = "Offline"
        elif a.private:
            state = "Private"
        elif a.online:
            state = "Online"
        else:
            state = "Offline"
        name = a.display_name or a.username or "esstracker"
        org = a.company_name or "ESS Tracker"
        tip = ""
        if self._backend == "appindicator":
            tip = "\n(Left-click for menu)"
        return f"esstracker · {state}\n{name}\n{org}{tip}"

    @staticmethod
    def _noop(icon=None, item=None) -> None:
        """No-op so label rows stay in the menu on backends that drop action=None."""
        return None

    def _rebuild_menu(self):
        import pystray

        a = self.agent
        if a.user_offline:
            state = "Offline (idle)"
        elif a.private:
            state = "Private Time ON"
        elif a.online:
            state = "Tracking"
        else:
            state = "Offline"
        app, title = a._last_window if hasattr(a, "_last_window") else ("", "")
        now_line = f"Now: {app}" + (f" — {title[:40]}" if title else "")
        use_default = self._backend in ("gtk", "xorg")
        items = [
            pystray.MenuItem(f"Status: {state}", self._noop),
            pystray.MenuItem(f"User: {a.display_name or a.username or '—'}", self._noop),
            pystray.MenuItem(f"Org: {a.company_name or '—'}", self._noop),
            pystray.MenuItem(
                f"Role: {a.role or '—'}"
                + (f" · Shots {'ON' if a.shots_enabled else 'OFF'}" if a.online else ""),
                self._noop,
            ),
            pystray.MenuItem(now_line[:70] if app else "Now: —", self._noop),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Private Time",
                self._toggle_private,
                checked=lambda _: self.agent.private,
                enabled=lambda _: self.agent.private_allowed or self.agent.private,
            ),
            pystray.MenuItem(
                "Open My Day",
                self._open_dashboard,
                default=use_default,
            ),
            pystray.MenuItem("Sync now", self._flush_now),
            pystray.MenuItem("Preferences", self._preferences_menu()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Sign out", self._sign_out, enabled=lambda _: self.agent.tray_logout_allowed
            ),
            pystray.MenuItem(
                "Quit", self._menu_quit, enabled=lambda _: self.agent.tray_quit_allowed
            ),
        ]
        return pystray.Menu(*items)

    def refresh_ui(self) -> None:
        icon = self._icon
        if icon is None:
            return
        try:
            icon.icon = _make_icon(self._status_color(), private=self.agent.private)
            icon.title = self._title()
            icon.menu = self._rebuild_menu()
            icon.update_menu()
        except Exception as exc:
            print(f"[esstracker] tray refresh failed: {exc!r}")

    def _toggle_private(self, icon=None, item=None) -> None:
        want = not self.agent.private
        data = self.agent.client.set_private(want)
        if data is None:
            print("[esstracker] could not toggle Private Time")
            return
        self.agent.private = bool(data.get("active"))
        self.agent.refresh_server_policy()
        self.refresh_ui()

    def _open_dashboard(self, icon=None, item=None) -> None:
        url = self.agent.config.server_url.rstrip("/") + "/me"
        webbrowser.open(url)

    def _preferences_menu(self):
        """Tray submenu — no Tk window (works on GNOME AppIndicator)."""
        import pystray

        from .. import __version__
        from . import autostart

        server = self.agent.config.server_url.replace("https://", "").replace("http://", "")
        if len(server) > 40:
            server = server[:37] + "…"
        return pystray.Menu(
            pystray.MenuItem(f"Version {__version__}", self._noop),
            pystray.MenuItem(f"Server: {server}", self._noop),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Launch at login",
                self._toggle_autostart,
                checked=lambda _: autostart.is_enabled(),
                enabled=lambda _: autostart.is_supported(),
            ),
            pystray.MenuItem("Test connection", self._pref_test_connection),
            pystray.MenuItem("Check for updates", self._pref_check_updates),
            pystray.MenuItem("Download page", self._open_download),
        )

    def _toggle_autostart(self, icon=None, item=None) -> None:
        from . import autostart
        from .idle_ui import notify

        want = not autostart.is_enabled()
        try:
            autostart.set_enabled(want)
            notify("esstracker", f"Launch at login {'ON' if want else 'OFF'}")
        except Exception as exc:
            notify("esstracker", f"Startup setting failed: {exc}")
        self.refresh_ui()

    def _pref_test_connection(self, icon=None, item=None) -> None:
        from .idle_ui import notify

        def work() -> None:
            t0 = time.monotonic()
            data = self.agent.client.ping()
            ms = int((time.monotonic() - t0) * 1000)
            if data:
                notify("esstracker", f"Connected · {ms} ms")
            else:
                notify("esstracker", "Server not reachable — check your internet")

        threading.Thread(target=work, daemon=True, name="esstracker-ping").start()

    def _pref_check_updates(self, icon=None, item=None) -> None:
        from .. import __version__
        from .idle_ui import notify

        base = self.agent.config.server_url.rstrip("/")

        def work() -> None:
            data = self.agent.client.ping()
            upd = (data or {}).get("client_update") if data else None
            if data is None:
                notify("esstracker", "Could not reach the server")
            elif upd:
                latest = upd.get("latest") or "new"
                notify("esstracker", f"Version {latest} available")
                webbrowser.open(base + str(upd.get("page") or "/download"))
            else:
                notify("esstracker", f"You have the latest version ({__version__})")

        threading.Thread(target=work, daemon=True, name="esstracker-update").start()

    def _open_download(self, icon=None, item=None) -> None:
        webbrowser.open(self.agent.config.server_url.rstrip("/") + "/download")

    def _flush_now(self, icon=None, item=None) -> None:
        a, s = self.agent.flush()
        print(f"[esstracker] tray sync: {a} activities, {s} screenshots")
        self.refresh_ui()

    def _menu_quit(self, icon=None, item=None) -> None:
        if not self.agent.tray_quit_allowed:
            print("[esstracker] Quit is disabled by your administrator")
            return
        self._quit()

    def _sign_out(self, icon=None, item=None) -> None:
        from .config import clear_saved_token

        if not self.agent.tray_logout_allowed:
            print("[esstracker] Sign out is disabled by your administrator")
            return
        clear_saved_token(self.agent.config)
        self.agent.config.api_token = ""
        print("[esstracker] signed out — relaunch to sign in again")
        self._quit(reason="logout")

    def _quit(self, icon=None, item=None, *, reason: str = "quit") -> None:
        self.agent.request_stop(reason)
        if self._icon is not None:
            self._icon.stop()

    def run(self, on_ready: Callable[[], None] | None = None) -> None:
        import pystray

        _patch_frozen_pystray_menu()
        backend = _pick_backend()
        if backend is None:
            if sys.platform == "win32":
                raise RuntimeError(
                    "No system-tray backend available on Windows.\n"
                    "Install: pip install pystray Pillow pywin32"
                )
            raise RuntimeError(
                "No system-tray backend available on this Ubuntu session.\n"
                "Try an X11 session, or install:\n"
                "  sudo apt install python3-gi libayatana-appindicator3-1 "
                "gir1.2-ayatanaappindicator3-0.1 libnotify-bin\n"
                "On Ubuntu 20.04 you may need:\n"
                "  sudo apt install libappindicator3-1 gir1.2-appindicator3-0.1"
            )
        self._backend = backend
        print(f"[esstracker] tray backend={backend}")
        if backend == "appindicator":
            print(
                "[esstracker] tip: left-click the ESS tray icon for the menu "
                "(right-click does nothing on GNOME)"
            )
        elif backend == "xorg":
            print(
                "[esstracker] tip: left-click the tray icon for actions "
                "(the X11 tray backend has no right-click menu)"
            )
        self._icon = pystray.Icon(
            "esstracker",
            _make_icon(self._status_color()),
            self._title(),
            self._rebuild_menu(),
        )
        loop = None
        if on_ready:
            loop = threading.Thread(target=on_ready, daemon=True)
            loop.start()
        self._icon.run()
        if loop is not None:
            # Let the loop finish its final sync + stop notice before the process exits.
            self.agent.request_stop(self.agent._stop_reason)
            loop.join(timeout=20)


__all__ = ["AgentTray", "tray_available"]
