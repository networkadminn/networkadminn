"""CLI for the esstracker employee agent.

Usage:
    esstracker-agent              # DeskTime-style: login if needed, then tray
    esstracker-agent run
    esstracker-agent ping
    esstracker-agent status
    esstracker-agent flush
    esstracker-agent logout
"""

from __future__ import annotations

import argparse
import atexit
import fcntl
import os
import sys

from .agent import Agent
from .buffer import AgentBuffer
from .client import ServerClient
from .config import (
    LOCK_PATH,
    clear_saved_token,
    ensure_data_dirs,
    load_agent_config,
)


_lock_fh = None


def _acquire_single_instance() -> bool:
    """Only one agent per user (DeskTime-style)."""
    global _lock_fh
    ensure_data_dirs()
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    _lock_fh = open(LOCK_PATH, "w", encoding="utf-8")
    try:
        fcntl.flock(_lock_fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        _lock_fh.close()
        _lock_fh = None
        return False
    _lock_fh.write(str(os.getpid()))
    _lock_fh.flush()

    def _release() -> None:
        global _lock_fh
        if _lock_fh is not None:
            try:
                fcntl.flock(_lock_fh.fileno(), fcntl.LOCK_UN)
                _lock_fh.close()
            except Exception:
                pass
            _lock_fh = None

    atexit.register(_release)
    return True


def _ensure_signed_in(cfg, *, force_login: bool = False):
    """Show login window if no token or token is invalid."""
    from .login_ui import show_login

    if force_login or not cfg.is_signed_in:
        return show_login(cfg)

    client = ServerClient(cfg.server_url, cfg.api_token)
    if client.ping() is not None:
        return True
    # Token expired / wrong server → ask again
    print("[esstracker] session expired — please sign in again")
    cfg.api_token = ""
    return show_login(cfg)


def _cmd_run(args: argparse.Namespace) -> int:
    if not _acquire_single_instance():
        print("[esstracker] already running (tray). Open the ESS tray icon.")
        return 0

    cfg = load_agent_config(args.config)
    if not args.no_login:
        if not _ensure_signed_in(cfg, force_login=bool(args.login)):
            print("[esstracker] sign-in cancelled")
            return 1
        # Reload after save
        cfg = load_agent_config(args.config or cfg.config_path)

    Agent(cfg).run(tray=not args.no_tray)
    return 0


def _cmd_ping(args: argparse.Namespace) -> int:
    cfg = load_agent_config(args.config)
    client = ServerClient(cfg.server_url, cfg.api_token)
    result = client.ping()
    if result is None:
        print(f"could not reach {cfg.server_url} (or invalid token)")
        return 1
    print(f"ok: user={result.get('user')} role={result.get('role')}")
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    cfg = load_agent_config(args.config)
    with AgentBuffer(cfg.buffer_path) as buf:
        acts, shots = buf.pending_counts()
    print(f"server            : {cfg.server_url}")
    print(f"signed in         : {'yes' if cfg.is_signed_in else 'no'}")
    print(f"token set         : {'yes' if cfg.api_token else 'no'}")
    print(f"config            : {cfg.config_path}")
    print(
        f"screenshots       : {'on' if cfg.screenshots_enabled else 'off'} "
        f"(every {cfg.screenshot_interval:.0f}s)"
    )
    print(f"buffer            : {cfg.buffer_path}")
    print(f"pending activities: {acts}")
    print(f"pending screenshots: {shots}")
    return 0


def _cmd_flush(args: argparse.Namespace) -> int:
    agent = Agent(load_agent_config(args.config))
    a, s = agent.flush()
    agent.buffer.close()
    print(f"synced {a} activities, {s} screenshots")
    return 0


def _cmd_private(args: argparse.Namespace) -> int:
    cfg = load_agent_config(args.config)
    client = ServerClient(cfg.server_url, cfg.api_token)
    mode = args.mode
    if mode == "status":
        data = client.get_private()
        if data is None:
            print("could not reach server")
            return 1
        print(
            f"private={'ON' if data.get('active') else 'off'} "
            f"allowed={'yes' if data.get('allowed') else 'no'}"
        )
        return 0
    enable = mode == "on"
    data = client.set_private(enable)
    if data is None:
        print("could not update private time (check token / network)")
        return 1
    print(f"private time {'ON' if data.get('active') else 'OFF'}")
    return 0


def _cmd_logout(args: argparse.Namespace) -> int:
    clear_saved_token(load_agent_config(args.config))
    print("Signed out. Next launch will ask you to sign in.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="esstracker-agent", description=__doc__)
    p.add_argument("-c", "--config", help="path to agent.toml")
    sub = p.add_subparsers(dest="command", required=True)
    run_p = sub.add_parser("run", help="start tracking (login window if needed)")
    run_p.add_argument(
        "--no-tray",
        action="store_true",
        help="run headless without the notification-area icon",
    )
    run_p.add_argument(
        "--no-login",
        action="store_true",
        help="skip login UI (use existing token only)",
    )
    run_p.add_argument(
        "--login",
        action="store_true",
        help="force the sign-in window even if already signed in",
    )
    run_p.set_defaults(func=_cmd_run)
    sub.add_parser("ping", help="verify server + token").set_defaults(func=_cmd_ping)
    sub.add_parser("status", help="show buffer + config").set_defaults(func=_cmd_status)
    sub.add_parser("flush", help="force a sync now").set_defaults(func=_cmd_flush)
    sub.add_parser("logout", help="clear saved login (DeskTime sign-out)").set_defaults(
        func=_cmd_logout
    )
    pp = sub.add_parser("private", help="toggle DeskTime-style Private Time")
    pp.add_argument("mode", choices=("on", "off", "status"))
    pp.set_defaults(func=_cmd_private)
    return p


def main(argv: list[str] | None = None) -> None:
    raw = list(sys.argv[1:] if argv is None else argv)
    commands = {"run", "ping", "status", "flush", "private", "logout"}
    if not raw or raw[0] not in commands:
        if not (raw and raw[0] in ("-h", "--help")):
            raw = ["run", *raw]
    args = build_parser().parse_args(raw)
    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()
