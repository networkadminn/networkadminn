"""Persist and load employee agent config (DeskTime-style zero-touch)."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from ..config import Config as _CoreConfig
from ..config import merge_rules as _merge_rules
from ..userdirs import config_dir, data_dir, expand_path, install_dir

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None  # type: ignore[assignment]

DEFAULT_SERVER_URL = "https://tracker.euclideesolutions.com"
DEFAULT_BUFFER_PATH = str(data_dir() / "agent-buffer.db")
DEFAULT_SHOTS_DIR = str(data_dir() / "agent-shots")
USER_CONFIG_PATH = config_dir() / "agent.toml"
LOCK_PATH = data_dir() / "agent.lock"


@dataclass
class AgentConfig:
    server_url: str = DEFAULT_SERVER_URL
    api_token: str = ""
    poll_interval: float = 5.0
    idle_threshold: float = 180.0
    screenshots_enabled: bool = True
    screenshot_interval: float = 300.0
    screenshot_max_width: int = 1920
    flush_interval: float = 30.0
    batch_size: int = 200
    buffer_path: str = DEFAULT_BUFFER_PATH
    shots_dir: str = DEFAULT_SHOTS_DIR
    rules: dict[str, list[str]] = field(default_factory=lambda: _merge_rules({}))
    config_path: str = ""

    def categorize(self, app: str, title: str = "", url: str = "") -> str:
        core = _CoreConfig(rules=self.rules)
        return core.categorize(app, title, url=url)

    @property
    def is_signed_in(self) -> bool:
        return bool(self.api_token and self.server_url)


def user_config_path() -> Path:
    return USER_CONFIG_PATH


def ensure_data_dirs(cfg: AgentConfig | None = None) -> None:
    USER_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    if cfg:
        Path(cfg.buffer_path).parent.mkdir(parents=True, exist_ok=True)
        Path(cfg.shots_dir).mkdir(parents=True, exist_ok=True)


def _defaults_paths() -> list[Path]:
    """Company defaults.toml locations (deb /etc, Windows next to EXE)."""
    paths: list[Path] = []
    inst = install_dir()
    if inst is not None:
        paths.append(inst / "defaults.toml")
    if sys.platform == "win32":
        progdata = os.environ.get("PROGRAMDATA")
        if progdata:
            paths.append(Path(progdata) / "timeforge" / "defaults.toml")
        local = os.environ.get("LOCALAPPDATA")
        if local:
            paths.append(Path(local) / "Programs" / "timeforge" / "defaults.toml")
    else:
        paths.append(Path("/etc/timeforge/defaults.toml"))
        paths.append(Path("/etc/esstracker/defaults.toml"))  # legacy package
    return paths


def _candidate_paths() -> list[Path]:
    return [
        Path.cwd() / "agent.toml",
        Path.cwd() / "config.toml",
        config_dir() / "agent.toml",
        Path(os.path.expanduser("~")) / ".config" / "timeforge" / "agent.toml",
        Path(os.path.expanduser("~")) / ".config" / "esstracker" / "agent.toml",
        Path(os.path.expanduser("~")) / ".config" / "timetrack" / "agent.toml",
        Path("/etc/timeforge/agent.toml"),
        Path("/etc/esstracker/agent.toml"),
        Path("/etc/timetrack/agent.toml"),
    ]


def _apply_defaults_file(cfg: AgentConfig) -> None:
    if tomllib is None:
        return
    for defaults in _defaults_paths():
        if not defaults.is_file():
            continue
        try:
            with open(defaults, "rb") as fh:
                data = tomllib.load(fh)
            agent = data.get("agent", data)
            if agent.get("server_url"):
                cfg.server_url = str(agent["server_url"])
            return
        except Exception:
            continue


def load_agent_config(path: str | os.PathLike[str] | None = None) -> AgentConfig:
    cfg = AgentConfig()
    _apply_defaults_file(cfg)

    candidate: Path | None = None
    if path is not None:
        candidate = Path(path)
    else:
        for p in _candidate_paths():
            if p.is_file() and p.name != "defaults.toml":
                candidate = p
                break

    if candidate is not None and candidate.is_file() and tomllib is not None:
        with open(candidate, "rb") as fh:
            data = tomllib.load(fh)
        agent = data.get("agent", data)
        cfg.server_url = str(agent.get("server_url", cfg.server_url))
        cfg.api_token = str(agent.get("api_token", cfg.api_token))
        cfg.poll_interval = float(agent.get("poll_interval", cfg.poll_interval))
        cfg.idle_threshold = float(agent.get("idle_threshold", cfg.idle_threshold))
        cfg.screenshots_enabled = bool(
            agent.get("screenshots_enabled", cfg.screenshots_enabled)
        )
        cfg.screenshot_interval = float(
            agent.get("screenshot_interval", cfg.screenshot_interval)
        )
        cfg.screenshot_max_width = int(
            agent.get("screenshot_max_width", cfg.screenshot_max_width)
        )
        cfg.flush_interval = float(agent.get("flush_interval", cfg.flush_interval))
        cfg.batch_size = int(agent.get("batch_size", cfg.batch_size))
        cfg.buffer_path = expand_path(str(agent.get("buffer_path", cfg.buffer_path)))
        cfg.shots_dir = expand_path(str(agent.get("shots_dir", cfg.shots_dir)))
        cfg.rules = _merge_rules(data.get("rules", {}) or {})
        cfg.config_path = str(candidate)

    _apply_env(cfg)
    if not cfg.config_path:
        cfg.config_path = str(USER_CONFIG_PATH)
    ensure_data_dirs(cfg)
    return cfg


def save_agent_config(cfg: AgentConfig, path: str | os.PathLike[str] | None = None) -> Path:
    """Write agent.toml so next launch needs no setup (DeskTime-style)."""
    dest = Path(path or cfg.config_path or USER_CONFIG_PATH)
    dest.parent.mkdir(parents=True, exist_ok=True)

    def q(s: str) -> str:
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'

    text = (
        "# Auto-saved by timeforge — do not share this file\n"
        "[agent]\n"
        f"server_url = {q(cfg.server_url)}\n"
        f"api_token = {q(cfg.api_token)}\n"
        f"poll_interval = {float(cfg.poll_interval)}\n"
        f"idle_threshold = {float(cfg.idle_threshold)}\n"
        f"screenshots_enabled = {'true' if cfg.screenshots_enabled else 'false'}\n"
        f"screenshot_interval = {float(cfg.screenshot_interval)}\n"
        f"flush_interval = {float(cfg.flush_interval)}\n"
        f"buffer_path = {q(cfg.buffer_path)}\n"
        f"shots_dir = {q(cfg.shots_dir)}\n"
    )
    dest.write_text(text, encoding="utf-8")
    try:
        os.chmod(dest, 0o600)
    except OSError:
        pass
    cfg.config_path = str(dest)
    return dest


def clear_saved_token(cfg: AgentConfig | None = None) -> None:
    """Sign out: wipe token from user config (login window shows next run)."""
    cfg = cfg or load_agent_config()
    cfg.api_token = ""
    save_agent_config(cfg)


def _apply_env(cfg: AgentConfig) -> None:
    for key in (
        "TIMEFORGE_SERVER_URL",
        "ESSTRACKER_SERVER_URL",
        "TIMETRACK_SERVER_URL",
    ):
        if os.environ.get(key):
            cfg.server_url = os.environ[key]
            break
    for key in (
        "TIMEFORGE_API_TOKEN",
        "ESSTRACKER_API_TOKEN",
        "TIMETRACK_API_TOKEN",
    ):
        if os.environ.get(key):
            cfg.api_token = os.environ[key]
            break


__all__ = [
    "AgentConfig",
    "DEFAULT_BUFFER_PATH",
    "DEFAULT_SERVER_URL",
    "LOCK_PATH",
    "USER_CONFIG_PATH",
    "clear_saved_token",
    "ensure_data_dirs",
    "load_agent_config",
    "save_agent_config",
    "user_config_path",
]
