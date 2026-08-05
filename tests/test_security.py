"""Tests for application-level security hardening."""

import pytest

from timetrack.config import Config
from timetrack.dashboard import create_app as create_dashboard_app
from timetrack.security import (
    ip_allowed,
    is_safe_redirect_target,
    parse_ip_allowlist,
    password_policy_error,
)
from timetrack.server import create_app as create_server_app
from timetrack.server.extensions import db
from timetrack.server.models import ROLE_ADMIN, ROLE_EMPLOYEE, User


# --- helpers -----------------------------------------------------------


def _server_app(tmp_path, **overrides):
    app = create_server_app(
        data_dir=str(tmp_path / "data"),
        database_uri="sqlite:///" + str(tmp_path / "test.db"),
        secret_key="test-secret",
        **overrides,
    )
    app.config.update(TESTING=True)
    with app.app_context():
        admin = User(username="boss", role=ROLE_ADMIN)
        admin.set_password("adminpass")
        emp = User(username="alice", role=ROLE_EMPLOYEE)
        emp.set_password("alicepass")
        db.session.add_all([admin, emp])
        db.session.commit()
    return app


def _login(client, username, password, query=""):
    return client.post(
        f"/login{query}",
        data={"username": username, "password": password},
        follow_redirects=False,
    )


# --- response headers --------------------------------------------------

EXPECTED_HEADERS = {
    "X-Frame-Options": "SAMEORIGIN",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
}


def test_server_sets_security_headers(tmp_path):
    client = _server_app(tmp_path).test_client()
    resp = client.get("/login")
    for name, value in EXPECTED_HEADERS.items():
        assert resp.headers.get(name) == value
    csp = resp.headers.get("Content-Security-Policy", "")
    assert "default-src 'self'" in csp
    assert "https://cdn.jsdelivr.net" in csp


def test_hsts_only_on_https(tmp_path):
    client = _server_app(tmp_path).test_client()

    plain = client.get("/login")
    assert "Strict-Transport-Security" not in plain.headers

    https = client.get("/login", headers={"X-Forwarded-Proto": "https"})
    assert https.headers["Strict-Transport-Security"].startswith("max-age=")


def test_hsts_can_be_disabled(tmp_path):
    client = _server_app(tmp_path, hsts_enabled=False).test_client()
    resp = client.get("/login", headers={"X-Forwarded-Proto": "https"})
    assert "Strict-Transport-Security" not in resp.headers


def test_dashboard_sets_security_headers(tmp_path):
    app = create_dashboard_app(Config(db_path=str(tmp_path / "t.db")))
    app.config.update(TESTING=True)
    resp = app.test_client().get("/healthz")
    for name, value in EXPECTED_HEADERS.items():
        assert resp.headers.get(name) == value
    assert "default-src 'self'" in resp.headers.get("Content-Security-Policy", "")


def test_session_cookie_flags(tmp_path):
    app = _server_app(tmp_path)
    assert app.config["SESSION_COOKIE_HTTPONLY"] is True
    assert app.config["SESSION_COOKIE_SAMESITE"] == "Lax"


# --- open-redirect protection ------------------------------------------


def test_login_rejects_external_redirect(tmp_path):
    client = _server_app(tmp_path).test_client()
    resp = _login(client, "alice", "alicepass", query="?next=https://evil.example")
    assert resp.status_code == 302
    assert "evil.example" not in resp.headers["Location"]


def test_login_rejects_protocol_relative_redirect(tmp_path):
    client = _server_app(tmp_path).test_client()
    resp = _login(client, "alice", "alicepass", query="?next=//evil.example")
    assert resp.status_code == 302
    assert "evil.example" not in resp.headers["Location"]


def test_login_allows_relative_redirect(tmp_path):
    client = _server_app(tmp_path).test_client()
    resp = _login(client, "alice", "alicepass", query="?next=/me")
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/me")


@pytest.mark.parametrize(
    ("target", "ok"),
    [
        ("/me", True),
        ("/admin?day=2026-01-01", True),
        (None, False),
        ("", False),
        ("https://evil.example/x", False),
        ("//evil.example", False),
        ("/\\evil.example", False),
        ("javascript:alert(1)", False),
        ("me", False),
    ],
)
def test_is_safe_redirect_target(target, ok):
    assert is_safe_redirect_target(target) is ok


# --- admin IP allowlist -------------------------------------------------


def test_admin_blocked_when_ip_not_allowlisted(tmp_path):
    app = _server_app(tmp_path, admin_ip_allowlist="203.0.113.0/24")
    client = app.test_client()  # test client connects from 127.0.0.1
    _login(client, "boss", "adminpass")
    assert client.get("/admin").status_code == 403


def test_admin_allowed_when_ip_allowlisted(tmp_path):
    app = _server_app(tmp_path, admin_ip_allowlist="203.0.113.0/24, 127.0.0.1")
    client = app.test_client()
    _login(client, "boss", "adminpass")
    assert client.get("/admin").status_code == 200


def test_empty_allowlist_allows_everyone():
    assert ip_allowed("127.0.0.1", []) is True
    assert ip_allowed("127.0.0.1", None) is True


def test_ip_allowed_matching():
    nets = parse_ip_allowlist("10.0.0.0/8, 203.0.113.7")
    assert ip_allowed("10.1.2.3", nets) is True
    assert ip_allowed("203.0.113.7", nets) is True
    assert ip_allowed("203.0.113.8", nets) is False
    assert ip_allowed(None, nets) is False
    assert ip_allowed("not-an-ip", nets) is False


def test_parse_ip_allowlist_rejects_garbage():
    with pytest.raises(ValueError):
        parse_ip_allowlist("nonsense")


# --- password policy -----------------------------------------------------


@pytest.mark.parametrize(
    ("password", "ok"),
    [
        ("Correct-Horse-42-battery", True),
        ("short1A", False),
        ("alllowercase1234", False),
        ("ALLUPPERCASE1234", False),
        ("NoDigitsInHerePassword", False),
    ],
)
def test_password_policy(password, ok):
    assert (password_policy_error(password) is None) is ok


def test_cli_rejects_weak_password(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TIMETRACK_SERVER_DATA", str(tmp_path / "data"))
    from timetrack.server.__main__ import main

    rc = main(["create-user", "bob", "--password", "weak"])
    assert rc == 2
    assert "weak password" in capsys.readouterr().err


def test_cli_accepts_strong_password(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TIMETRACK_SERVER_DATA", str(tmp_path / "data"))
    from timetrack.server.__main__ import main

    rc = main(["create-user", "bob", "--password", "Str0ng-enough-Pass"])
    assert rc == 0
    assert "created" in capsys.readouterr().out
