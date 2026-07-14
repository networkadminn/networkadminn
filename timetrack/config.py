"""Configuration loading and app/website categorization.

Config is read from a TOML file (Python 3.11+ ships ``tomllib``). If no file
is found, sensible defaults are used so the tracker works out of the box.

Categories mirror DeskTime's model:

- ``productive``   -> counts positively toward the productivity score
- ``unproductive`` -> counts negatively
- ``neutral``      -> ignored by the score (default for unknown apps)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None  # type: ignore[assignment]

PRODUCTIVE = "productive"
UNPRODUCTIVE = "unproductive"
NEUTRAL = "neutral"
VALID_CATEGORIES = {PRODUCTIVE, UNPRODUCTIVE, NEUTRAL}

DEFAULT_DB_PATH = os.path.join(
    os.path.expanduser("~"), ".local", "share", "timetrack", "timetrack.db"
)

# Substrings are matched case-insensitively against "<app> <title>".
_DEFAULT_RULES: dict[str, list[str]] = {
    PRODUCTIVE: [
        "code", "vim", "nvim", "emacs", "pycharm", "intellij", "sublime",
        "terminal", "iterm", "konsole", "gnome-terminal", "alacritty", "kitty",
        "jupyter", "docker", "kubectl", "postman", "dbeaver", "pgadmin",
        "libreoffice", "word", "excel", "powerpoint", "notion", "obsidian",
        "github", "gitlab", "stackoverflow", "jira", "confluence",
    ],
    UNPRODUCTIVE: [
        "youtube", "netflix", "twitch", "hulu", "disney",
        "facebook", "instagram", "tiktok", "reddit", "twitter", " x.com",
        "steam", "epicgames", "discord", "9gag", "pinterest",
    ],
}


@dataclass
class Config:
    db_path: str = DEFAULT_DB_PATH
    poll_interval: float = 5.0
    """Seconds between activity samples."""
    idle_threshold: float = 180.0
    """Seconds of no input after which time is counted as idle."""
    rules: dict[str, list[str]] = field(default_factory=dict)
    host: str = "127.0.0.1"
    port: int = 8000

    def categorize(self, app: str, title: str = "") -> str:
        """Classify an activity as productive/unproductive/neutral."""
        haystack = f"{app} {title}".lower()
        # Unproductive wins ties (e.g. YouTube open in a "productive" browser).
        for category in (UNPRODUCTIVE, PRODUCTIVE):
            for needle in self.rules.get(category, []):
                if needle.strip().lower() in haystack:
                    return category
        return NEUTRAL


def _merge_rules(user_rules: dict[str, list[str]]) -> dict[str, list[str]]:
    merged: dict[str, list[str]] = {k: list(v) for k, v in _DEFAULT_RULES.items()}
    for category, needles in user_rules.items():
        if category not in VALID_CATEGORIES:
            continue
        merged.setdefault(category, [])
        merged[category].extend(str(n) for n in needles)
    return merged


def default_config_paths() -> list[Path]:
    return [
        Path.cwd() / "config.toml",
        Path(os.path.expanduser("~")) / ".config" / "timetrack" / "config.toml",
    ]


def load_config(path: str | os.PathLike[str] | None = None) -> Config:
    """Load configuration, falling back to defaults + a discovery search."""
    cfg = Config(rules=_merge_rules({}))

    candidate: Path | None = None
    if path is not None:
        candidate = Path(path)
    else:
        for p in default_config_paths():
            if p.is_file():
                candidate = p
                break

    if candidate is None or not candidate.is_file() or tomllib is None:
        return cfg

    with open(candidate, "rb") as fh:
        data = tomllib.load(fh)

    cfg.db_path = str(data.get("db_path", cfg.db_path))
    cfg.poll_interval = float(data.get("poll_interval", cfg.poll_interval))
    cfg.idle_threshold = float(data.get("idle_threshold", cfg.idle_threshold))
    cfg.host = str(data.get("host", cfg.host))
    cfg.port = int(data.get("port", cfg.port))
    cfg.rules = _merge_rules(data.get("rules", {}) or {})
    return cfg


__all__ = [
    "Config",
    "load_config",
    "default_config_paths",
    "PRODUCTIVE",
    "UNPRODUCTIVE",
    "NEUTRAL",
    "VALID_CATEGORIES",
    "DEFAULT_DB_PATH",
]
