"""Idle / offline UX for the employee agent.

When the user becomes idle: show a full-screen circular countdown 5→1,
then mark tray as Offline.

When activity resumes: notify Welcome again + total offline duration.
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
from typing import Callable


def human_duration(seconds: float) -> str:
    secs = max(0, int(seconds))
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


def notify(title: str, body: str) -> None:
    """Best-effort desktop notification (Linux notify-send) with ESS icon."""
    icon = os.environ.get(
        "ESSTRACKER_ICON",
        "/usr/share/icons/hicolor/256x256/apps/esstracker.png",
    )
    cmd = ["notify-send", "-a", "esstracker", "-u", "normal"]
    if icon and os.path.isfile(icon):
        cmd.extend(["-i", icon])
    else:
        cmd.extend(["-i", "esstracker"])
    cmd.extend([title, body])
    try:
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        print(f"[esstracker] {title}: {body}")


def show_going_offline_countdown(
    seconds: int = 5,
    on_tick: Callable[[int], None] | None = None,
    on_done: Callable[[], None] | None = None,
) -> None:
    """Show a big circular countdown, then call on_done (daemon thread)."""

    def _run() -> None:
        ok = _gtk_countdown(seconds, on_tick)
        if not ok:
            for n in range(seconds, 0, -1):
                if on_tick:
                    on_tick(n)
                notify("Going offline", f"{n}…")
                time.sleep(1)
        if on_done:
            on_done()

    threading.Thread(target=_run, daemon=True, name="esstracker-idle-countdown").start()


def _gtk_countdown(seconds: int, on_tick: Callable[[int], None] | None) -> bool:
    try:
        import os
        import sys

        # Frozen .deb cannot use host PyGObject (wrong Python ABI).
        if getattr(sys, "frozen", False) or hasattr(sys, "_MEIPASS"):
            return False
        dist = "/usr/lib/python3/dist-packages"
        if dist not in sys.path and os.path.isdir(dist):
            sys.path.append(dist)
        import gi

        gi.require_version("Gtk", "3.0")
        from gi.repository import GLib, Gtk
    except Exception:
        return False

    state = {"n": seconds, "done": False}

    win = Gtk.Window(type=Gtk.WindowType.POPUP)
    win.set_decorated(False)
    win.set_keep_above(True)
    win.set_accept_focus(False)
    win.fullscreen()
    win.set_app_paintable(True)

    screen = win.get_screen()
    visual = screen.get_rgba_visual()
    if visual is not None:
        win.set_visual(visual)

    css = Gtk.CssProvider()
    css.load_from_data(
        b"""
        window { background-color: rgba(6, 40, 28, 0.78); }
        .ring {
          background-color: #0B7A4B;
          border-radius: 999px;
          min-width: 240px;
          min-height: 240px;
        }
        .count {
          color: white;
          font-size: 108px;
          font-weight: 800;
        }
        .label {
          color: rgba(255,255,255,0.92);
          font-size: 18px;
          font-weight: 700;
          letter-spacing: 0.08em;
        }
        """
    )
    Gtk.StyleContext.add_provider_for_screen(
        screen, css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )

    outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
    outer.set_halign(Gtk.Align.CENTER)
    outer.set_valign(Gtk.Align.CENTER)
    ring = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    ring.get_style_context().add_class("ring")
    ring.set_halign(Gtk.Align.CENTER)
    count = Gtk.Label(label=str(seconds))
    count.get_style_context().add_class("count")
    count.set_halign(Gtk.Align.CENTER)
    count.set_valign(Gtk.Align.CENTER)
    ring.pack_start(count, True, True, 48)
    label = Gtk.Label(label="GOING OFFLINE")
    label.get_style_context().add_class("label")
    outer.pack_start(ring, False, False, 0)
    outer.pack_start(label, False, False, 0)
    win.add(outer)
    win.show_all()

    state = {"n": seconds}

    def finish(_=None) -> bool:
        try:
            win.destroy()
        except Exception:
            pass
        Gtk.main_quit()
        return False

    def tick() -> bool:
        n = state["n"]
        count.set_text(str(n))
        if on_tick:
            try:
                on_tick(n)
            except Exception:
                pass
        if n <= 1:
            GLib.timeout_add(800, finish)
            return False
        state["n"] = n - 1
        return True

    GLib.idle_add(tick)
    GLib.timeout_add_seconds(1, tick)
    try:
        Gtk.main()
    except Exception:
        return False
    return True


def welcome_back(offline_seconds: float) -> None:
    dur = human_duration(offline_seconds)
    notify("Welcome again", f"Total offline time: {dur}")
    print(f"[esstracker] Welcome again — offline for {dur}")


__all__ = [
    "human_duration",
    "notify",
    "show_going_offline_countdown",
    "welcome_back",
]
