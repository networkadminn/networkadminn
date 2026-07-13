"""CLI for the TimeTrack employee agent.

Usage:
    python -m timetrack.agent run          # start tracking + syncing
    python -m timetrack.agent ping         # verify server + token
    python -m timetrack.agent status       # show buffer + config
    python -m timetrack.agent flush        # force a one-off sync
"""

from __future__ import annotations

import argparse

from .agent import Agent
from .buffer import AgentBuffer
from .client import ServerClient
from .config import load_agent_config


def _cmd_run(args: argparse.Namespace) -> int:
    Agent(load_agent_config(args.config)).run()
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
    print(f"token set         : {'yes' if cfg.api_token else 'no'}")
    print(f"screenshots       : {'on' if cfg.screenshots_enabled else 'off'} "
          f"(every {cfg.screenshot_interval:.0f}s)")
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


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="timetrack.agent", description=__doc__)
    p.add_argument("-c", "--config", help="path to agent.toml")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("run", help="start the agent").set_defaults(func=_cmd_run)
    sub.add_parser("ping", help="verify server + token").set_defaults(func=_cmd_ping)
    sub.add_parser("status", help="show buffer + config").set_defaults(func=_cmd_status)
    sub.add_parser("flush", help="force a sync now").set_defaults(func=_cmd_flush)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
