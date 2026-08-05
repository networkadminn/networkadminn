import io
import time

import pytest

from timetrack.server import create_app
from timetrack.server.extensions import db
from timetrack.server.models import (
    ROLE_ADMIN,
    ROLE_EMPLOYEE,
    User,
    validate_password_strength,
)

ADMIN_PASSWORD = "Admin-Passphrase-123"
EMPLOYEE_PASSWORD = "Employee-Passphrase-123"


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
        admin.set_password(ADMIN_PASSWORD)
        emp = User(username="alice", role=ROLE_EMPLOYEE, display_name="Alice")
        emp.set_password(EMPLOYEE_PASSWORD)
        db.session.add_all([admin, emp])
        db.session.commit()
        app.config["_ADMIN_TOKEN"] = admin.api_token
        app.config["_EMP_TOKEN"] = emp.api_token
        app.config["_EMP_ID"] = emp.id
    return app


@pytest.fixture()
def client(app):
    return app.test_client()


def _login(client, username, password):
    return client.post(
        "/login", data={"username": username, "password": password},
        follow_redirects=True,
    )


def test_login_required_redirect(client):
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_admin_login_sees_team(client):
    resp = _login(client, "boss", ADMIN_PASSWORD)
    assert resp.status_code == 200
    assert b"Team overview" in resp.data


def test_employee_cannot_access_admin(client):
    _login(client, "alice", EMPLOYEE_PASSWORD)
    resp = client.get("/admin")
    assert resp.status_code == 403


def test_bad_password(client):
    resp = _login(client, "boss", "wrong")
    assert b"Invalid username or password" in resp.data


def test_server_sets_security_headers(client):
    resp = client.get("/login")
    assert resp.headers["Strict-Transport-Security"] == (
        "max-age=31536000; includeSubDomains"
    )
    assert resp.headers["X-Frame-Options"] == "SAMEORIGIN"
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert "default-src 'self'" in resp.headers["Content-Security-Policy"]


def test_login_rejects_external_next_redirect(client):
    resp = client.post(
        "/login?next=https://evil.example/phish",
        data={"username": "boss", "password": ADMIN_PASSWORD},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/"


def test_password_strength_validation():
    assert validate_password_strength("short", username="boss")
    assert not validate_password_strength("Correct-Horse-123", username="boss")


def test_admin_ip_allowlist(client, app):
    app.config["TIMETRACK_SERVER_CONFIG"].admin_allowed_ips = ("203.0.113.0/24",)
    _login(client, "boss", ADMIN_PASSWORD)
    blocked = client.get("/admin", environ_overrides={"REMOTE_ADDR": "198.51.100.5"})
    assert blocked.status_code == 403
    allowed = client.get("/admin", environ_overrides={"REMOTE_ADDR": "203.0.113.8"})
    assert allowed.status_code == 200


def test_api_requires_token(client):
    resp = client.post("/api/v1/activities", json={"activities": []})
    assert resp.status_code == 401


def test_api_ingest_activities(client, app):
    token = app.config["_EMP_TOKEN"]
    now = time.time()
    resp = client.post(
        "/api/v1/activities",
        json={"activities": [
            {"app": "code", "title": "main.py", "category": "productive",
             "idle": False, "start_ts": now - 60, "end_ts": now, "duration": 60},
            {"app": "firefox", "title": "youtube", "category": "unproductive",
             "idle": False, "start_ts": now - 30, "end_ts": now, "duration": 30},
        ]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    assert resp.get_json()["accepted"] == 2


def test_api_ingest_screenshot_and_serve(client, app):
    token = app.config["_EMP_TOKEN"]
    # 1x1 red JPEG via Pillow.
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (200, 30, 30)).save(buf, format="JPEG")
    buf.seek(0)

    resp = client.post(
        "/api/v1/screenshots",
        data={"ts": str(time.time()), "app": "code", "title": "x",
              "image": (buf, "shot.jpg")},
        headers={"Authorization": f"Bearer {token}"},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 201
    shot_id = resp.get_json()["id"]

    # Admin can view it.
    _login(client, "boss", ADMIN_PASSWORD)
    img = client.get(f"/screenshot/{shot_id}")
    assert img.status_code == 200
    assert img.data[:2] == b"\xff\xd8"  # JPEG magic


def test_employee_dashboard_shows_own_data(client, app):
    token = app.config["_EMP_TOKEN"]
    now = time.time()
    client.post(
        "/api/v1/activities",
        json={"activities": [
            {"app": "code", "title": "m.py", "category": "productive",
             "idle": False, "start_ts": now - 120, "end_ts": now, "duration": 120},
        ]},
        headers={"Authorization": f"Bearer {token}"},
    )
    _login(client, "alice", EMPLOYEE_PASSWORD)
    resp = client.get("/me")
    assert resp.status_code == 200
    assert b"Productivity" in resp.data


def test_employee_cannot_view_others_screenshot(client, app):
    # Employee alice cannot access a screenshot owned by someone else.
    admin_token = app.config["_ADMIN_TOKEN"]
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (8, 8)).save(buf, format="JPEG")
    buf.seek(0)
    resp = client.post(
        "/api/v1/screenshots",
        data={"ts": str(time.time()), "image": (buf, "s.jpg")},
        headers={"Authorization": f"Bearer {admin_token}"},
        content_type="multipart/form-data",
    )
    shot_id = resp.get_json()["id"]

    _login(client, "alice", EMPLOYEE_PASSWORD)
    assert client.get(f"/screenshot/{shot_id}").status_code == 403
