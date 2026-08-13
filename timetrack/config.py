"""Configuration loading and app/website categorization.

Config is read from a TOML file (Python 3.11+ ships ``tomllib``). If no file
is found, sensible defaults are used so the tracker works out of the box.

Categories:

- ``productive``   -> counts positively toward the productivity score
- ``unproductive`` -> counts negatively
- ``neutral``      -> ignored by the score (default for unknown apps)

Website/domain rules (``sites_productive`` / ``sites_unproductive``) are
checked first when a URL/domain can be inferred.
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
SITES_PRODUCTIVE = "sites_productive"
SITES_UNPRODUCTIVE = "sites_unproductive"
VALID_CATEGORIES = {PRODUCTIVE, UNPRODUCTIVE, NEUTRAL}
VALID_RULE_KEYS = VALID_CATEGORIES | {SITES_PRODUCTIVE, SITES_UNPRODUCTIVE}

def _default_db_path() -> str:
    from .userdirs import data_dir

    return str(data_dir("timetrack") / "timetrack.db")


DEFAULT_DB_PATH = _default_db_path()

# Substrings are matched case-insensitively against "<app> <title>".
DEFAULT_RULES: dict[str, list[str]] = {
    PRODUCTIVE: [
        "code", "vim", "nvim", "emacs", "pycharm", "intellij", "sublime",
        "terminal", "iterm", "konsole", "gnome-terminal", "alacritty", "kitty",
        "windowsterminal", "wt.exe", "powershell", "pwsh", "cmd.exe",
        "jupyter", "docker", "kubectl", "postman", "dbeaver", "pgadmin",
        "libreoffice", "word", "excel", "powerpoint", "winword", "notion",
        "obsidian", "github", "gitlab", "stackoverflow", "jira", "confluence",
        "outlook", "teams", "slack",
    ],
    UNPRODUCTIVE: [
        "youtube", "netflix", "twitch", "hulu", "disney",
        "facebook", "instagram", "tiktok", "reddit", "twitter", " x.com",
        "steam", "epicgames", "discord", "9gag", "pinterest",
    ],
}

# Domain needles matched against inferred hostname (e.g. youtube.com).
DEFAULT_SITE_RULES: dict[str, list[str]] = {
    SITES_PRODUCTIVE: [
        "docs.google.com",
        "drive.google.com",
        "github.com",
        "gitlab.com",
        "bitbucket.org",
        "stackoverflow.com",
        "notion.so",
        "figma.com",
        "atlassian.net",
        "linear.app",
        "slack.com",
        "office.com",
        "microsoft.com",
        "chatgpt.com",
        "claude.ai",
    ],
    SITES_UNPRODUCTIVE: [
        "youtube.com",
        "netflix.com",
        "facebook.com",
        "instagram.com",
        "tiktok.com",
        "reddit.com",
        "twitter.com",
        "x.com",
        "twitch.tv",
        "pinterest.com",
        "9gag.com",
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

    def categorize(self, app: str, title: str = "", url: str = "") -> str:
        """Classify an activity as productive/unproductive/neutral.

        Domain rules win when a website can be inferred; then app/title rules.
        Unproductive always wins ties.
        """
        domain = _activity_domain(app, title, url)
        if domain:
            if _domain_matches(domain, self.rules.get(SITES_UNPRODUCTIVE, [])):
                return UNPRODUCTIVE
            if _domain_matches(domain, self.rules.get(SITES_PRODUCTIVE, [])):
                return PRODUCTIVE

        haystack = f"{app} {title}".lower()
        for category in (UNPRODUCTIVE, PRODUCTIVE):
            for needle in self.rules.get(category, []):
                if needle.strip().lower() in haystack:
                    return category
        return NEUTRAL


def _activity_domain(app: str, title: str, url: str = "") -> str:
    from .monitor import extract_domain, extract_url

    raw = (url or "").strip() or extract_url(app, title)
    return extract_domain(raw)


def _domain_matches(domain: str, needles: list[str]) -> bool:
    d = (domain or "").lower().lstrip(".")
    if not d:
        return False
    for needle in needles:
        n = (needle or "").strip().lower().lstrip(".")
        if not n:
            continue
        if d == n or d.endswith("." + n) or n in d:
            return True
    return False


def merge_rules(user_rules: dict[str, list[str]]) -> dict[str, list[str]]:
    merged: dict[str, list[str]] = {k: list(v) for k, v in DEFAULT_RULES.items()}
    for k, v in DEFAULT_SITE_RULES.items():
        merged[k] = list(v)
    for category, needles in (user_rules or {}).items():
        if category not in VALID_RULE_KEYS:
            continue
        merged.setdefault(category, [])
        merged[category].extend(str(n) for n in needles)
    return merged


# Back-compat aliases
_DEFAULT_RULES = DEFAULT_RULES
_merge_rules = merge_rules


def default_config_paths() -> list[Path]:
    from .userdirs import config_dir

    return [
        Path.cwd() / "config.toml",
        config_dir("timeforge") / "config.toml",
        config_dir("timetrack") / "config.toml",
        Path(os.path.expanduser("~")) / ".config" / "timetrack" / "config.toml",
    ]


def load_config(path: str | os.PathLike[str] | None = None) -> Config:
    """Load configuration, falling back to defaults + a discovery search."""
    from .userdirs import expand_path

    cfg = Config(rules=merge_rules({}))

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

    cfg.db_path = expand_path(str(data.get("db_path", cfg.db_path)))
    cfg.poll_interval = float(data.get("poll_interval", cfg.poll_interval))
    cfg.idle_threshold = float(data.get("idle_threshold", cfg.idle_threshold))
    cfg.host = str(data.get("host", cfg.host))
    cfg.port = int(data.get("port", cfg.port))
    cfg.rules = merge_rules(data.get("rules", {}) or {})
    return cfg


__all__ = [
    "Config",
    "load_config",
    "default_config_paths",
    "PRODUCTIVE",
    "UNPRODUCTIVE",
    "NEUTRAL",
    "SITES_PRODUCTIVE",
    "SITES_UNPRODUCTIVE",
    "VALID_CATEGORIES",
    "DEFAULT_DB_PATH",
    "DEFAULT_RULES",
    "DEFAULT_SITE_RULES",
    "merge_rules",
]
