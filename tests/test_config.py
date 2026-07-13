from timetrack.config import NEUTRAL, PRODUCTIVE, UNPRODUCTIVE, Config, load_config


def test_default_categorization():
    cfg = load_config("/nonexistent/config.toml")
    assert cfg.categorize("Code.exe", "main.py - project") == PRODUCTIVE
    assert cfg.categorize("firefox", "YouTube - Watch") == UNPRODUCTIVE
    assert cfg.categorize("weird-unknown-app", "random title") == NEUTRAL


def test_unproductive_wins_tie():
    # "github" is productive, "youtube" is unproductive; both present -> unproductive.
    cfg = load_config("/nonexistent/config.toml")
    assert cfg.categorize("chrome", "github vs youtube") == UNPRODUCTIVE


def test_config_from_toml(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(
        """
poll_interval = 10
idle_threshold = 60
port = 9999

[rules]
productive = ["myeditor"]
unproductive = ["mygame"]
""".strip()
    )
    cfg = load_config(p)
    assert cfg.poll_interval == 10
    assert cfg.idle_threshold == 60
    assert cfg.port == 9999
    assert cfg.categorize("myeditor", "") == PRODUCTIVE
    assert cfg.categorize("mygame", "") == UNPRODUCTIVE
    # Built-in defaults still apply (merged).
    assert cfg.categorize("code", "") == PRODUCTIVE


def test_config_dataclass_defaults():
    cfg = Config()
    assert cfg.poll_interval == 5.0
    assert cfg.idle_threshold == 180.0
