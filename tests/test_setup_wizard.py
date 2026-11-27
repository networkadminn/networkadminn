"""First-time setup wizard (empty install → create admin)."""

from __future__ import annotations

import io

import pytest
from PIL import Image

from timetrack.server import create_app
from timetrack.server.extensions import db
from timetrack.server.models import ROLE_ADMIN, CompanySettings, Organization, User
from timetrack.server.setup_util import needs_setup


@pytest.fixture()
def empty_app(tmp_path):
    app = create_app(
        data_dir=str(tmp_path / "data"),
        database_uri="sqlite:///" + str(tmp_path / "test.db"),
        secret_key="test-secret",
    )
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    return app


@pytest.fixture()
def client(empty_app):
    return empty_app.test_client()


def test_needs_setup_when_no_admin(empty_app):
    with empty_app.app_context():
        assert needs_setup() is True


def test_empty_install_redirects_to_setup(client):
    resp = client.get("/login", follow_redirects=False)
    assert resp.status_code == 302
    assert "/setup" in resp.headers["Location"]
    resp = client.get("/admin", follow_redirects=False)
    assert resp.status_code == 302
    assert "/setup" in resp.headers["Location"]


def test_setup_page_renders(client):
    resp = client.get("/setup")
    assert resp.status_code == 200
    assert b"First-time setup" in resp.data
    assert b"Company" in resp.data or b"brand" in resp.data.lower()
    assert b"Admin account" in resp.data


def test_complete_setup_creates_admin_and_brand(client, empty_app, tmp_path):
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), color=(11, 122, 75)).save(buf, format="PNG")
    buf.seek(0)

    resp = client.post(
        "/setup",
        data={
            "company_name": "Acme Tracker",
            "company_website": "https://acme.example",
            "company_email": "ops@acme.example",
            "timezone": "Asia/Dubai",
            "admin_name": "Ada Admin",
            "username": "ada",
            "admin_email": "ada@acme.example",
            "password": "secret12",
            "password_confirm": "secret12",
            "logo": (buf, "logo.png"),
        },
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]

    with empty_app.app_context():
        assert needs_setup() is False
        user = db.session.execute(db.select(User).filter_by(username="ada")).scalar_one()
        assert user.role == ROLE_ADMIN
        assert user.display_name == "Ada Admin"
        assert user.email == "ada@acme.example"
        assert user.check_password("secret12")
        settings = db.session.execute(db.select(CompanySettings)).scalar_one()
        assert settings.company_name == "Acme Tracker"
        assert settings.company_website == "https://acme.example"
        assert settings.timezone == "Asia/Dubai"
        assert settings.company_logo.startswith("brand/logo.")
        org = db.session.get(Organization, 1)
        assert org is not None
        assert org.name == "Acme Tracker"
        assert org.billing_email == "ops@acme.example"

    # Setup no longer available; login works.
    again = client.get("/setup", follow_redirects=False)
    assert again.status_code == 302
    assert "/login" in again.headers["Location"]
    login = client.post(
        "/login",
        data={"username": "ada", "password": "secret12"},
        follow_redirects=False,
    )
    assert login.status_code == 302
