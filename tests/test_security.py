"""Tests for HTTP security headers, IP allowlisting, and password policy."""

import pytest

from timetrack.server import create_app
from timetrack.server.extensions import db
from timetrack.server.models import ROLE_ADMIN, User
from timetrack.server.security import is_safe_redirect, validate_password


@pytest.fixture()
def app(tmp_path):
    app = create_app(
        data_dir=str(tmp_path / "data"),
        database_uri="sqlite:///" + str(tmp_path / "test.db"),
        secret_key="test-secret",
        hsts_max_age=31536000,
        trust_proxy_headers=True,
    )
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    with app.app_context():
        admin = User(username="boss", role=ROLE_ADMIN)
        admin.set_password("adminpass-long")
        db.session.add(admin)
        db.session.commit()
    return app


@pytest.fixture()
def client(app):
    return app.test_client()


def test_security_headers_on_response(client):
    resp = client.get("/login")
    assert resp.status_code == 200
    assert resp.headers.get("X-Frame-Options") == "SAMEORIGIN"
    assert resp.headers.get("X-Content-Type-Options") == "nosniff"
    assert resp.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"
    assert "default-src 'self'" in resp.headers.get("Content-Security-Policy", "")


def test_hsts_when_secure_and_configured(client):
    resp = client.get("/login", headers={"X-Forwarded-Proto": "https"})
    assert "max-age=31536000" in resp.headers.get("Strict-Transport-Security", "")


def test_force_https_redirect(client, app):
    app.config["TIMETRACK_SERVER_CONFIG"].force_https = True
    resp = client.get("/login", follow_redirects=False)
    assert resp.status_code == 301
    assert resp.headers["Location"].startswith("https://")


def test_admin_ip_allowlist_blocks_login(client, app):
    app.config["TIMETRACK_SERVER_CONFIG"].admin_ip_allowlist = ["10.0.0.0/8"]
    resp = client.get("/login")
    assert resp.status_code == 403


def test_admin_ip_allowlist_allows_matching_ip(client, app):
    app.config["TIMETRACK_SERVER_CONFIG"].admin_ip_allowlist = ["127.0.0.1"]
    resp = client.get("/login")
    assert resp.status_code == 200


def test_api_not_blocked_by_admin_ip_allowlist(client, app):
    app.config["TIMETRACK_SERVER_CONFIG"].admin_ip_allowlist = ["10.0.0.0/8"]
    resp = client.post("/api/v1/activities", json={"activities": []})
    assert resp.status_code == 401  # missing token, not IP-blocked


def test_validate_password_policy():
    assert validate_password("short", min_length=12) is not None
    assert validate_password("long-enough-pass", min_length=12) is None


def test_is_safe_redirect():
    assert is_safe_redirect("/me")
    assert is_safe_redirect("/admin?day=2026-01-01")
    assert not is_safe_redirect("https://evil.example/")
    assert not is_safe_redirect("//evil.example/")


def test_login_rejects_external_next_redirect(client):
    resp = client.post(
        "/login?next=https://evil.example/",
        data={"username": "boss", "password": "adminpass-long"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/")


def test_load_server_config_from_toml(tmp_path):
    from timetrack.server.config import load_server_config

    p = tmp_path / "server.toml"
    p.write_text(
        """
hsts_max_age = 86400
force_https = true
min_password_length = 16
admin_ip_allowlist = ["192.168.1.1"]
referrer_policy = "no-referrer"
""".strip()
    )
    cfg = load_server_config(p)
    assert cfg.hsts_max_age == 86400
    assert cfg.force_https is True
    assert cfg.min_password_length == 16
    assert cfg.admin_ip_allowlist == ["192.168.1.1"]
    assert cfg.referrer_policy == "no-referrer"
