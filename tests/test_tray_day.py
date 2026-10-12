"""Tray day JSON API for desktop flyout."""


def test_tray_day_requires_token(client):
    assert client.get("/api/v1/tray-day").status_code == 401


def test_tray_day_ok(client, app):
    token = app.config["_EMP_TOKEN"]
    resp = client.get("/api/v1/tray-day", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert "dt_productivity" in data
    assert "mix_pct" in data
    assert "top_apps" in data
