import re

import pytest

from timetrack.config import Config
from timetrack.dashboard import create_app as create_dashboard_app
from timetrack.security import (
    SecurityHeaders,
    client_ip,
    ip_in_allowlist,
)
from timetrack.server import create_app
from timetrack.server.extensions import db
from timetrack.server.models import ROLE_ADMIN, ROLE_EMPLOYEE, User
from timetrack.server.passwords import PasswordPolicy, validate_password
from timetrack.storage import Storage


# --------------------------------------------------------------------------- #
# App fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture()
def app(tmp_path):
    app = create_app(
        data_dir=str(tmp_path / "data"),
        database_uri="sqlite:///" + str(tmp_path / "test.db"),
        secret_key="test-secret",
    )
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    with app.app_context():
        admin = User(username="boss", role=ROLE_ADMIN, display_name="The Boss")
        admin.set_password("adminpass")
        emp = User(username="alice", role=ROLE_EMPLOYEE, display_name="Alice")
        emp.set_password("alicepass")
        db.session.add_all([admin, emp])
        db.session.commit()
        app.config["_ADMIN_ID"] = admin.id
        app.config["_EMP_ID"] = emp.id
    return app


@pytest.fixture()
def client(app):
    return app.test_client()


def _login(client, username, password, **kwargs):
    return client.post(
        "/login", data={"username": username, "password": password}, **kwargs
    )


