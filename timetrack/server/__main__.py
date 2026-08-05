"""CLI for the TimeTrack server.

Usage:
    python -m timetrack.server run                 # start the web server
    python -m timetrack.server create-user ...      # add an admin/employee
    python -m timetrack.server list-users
    python -m timetrack.server reset-token USERNAME
    python -m timetrack.server set-password USERNAME
"""

from __future__ import annotations

import argparse
import getpass
import sys

from ..security import password_policy_error
from .app import create_app
from .extensions import db
from .models import ROLE_ADMIN, ROLE_EMPLOYEE, User


def _checked_password(raw: str | None, prompt: str) -> str | None:
    """Prompt for / validate a password; return it or None if rejected."""
    password = raw or getpass.getpass(prompt)
    if not password:
        print("password required", file=sys.stderr)
        return None
    problem = password_policy_error(password)
    if problem:
        print(f"weak password: {problem}", file=sys.stderr)
        return None
    return password


def _app():
    return create_app()


def _cmd_run(args: argparse.Namespace) -> int:
    app = _app()
    cfg = app.config["TIMETRACK_SERVER_CONFIG"]
    host = args.host or cfg.host
    port = args.port or cfg.port
    print(f"[timetrack-server] listening on http://{host}:{port}")
    app.run(host=host, port=port, debug=args.debug)
    return 0


def _cmd_create_user(args: argparse.Namespace) -> int:
    app = _app()
    with app.app_context():
        existing = db.session.execute(
            db.select(User).filter_by(username=args.username)
        ).scalar_one_or_none()
        if existing is not None:
            print(f"user {args.username!r} already exists", file=sys.stderr)
            return 1

        password = _checked_password(args.password, "Password: ")
        if password is None:
            return 2

        user = User(
            username=args.username,
            email=args.email or "",
            display_name=args.name or "",
            role=ROLE_ADMIN if args.admin else ROLE_EMPLOYEE,
        )
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        print(f"created {user.role} {user.username!r}")
        print(f"API token: {user.api_token}")
    return 0


def _cmd_list_users(_args: argparse.Namespace) -> int:
    app = _app()
    with app.app_context():
        users = db.session.execute(db.select(User).order_by(User.id)).scalars().all()
        if not users:
            print("no users yet")
            return 0
        print(f"{'ID':<4}{'USERNAME':<20}{'ROLE':<10}{'ENABLED':<9}TOKEN")
        for u in users:
            print(f"{u.id:<4}{u.username:<20}{u.role:<10}{str(u.enabled):<9}{u.api_token}")
    return 0


def _cmd_reset_token(args: argparse.Namespace) -> int:
    app = _app()
    with app.app_context():
        user = db.session.execute(
            db.select(User).filter_by(username=args.username)
        ).scalar_one_or_none()
        if user is None:
            print(f"no such user: {args.username!r}", file=sys.stderr)
            return 1
        token = user.rotate_token()
        db.session.commit()
        print(f"new API token for {user.username!r}: {token}")
    return 0


def _cmd_set_password(args: argparse.Namespace) -> int:
    app = _app()
    with app.app_context():
        user = db.session.execute(
            db.select(User).filter_by(username=args.username)
        ).scalar_one_or_none()
        if user is None:
            print(f"no such user: {args.username!r}", file=sys.stderr)
            return 1
        password = _checked_password(args.password, "New password: ")
        if password is None:
            return 2
        user.set_password(password)
        db.session.commit()
        print(f"password updated for {user.username!r}")
    return 0


def _cmd_init_db(_args: argparse.Namespace) -> int:
    _app()  # create_app calls db.create_all()
    print("database initialized")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="timetrack.server", description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)

    rp = sub.add_parser("run", help="start the web server")
    rp.add_argument("--host")
    rp.add_argument("--port", type=int)
    rp.add_argument("--debug", action="store_true")
    rp.set_defaults(func=_cmd_run)

    cu = sub.add_parser("create-user", help="create a user")
    cu.add_argument("username")
    cu.add_argument("--password")
    cu.add_argument("--name", help="display name")
    cu.add_argument("--email")
    cu.add_argument("--admin", action="store_true", help="make this user an admin")
    cu.set_defaults(func=_cmd_create_user)

    sub.add_parser("list-users", help="list users + tokens").set_defaults(
        func=_cmd_list_users
    )

    rt = sub.add_parser("reset-token", help="rotate a user's API token")
    rt.add_argument("username")
    rt.set_defaults(func=_cmd_reset_token)

    spw = sub.add_parser("set-password", help="set a user's password")
    spw.add_argument("username")
    spw.add_argument("--password")
    spw.set_defaults(func=_cmd_set_password)

    sub.add_parser("init-db", help="create database tables").set_defaults(
        func=_cmd_init_db
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
