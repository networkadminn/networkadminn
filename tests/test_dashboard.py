from timetrack.config import Config
from timetrack.dashboard import create_app
from timetrack.storage import Storage


def _app(tmp_path):
    db = str(tmp_path / "t.db")
    with Storage(db) as s:
        s.record("code", "main.py", "productive", False, 120.0)
        s.record("firefox", "youtube", "unproductive", False, 60.0)
    cfg = Config(db_path=db)
    app = create_app(cfg)
    app.config.update(TESTING=True)
    return app


def test_healthz(tmp_path):
    client = _app(tmp_path).test_client()
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "ok"


def test_dashboard_sets_security_headers(tmp_path):
    client = _app(tmp_path).test_client()
    resp = client.get("/")
    assert resp.headers["Strict-Transport-Security"] == (
        "max-age=31536000; includeSubDomains"
    )
    assert resp.headers["X-Frame-Options"] == "SAMEORIGIN"
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert "default-src 'self'" in resp.headers["Content-Security-Policy"]


def test_index_renders(tmp_path):
    client = _app(tmp_path).test_client()
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"TimeTrack" in resp.data


def test_api_summary(tmp_path):
    client = _app(tmp_path).test_client()
    data = client.get("/api/summary").get_json()
    assert "productivity_pct" in data
    assert data["active_seconds"] >= 180.0


def test_api_timeline(tmp_path):
    client = _app(tmp_path).test_client()
    data = client.get("/api/timeline").get_json()
    assert "buckets" in data
    assert isinstance(data["buckets"], list)