# --------------------------------------------------------------------------- #
# Security headers
# --------------------------------------------------------------------------- #
def test_security_headers_present(client):
    resp = client.get("/login")
    h = resp.headers
    assert "Content-Security-Policy" in h
    assert h["X-Frame-Options"] == "SAMEORIGIN"
    assert h["X-Content-Type-Options"] == "nosniff"
    assert h["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert "Permissions-Policy" in h
    assert h["Cross-Origin-Opener-Policy"] == "same-origin"


def test_hsts_only_on_https(client):
    plain = client.get("/login")
    assert "Strict-Transport-Security" not in plain.headers

    secure = client.get("/login", headers={"X-Forwarded-Proto": "https"})
    assert "Strict-Transport-Security" in secure.headers
    assert "max-age=" in secure.headers["Strict-Transport-Security"]
    assert "includeSubDomains" in secure.headers["Strict-Transport-Security"]


def test_csp_nonce_matches_inline_script(client):
    resp = client.get("/login")
    csp = resp.headers["Content-Security-Policy"]
    m = re.search(r"'nonce-([\w-]+)'", csp)
    assert m, csp
    nonce = m.group(1)
    # The base template's inline behaviour script must carry the same nonce.
    assert f'nonce="{nonce}"'.encode() in resp.data
    # CSP must not fall back to unsafe-inline for scripts.
    script_src = next(d for d in csp.split(";") if d.strip().startswith("script-src"))
    assert "'unsafe-inline'" not in script_src
    assert "https://cdn.jsdelivr.net" in script_src


def test_csp_nonce_is_per_request(client):
    a = re.search(r"'nonce-([\w-]+)'", client.get("/login").headers["Content-Security-Policy"])
    b = re.search(r"'nonce-([\w-]+)'", client.get("/login").headers["Content-Security-Policy"])
    assert a.group(1) != b.group(1)


def test_dashboard_app_has_headers(tmp_path):
    db_path = str(tmp_path / "t.db")
    with Storage(db_path) as s:
        s.record("code", "main.py", "productive", False, 60.0)
    app = create_dashboard_app(Config(db_path=db_path))
    app.config.update(TESTING=True)
    resp = app.test_client().get("/")
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert "Content-Security-Policy" in resp.headers


# --------------------------------------------------------------------------- #
# SecurityHeaders unit behaviour
# --------------------------------------------------------------------------- #
def test_security_headers_disabled():
    sh = SecurityHeaders(enabled=False)
    # build() itself always produces values; the install hook is what respects
    # ``enabled``. Sanity check the builder still works.
    built = sh.build(nonce="x", is_secure=True)
    assert "Content-Security-Policy" in built


def test_hsts_force_sends_without_https():
    sh = SecurityHeaders(hsts=True, hsts_force=True)
    assert "Strict-Transport-Security" in sh.build(nonce="n", is_secure=False)


def test_custom_csp_template():
    sh = SecurityHeaders(csp="default-src 'self'; script-src 'nonce-{nonce}'")
    out = sh.build(nonce="abc", is_secure=False)["Content-Security-Policy"]
    assert out == "default-src 'self'; script-src 'nonce-abc'"


# --------------------------------------------------------------------------- #
# IP allow-list
# --------------------------------------------------------------------------- #
def test_ip_in_allowlist():
    assert ip_in_allowlist("10.0.0.5", []) is True  # empty = allow all
    assert ip_in_allowlist("10.0.0.5", ["10.0.0.5"]) is True
    assert ip_in_allowlist("10.0.0.6", ["10.0.0.5"]) is False
    assert ip_in_allowlist("10.0.0.42", ["10.0.0.0/24"]) is True
    assert ip_in_allowlist("10.0.1.42", ["10.0.0.0/24"]) is False
    assert ip_in_allowlist("not-an-ip", ["10.0.0.0/24"]) is False


def test_client_ip_respects_trust_proxy():
    class Req:
        remote_addr = "127.0.0.1"
        headers = {"X-Forwarded-For": "203.0.113.9, 10.0.0.1"}

    assert client_ip(Req(), trust_proxy=False) == "127.0.0.1"
    assert client_ip(Req(), trust_proxy=True) == "203.0.113.9"


def test_admin_ip_allowlist_blocks(tmp_path):
    app = create_app(
        data_dir=str(tmp_path / "data"),
        database_uri="sqlite:///" + str(tmp_path / "t.db"),
        secret_key="s",
        admin_ip_allowlist=["203.0.113.1"],
    )
    app.config.update(TESTING=True)
    with app.app_context():
        admin = User(username="boss", role=ROLE_ADMIN)
        admin.set_password("adminpass")
        db.session.add(admin)
        db.session.commit()
    client = app.test_client()
    _login(client, "boss", "adminpass")
    # test client's remote_addr is 127.0.0.1, not in the allow-list.
    assert client.get("/admin").status_code == 403


def test_admin_ip_allowlist_allows(tmp_path):
    app = create_app(
        data_dir=str(tmp_path / "data"),
        database_uri="sqlite:///" + str(tmp_path / "t.db"),
        secret_key="s",
        admin_ip_allowlist=["127.0.0.1"],
    )
    app.config.update(TESTING=True)
    with app.app_context():
        admin = User(username="boss", role=ROLE_ADMIN)
        admin.set_password("adminpass")
        db.session.add(admin)
        db.session.commit()
    client = app.test_client()
    _login(client, "boss", "adminpass")
    assert client.get("/admin").status_code == 200


# --------------------------------------------------------------------------- #
# Password policy
# --------------------------------------------------------------------------- #
def test_password_policy_rejects_weak():
    with pytest.raises(ValueError):
        validate_password("short")
    with pytest.raises(ValueError):
        validate_password("password")  # common
    with pytest.raises(ValueError):
        validate_password("alllowercase123")  # no upper/symbol
    with pytest.raises(ValueError):
        validate_password("Str0ng!Pass", username="Str0ng")  # contains username


def test_password_policy_accepts_strong():
    validate_password("Corr3ct-Horse!Battery")  # should not raise


def test_password_policy_configurable():
    lax = PasswordPolicy(min_length=4, require_symbol=False, require_upper=False)
    lax.validate("ab12")  # ok under relaxed policy


# --------------------------------------------------------------------------- #
# MFA (TOTP)
# --------------------------------------------------------------------------- #
def test_mfa_login_flow(app):
    import pyotp

    with app.app_context():
        user = db.session.get(User, app.config["_EMP_ID"])
        secret = user.enable_mfa()
        db.session.commit()

    client = app.test_client()
    # Password step redirects to the MFA challenge instead of logging in.
    resp = _login(client, "alice", "alicepass", follow_redirects=False)
    assert resp.status_code == 302
    assert "/login/mfa" in resp.headers["Location"]

    # Not yet authenticated: home still bounces to login.
    assert client.get("/", follow_redirects=False).status_code == 302

    # Wrong code is rejected.
    bad = client.post("/login/mfa", data={"code": "000000"})
    assert b"Invalid authentication code" in bad.data

    # Correct code completes the login.
    code = pyotp.TOTP(secret).now()
    ok = client.post("/login/mfa", data={"code": code}, follow_redirects=True)
    assert ok.status_code == 200
    assert b"Productivity" in ok.data


def test_mfa_not_required_when_disabled(app):
    client = app.test_client()
    resp = _login(client, "alice", "alicepass", follow_redirects=False)
    # No MFA -> straight to the app (redirect to home), not the MFA page.
    assert resp.status_code == 302
    assert "/login/mfa" not in resp.headers["Location"]


def test_mfa_page_without_pending_redirects(app):
    client = app.test_client()
    resp = client.get("/login/mfa", follow_redirects=False)
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]
