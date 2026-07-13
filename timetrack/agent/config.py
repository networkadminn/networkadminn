"""Agent configuration (server URL, token, intervals, categorization rules)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from ..config import Config as _CoreConfig
from ..config import _merge_rules  # reuse default categorization rules

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None  # type: ignore[assignment]

DEFAULT_BUFFER_PATH = os.path.join(
    os.path.expanduser("~"), ".local", "share", "timetrack", "agent-buffer.db"
)
DEFAULT_SHOTS_DIR = os.path.join(
    os.path.expanduser("~"), ".local", "share", "timetrack", "agent-shots"
)


@dataclass
class AgentConfig:
    server_url: str = "http://127.0.0.1:8080"
    api_token: str = ""
    poll_interval: float = 5.0
    idle_threshold: float = 180.0
    screenshots_enabled: bool = True
    screenshot_interval: float = 300.0
    screenshot_max_width: int = 1280
    flush_interval: float = 30.0
    batch_size: int = 200
    buffer_path: str = DEFAULT_BUFFER_PATH
    shots_dir: str = DEFAULT_SHOTS_DIR
    rules: dict[str, list[str]] = field(default_factory=lambda: _merge_rules({}))

    def categorize(self, app: str, title: str = "") -> str:
        # Delegate to the core categorizer for identical behavior.
        core = _CoreConfig(rules=self.rules)
        return core.categorize(app, title)


def _candidate_paths() -> list[Path]:
    return [
        Path.cwd() / "agent.toml",
        Path.cwd() / "config.toml",
        Path(os.path.expanduser("~")) / ".config" / "timetrack" / "agent.toml",
    ]


def load_agent_config(path: str | os.PathLike[str] | None = None) -> AgentConfig:
    cfg = AgentConfig()

    candidate: Path | None = None
    if path is not None:
        candidate = Path(path)
    else:
        for p in _candidate_paths():
            if p.is_file():
                candidate = p
                break

    if candidate is None or not candidate.is_file() or tomllib is None:
        _apply_env(cfg)
        return cfg

    with open(candidate, "rb") as fh:
        data = tomllib.load(fh)

    agent = data.get("agent", data)  # allow [agent] table or top-level keys
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
    cfg.buffer_path = str(agent.get("buffer_path", cfg.buffer_path))
    cfg.shots_dir = str(agent.get("shots_dir", cfg.shots_dir))
    cfg.rules = _merge_rules(data.get("rules", {}) or {})

    _apply_env(cfg)
    return cfg


def _apply_env(cfg: AgentConfig) -> None:
    """Environment variables override file/defaults (handy for deployment)."""
    if os.environ.get("TIMETRACK_SERVER_URL"):
        cfg.server_url = os.environ["TIMETRACK_SERVER_URL"]
    if os.environ.get("TIMETRACK_API_TOKEN"):
        cfg.api_token = os.environ["TIMETRACK_API_TOKEN"]


__all__ = ["AgentConfig", "load_agent_config", "DEFAULT_BUFFER_PATH"]
