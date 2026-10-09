"""Agent version reporting and the update-available offer."""

from __future__ import annotations

import time

import pytest

from timetrack import __version__
from timetrack.agent import update_ui
from timetrack.server import create_app
from timetrack.server.extensions import db
from timetrack.server.models import ROLE_EMPLOYEE, User
from timetrack.server.releases import is_outdated, version_tuple


@pytest.fixture()
def app(tmp_path):
    app = create_app(
        data_dir=str(tmp_path / "data"),
        database_uri="sqlite:///" + str(tmp_path / "test.db"),
        secret_key="test-secret",
    )
    app.config.update(TESTING=True)
    with app.app_context():
        emp = User(username="alice", role=ROLE_EMPLOYEE, display_name="Alice")
        emp.set_password("alicepass")
        db.session.add(emp)
        db.session.commit()
        app.config["_EMP_TOKEN"] = emp.api_token
    return app


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def agent_token(app):
    return app.config["_EMP_TOKEN"]


def test_version_compare():
    assert version_tuple("0.3.10") > version_tuple("0.3.2")
    assert is_outdated("0.0.1")
    assert is_outdated(None)
    assert not is_outdated(__version__)


@pytest.fixture()
def publish(tmp_path, monkeypatch):
    rel = tmp_path / "releases"
    rel.mkdir()
    monkeypatch.setenv("ESSTRACKER_RELEASES_DIR", str(rel))

    def _publish(version):
        (rel / f"esstracker-Setup-{version}.exe").write_bytes(b"MZ")

    return _publish


def _ping(client, token, version):
    return client.get(
        "/api/v1/ping",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Agent-Version": version,
            "X-Agent-Platform": "windows",
        },
    ).get_json()


def test_ping_offers_update_only_when_outdated(client, agent_token, publish):
    publish(__version__)
    old = _ping(client, agent_token, "0.3.1")
    assert old["client_update"]["latest"] == __version__
    assert old["client_update"]["url"].endswith(f"esstracker-Setup-{__version__}.exe")
    assert old["client_update"]["url"].startswith("/")
    assert _ping(client, agent_token, __version__)["client_update"] is None


def test_no_offer_when_installer_missing(client, agent_token, publish):
    publish("0.0.1")
    assert _ping(client, agent_token, "0.3.1")["client_update"] is None


def test_web_banner_for_outdated_agent(client, agent_token, publish):
    publish(__version__)
    _ping(client, agent_token, "0.3.1")
    client.post("/login", data={"username": "alice", "password": "alicepass"})
    html = client.get("/me").get_data(as_text=True)
    assert "Download update" in html
    _ping(client, agent_token, __version__)
    assert "Download update" not in client.get("/me").get_data(as_text=True)


def test_snooze(tmp_path):
    assert not update_ui._snoozed(str(tmp_path), "9.9.9")
    update_ui._snooze(str(tmp_path), "9.9.9")
    assert update_ui._snoozed(str(tmp_path), "9.9.9")
    assert not update_ui._snoozed(str(tmp_path), "9.9.10")
    (tmp_path / "update_snooze.json").write_text(
        '{"version": "9.9.9", "until": %f}' % (time.time() - 1)
    )
    assert not update_ui._snoozed(str(tmp_path), "9.9.9")
