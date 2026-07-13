"""Command-line interface for TimeTrack.

Usage:
    python -m timetrack track          # start the background tracker
    python -m timetrack serve          # launch the web dashboard
    python -m timetrack report [DAY]   # print a text report (YYYY-MM-DD)
    python -m timetrack doctor         # check platform capabilities
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime

from . import __version__
from . import platform as pf
from .analytics import humanize, summarize_day
from .config import load_config
from .storage import Storage
from .tracker import Tracker


def _cmd_track(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    Tracker(config=cfg).run()
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    from .dashboard import create_app

    cfg = load_config(args.config)
    host = args.host or cfg.host
    port = args.port or cfg.port
    app = create_app(cfg)
    print(f"[timetrack] dashboard on http://{host}:{port}")
    app.run(host=host, port=port, debug=args.debug)
    return 0


def _cmd_report(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    day = datetime.now()
    if args.day:
        try:
            day = datetime.strptime(args.day, "%Y-%m-%d")
        except ValueError:
            print(f"invalid date: {args.day!r} (use YYYY-MM-DD)", file=sys.stderr)
            return 2

    with Storage(cfg.db_path) as storage:
        s = summarize_day(storage, day)

    print(f"TimeTrack report for {day.strftime('%Y-%m-%d')}")
    print("-" * 40)
    print(f"Active time      : {humanize(s.active_seconds)}")
    print(f"Idle time        : {humanize(s.idle_seconds)}")
    print(f"Productive       : {humanize(s.productive_seconds)}")
    print(f"Unproductive     : {humanize(s.unproductive_seconds)}")
    print(f"Neutral          : {humanize(s.neutral_seconds)}")
    print(f"Productivity      : {s.productivity_pct}%")
    print(f"Effectiveness     : {s.effectiveness_pct}%")
    if s.apps:
        print("\nTop applications:")
        for a in s.apps[:10]:
            print(f"  {a.app:<24} {a.category:<12} {humanize(a.seconds)}")
    return 0


def _cmd_doctor(_args: argparse.Namespace) -> int:
    print(f"TimeTrack {__version__}")
    print(f"Backend        : {pf.backend_name()}")
    win = pf.get_active_window()
    idle = pf.get_idle_seconds()
    if win is None:
        print("Active window  : <none detected>")
    else:
        print(f"Active window  : app={win.app!r} title={win.title!r} pid={win.pid}")
    print(f"Idle seconds   : {idle:.1f}")
    if win is None:
        print(
            "\nNote: no active window detected. On Linux install 'xdotool' or "
            "'xprop' (and use an X11 session); headless/CI has no windows."
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="timetrack", description=__doc__)
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("-c", "--config", help="path to config.toml")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("track", help="run the tracking loop").set_defaults(func=_cmd_track)

    sp = sub.add_parser("serve", help="run the web dashboard")
    sp.add_argument("--host")
    sp.add_argument("--port", type=int)
    sp.add_argument("--debug", action="store_true")
    sp.set_defaults(func=_cmd_serve)

    rp = sub.add_parser("report", help="print a text report for a day")
    rp.add_argument("day", nargs="?", help="YYYY-MM-DD (defaults to today)")
    rp.set_defaults(func=_cmd_report)

    sub.add_parser("doctor", help="check platform capabilities").set_defaults(
        func=_cmd_doctor
    )
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
