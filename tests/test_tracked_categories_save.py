"""Save categories from tracked-item rows only."""

from __future__ import annotations

from pathlib import Path

from timetrack.server.settings_util import save_tracked_category_rules


def test_save_updates_tracked_and_preserves_other_rules(tmp_path, monkeypatch):
    from timetrack.server import create_app
    from timetrack.server.extensions import db
    from timetrack.server.settings_util import rules_path

    app = create_app(
        data_dir=str(tmp_path / "data"),
        database_uri="sqlite:///" + str(tmp_path / "test.db"),
        secret_key="test-secret",
    )
    with app.app_context():
        db.create_all()
        path = rules_path(1)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(
            '{"productive":["vscode"],"unproductive":[],"neutral":[],"sites_productive":[],"sites_unproductive":[]}',
            encoding="utf-8",
        )
        payload = save_tracked_category_rules(
            1,
            [("slack", "productive"), ("chrome", "neutral")],
            [("github.com", "productive")],
        )
        assert "slack" in [x.lower() for x in payload["productive"]]
        assert "vscode" in payload["productive"]
        assert "chrome" in payload["neutral"]
        assert "github.com" in payload["sites_productive"]
