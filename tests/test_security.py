"""Tests for HTTP security headers, password policy, MFA, and admin IP allow-listing."""

import pytest

from timetrack.server import create_app
from timetrack.server.extensions import db
from timetrack.server.models import ROLE_ADMIN, ROLE_EMPLOYEE, User
from timetrack.server.security import (
    WeakPasswordError,
    client_ip_allowed,
    generate_mfa_secret,
    mfa_provisioning_uri,
    password_policy_violations,
    validate_password_strength,
    verify_mfa_code,
)

STRONG_ADMIN_PW = "Adm1n-Sup3r-Secret!"
STRONG_EMP_PW = "Al1ce-Sup3r-Secret!"


@pytest.fixture()
def app(tmp_path):
    app = create_app(
        data_dir=str(tmp_path / "data"),
        database_uri="sqlite:///" + str(tmp_path / "test.db"),
        secret_key="test-secret",
    )
    app.config.update(TESTING=True)
    with app.app_context():
        admin = User(username="boss", role=ROLE_ADMIN, display_name="The Boss")
        admin.set_password(STRONG_ADMIN_PW)
        emp = User(username="alice", role=ROLE_EMPLOYEE, display_name="Alice")
        emp.set_password(STRONG_EMP_PW)
        db.session.add_all([admin, emp])
        db.session.commit()
    return app


@pytest.fixture()
def client(app):
    return app.test_client()


def _login(client, username, password):
    return client.post(
        "/login", data={"username": username, "password": password},
        follow_redirects=False,
    )


# --- Security headers -------------------------------------------------------


def test_security_headers_present_on_login_page(client):
    resp = client.get("/login")
    assert resp.headers.get("X-Content-Type-Options") == "nosniff"
    assert resp.headers.get("X-Frame-Options") == "SAMEORIGIN"
    assert resp.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"
    assert "default-src 'self'" in resp.headers.get("Content-Security-Policy", "")


def test_hsts_absent_over_plain_http(client):
    resp = client.get("/login")
    assert "Strict-Transport-Security" not in resp.headers


def test_hsts_present_when_forwarded_https(client):
    resp = client.get("/login", headers={"X-Forwarded-Proto": "https"})
    hsts = resp.headers.get("Strict-Transport-Security", "")
    assert "max-age=" in hsts
    assert "includeSubDomains" in hsts


def test_security_headers_can_be_disabled(tmp_path):
    app = create_app(
        data_dir=str(tmp_path / "data2"),
        database_uri="sqlite:///" + str(tmp_path / "test2.db"),
        secret_key="test-secret",
        enable_security_headers=False,
    )
    resp = app.test_client().get("/login")
    assert "Content-Security-Policy" not in resp.headers
    assert "X-Frame-Options" not in resp.headers


# --- Password policy ---------------------------------------------------------


def test_password_policy_rejects_short_password():
    issues = password_policy_violations("Sh0rt!")
    assert any("characters long" in i for i in issues)


def test_password_policy_rejects_low_variety():
    issues = password_policy_violations("alllowercaseletters")
    assert any("three of" in i for i in issues)


def test_password_policy_rejects_common_password():
    issues = password_policy_violations("Password123")
    # too short AND common/low-variety depending on casing; just assert not empty
    assert issues


def test_password_policy_accepts_strong_password():
    assert password_policy_violations(STRONG_ADMIN_PW) == []


def test_validate_password_strength_raises():
    with pytest.raises(WeakPasswordError):
        validate_password_strength("weak")


def test_user_set_password_enforces_policy(app):
    with app.app_context():
        u = User(username="newbie", role=ROLE_EMPLOYEE)
        with pytest.raises(WeakPasswordError):
            u.set_password("weak")
        # Strong password succeeds.
        u.set_password("Str0ng-Enough-Pass!")
        assert u.check_password("Str0ng-Enough-Pass!")


def test_user_set_password_can_bypass_policy(app):
    with app.app_context():
        u = User(username="legacy", role=ROLE_EMPLOYEE)
        u.set_password("x", enforce_policy=False)
        assert u.check_password("x")


