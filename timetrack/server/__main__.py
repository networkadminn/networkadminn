"""CLI for the TimeTrack server.

Usage:
    python -m timetrack.server run                 # start the web server
    python -m timetrack.server create-user ...      # add an admin/employee
    python -m timetrack.server list-users
    python -m timetrack.server reset-token USERNAME
    python -m timetrack.server set-password USERNAME
    python -m timetrack.server enable-mfa USERNAME    # turn on TOTP 2FA
    python -m timetrack.server disable-mfa USERNAME
"""

from __future__ import annotations

import argparse
import getpass
import sys

from .app import create_app
from .extensions import db
from .models import ROLE_ADMIN, ROLE_EMPLOYEE, User
from .passwords import validate_password


def _app():
    return create_app()


def _check_password(password: str, *, username: str | None, allow_weak: bool) -> bool:
    """Validate ``password`` against the policy. Returns True if acceptable."""
    if allow_weak:
        return True
    try:
        validate_password(password, username=username)
    except ValueError as exc:
        print(f"weak password: {exc}", file=sys.stderr)
        print("(use --allow-weak-password to override)", file=sys.stderr)
        return False
    return True


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

        password = args.password or getpass.getpass("Password: ")
        if not password:
            print("password required", file=sys.stderr)
            return 2
        if not _check_password(
            password, username=args.username, allow_weak=args.allow_weak_password
        ):
            return 3

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
        password = args.password or getpass.getpass("New password: ")
        if not password:
            print("password required", file=sys.stderr)
            return 2
        if not _check_password(
            password, username=user.username, allow_weak=args.allow_weak_password
        ):
            return 3
        user.set_password(password)
        db.session.commit()
        print(f"password updated for {user.username!r}")
    return 0


def _cmd_enable_mfa(args: argparse.Namespace) -> int:
    app = _app()
    with app.app_context():
        user = db.session.execute(
            db.select(User).filter_by(username=args.username)
        ).scalar_one_or_none()
        if user is None:
            print(f"no such user: {args.username!r}", file=sys.stderr)
            return 1
        secret = user.enable_mfa(secret=args.secret or None)
        db.session.commit()
        print(f"MFA enabled for {user.username!r}")
        print(f"Secret (base32): {secret}")
        print(f"Provisioning URI: {user.totp_uri()}")
        print(
            "Add the secret or scan the URI in an authenticator app "
            "(Google Authenticator, Authy, 1Password, ...)."
        )
    return 0


def _cmd_disable_mfa(args: argparse.Namespace) -> int:
    app = _app()
    with app.app_context():
        user = db.session.execute(
            db.select(User).filter_by(username=args.username)
        ).scalar_one_or_none()
        if user is None:
            print(f"no such user: {args.username!r}", file=sys.stderr)
            return 1
        user.disable_mfa()
        db.session.commit()
        print(f"MFA disabled for {user.username!r}")
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
    cu.add_argument(
        "--allow-weak-password",
        action="store_true",
        help="skip password strength enforcement",
    )
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
    spw.add_argument(
        "--allow-weak-password",
        action="store_true",
        help="skip password strength enforcement",
    )
    spw.set_defaults(func=_cmd_set_password)

    em = sub.add_parser("enable-mfa", help="enable TOTP two-factor for a user")
    em.add_argument("username")
    em.add_argument("--secret", help="use a specific base32 secret (else random)")
    em.set_defaults(func=_cmd_enable_mfa)

    dm = sub.add_parser("disable-mfa", help="disable TOTP two-factor for a user")
    dm.add_argument("username")
    dm.set_defaults(func=_cmd_disable_mfa)

    sub.add_parser("init-db", help="create database tables").set_defaults(
        func=_cmd_init_db
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
