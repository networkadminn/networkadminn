"""Tests for AI day reports."""

import json
from datetime import datetime
from unittest.mock import patch

import pytest

from timetrack.server import create_app
from timetrack.server.extensions import db
from timetrack.server.models import Activity, DayAiReport, ROLE_ADMIN, ROLE_EMPLOYEE, User


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    app = create_app(
        data_dir=str(tmp_path / "data"),
        database_uri="sqlite:///" + str(tmp_path / "test.db"),
        secret_key="test-secret",
    )
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    with app.app_context():
        admin = User(
            username="boss",
            role=ROLE_ADMIN,
            display_name="The Boss",
            email="boss@example.com",
        )
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


def _login(client, username, password):
    return client.post(
        "/login",
        data={"username": username, "password": password},
        follow_redirects=True,
    )


def test_admin_user_shows_ai_report_section(client):
    _login(client, "boss", "adminpass")
    emp_id = client.application.config["_EMP_ID"]
    resp = client.get(f"/admin/user/{emp_id}")
    assert resp.status_code == 200
    assert b"AI day report" in resp.data
    assert b"Generate AI report" in resp.data


def test_generate_ai_report(client, app):
    _login(client, "boss", "adminpass")
    emp_id = app.config["_EMP_ID"]
    day = "2026-07-15"
    start = datetime(2026, 7, 15, 10, 0).timestamp()
    with app.app_context():
        db.session.add(
            Activity(
                user_id=emp_id,
                app="Cursor",
                title="networkadminn-1",
                category="productive",
                start_ts=start,
                end_ts=start + 3600,
                duration=3600,
            )
        )
        db.session.commit()

    fake_response = json.dumps(
        {
            "candidates": [
                {"content": {"parts": [{"text": "Alice spent an hour coding in Cursor."}]}}
            ]
        }
    ).encode()

    class FakeResp:
        def read(self):
            return fake_response

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    with patch("timetrack.server.ai_report.urllib.request.urlopen", return_value=FakeResp()):
        resp = client.post(
            f"/admin/user/{emp_id}/ai-report",
            data={"day": day, "action": "generate"},
            follow_redirects=True,
        )
    assert resp.status_code == 200
    assert b"AI day report generated" in resp.data
    assert b"Alice spent an hour coding in Cursor." in resp.data
    with app.app_context():
        report = db.session.execute(
            db.select(DayAiReport).filter_by(user_id=emp_id, day=day)
        ).scalar_one()
        assert "Cursor" in report.summary


def test_logout_triggers_ai_report_when_enabled(client, app, monkeypatch):
    from timetrack.server.settings_util import get_settings

    with app.app_context():
        settings = get_settings()
        settings.email_ai_report_on_logout = True
        settings.ai_report_after_office_only = False
        db.session.commit()

    called = {"n": 0}

    def fake_maybe(user):
        called["n"] += 1

    monkeypatch.setattr(
        "timetrack.server.ai_report.maybe_send_ai_report_on_logout", fake_maybe
    )
    _login(client, "alice", "alicepass")
    client.get("/logout", follow_redirects=True)
    assert called["n"] == 1