# --- MFA (TOTP) --------------------------------------------------------------


def test_mfa_disabled_by_default(app):
    with app.app_context():
        user = db.session.execute(
            db.select(User).filter_by(username="boss")
        ).scalar_one()
        assert not user.mfa_enabled
        assert not user.check_mfa_code("123456")


def test_mfa_secret_and_provisioning_uri():
    secret = generate_mfa_secret()
    assert len(secret) >= 16
    uri = mfa_provisioning_uri(secret, username="boss")
    assert uri.startswith("otpauth://totp/")
    assert "boss" in uri


def test_verify_mfa_code_roundtrip():
    import pyotp

    secret = generate_mfa_secret()
    code = pyotp.TOTP(secret).now()
    assert verify_mfa_code(secret, code)
    assert not verify_mfa_code(secret, "000000")


def test_login_requires_mfa_code_when_enabled(app, client):
    import pyotp

    with app.app_context():
        user = db.session.execute(
            db.select(User).filter_by(username="boss")
        ).scalar_one()
        secret = generate_mfa_secret()
        user.mfa_secret = secret
        db.session.commit()

    # Correct password, but no MFA code yet -> redirected to the MFA step,
    # not logged in.
    resp = _login(client, "boss", STRONG_ADMIN_PW)
    assert resp.status_code == 302
    assert "/login/verify" in resp.headers["Location"]

    home = client.get("/", follow_redirects=False)
    assert home.status_code == 302
    assert "/login" in home.headers["Location"]

    # Wrong code is rejected.
    bad = client.post("/login/verify", data={"code": "000000"})
    assert b"Invalid authentication code" in bad.data

    # Correct code logs the user in.
    with app.app_context():
        code = pyotp.TOTP(secret).now()
    good = client.post("/login/verify", data={"code": code}, follow_redirects=True)
    assert good.status_code == 200
    assert b"Team overview" in good.data


# --- Admin IP allow-listing ---------------------------------------------------


def test_client_ip_allowed_empty_allowlist_permits_all():
    assert client_ip_allowed("203.0.113.9", [])


def test_client_ip_allowed_matches_single_ip():
    assert client_ip_allowed("127.0.0.1", ["127.0.0.1"])
    assert not client_ip_allowed("127.0.0.2", ["127.0.0.1"])


def test_client_ip_allowed_matches_cidr():
    assert client_ip_allowed("10.1.2.3", ["10.0.0.0/8"])
    assert not client_ip_allowed("192.168.1.1", ["10.0.0.0/8"])


def test_admin_route_blocked_when_ip_not_allowlisted(tmp_path):
    app = create_app(
        data_dir=str(tmp_path / "data3"),
        database_uri="sqlite:///" + str(tmp_path / "test3.db"),
        secret_key="test-secret",
        admin_ip_allowlist=["203.0.113.0/24"],  # test client is 127.0.0.1
    )
    with app.app_context():
        admin = User(username="boss", role=ROLE_ADMIN)
        admin.set_password(STRONG_ADMIN_PW)
        db.session.add(admin)
        db.session.commit()

    client = app.test_client()
    _login(client, "boss", STRONG_ADMIN_PW)
    resp = client.get("/admin")
    assert resp.status_code == 403


def test_admin_route_allowed_when_ip_allowlisted(tmp_path):
    app = create_app(
        data_dir=str(tmp_path / "data4"),
        database_uri="sqlite:///" + str(tmp_path / "test4.db"),
        secret_key="test-secret",
        admin_ip_allowlist=["127.0.0.1/32"],  # matches the test client
    )
    with app.app_context():
        admin = User(username="boss", role=ROLE_ADMIN)
        admin.set_password(STRONG_ADMIN_PW)
        db.session.add(admin)
        db.session.commit()

    client = app.test_client()
    _login(client, "boss", STRONG_ADMIN_PW)
    resp = client.get("/admin")
    assert resp.status_code == 200
