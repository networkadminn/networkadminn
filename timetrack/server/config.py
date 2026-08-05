"""Server configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None  # type: ignore[assignment]


def _default_data_dir() -> str:
    return os.environ.get(
        "TIMETRACK_SERVER_DATA",
        os.path.join(os.path.expanduser("~"), ".local", "share", "timetrack-server"),
    )


@dataclass
class ServerConfig:
    data_dir: str = field(default_factory=_default_data_dir)
    secret_key: str = field(
        default_factory=lambda: os.environ.get("TIMETRACK_SECRET_KEY", "")
    )
    database_uri: str = ""
    host: str = "0.0.0.0"
    port: int = 8080
    # Consider a user as online if seen within this many seconds.
    online_window: float = 300.0

    # --- HTTP security ---
    hsts_max_age: int = 0
    force_https: bool = False
    trust_proxy_headers: bool = False
    session_cookie_secure: bool = False
    min_password_length: int = 12
    admin_ip_allowlist: list[str] = field(default_factory=list)
    content_security_policy: str = ""
    referrer_policy: str = "strict-origin-when-cross-origin"

    def finalize(self) -> "ServerConfig":
        os.makedirs(self.data_dir, exist_ok=True)
        os.makedirs(self.screenshots_dir, exist_ok=True)
        if not self.secret_key:
            self.secret_key = self._persistent_secret()
        if not self.database_uri:
            db_path = os.path.join(self.data_dir, "timetrack-server.db")
            self.database_uri = f"sqlite:///{db_path}"
        return self

    @property
    def screenshots_dir(self) -> str:
        return os.path.join(self.data_dir, "screenshots")

    def _persistent_secret(self) -> str:
        path = os.path.join(self.data_dir, "secret_key")
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as fh:
                return fh.read().strip()
        import secrets

        key = secrets.token_hex(32)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(key)
        os.chmod(path, 0o600)
        return key


def default_server_config_paths() -> list[Path]:
    return [
        Path.cwd() / "server.toml",
        Path(os.path.expanduser("~")) / ".config" / "timetrack" / "server.toml",
    ]


def load_server_config(path: str | os.PathLike[str] | None = None) -> ServerConfig:
    """Load server configuration from TOML, falling back to defaults."""
    cfg = ServerConfig()

    candidate: Path | None = None
    if path is not None:
        candidate = Path(path)
    else:
        for p in default_server_config_paths():
            if p.is_file():
                candidate = p
                break

    if candidate is None or not candidate.is_file() or tomllib is None:
        return cfg.finalize()

    with open(candidate, "rb") as fh:
        data = tomllib.load(fh)

    cfg.data_dir = str(data.get("data_dir", cfg.data_dir))
    cfg.host = str(data.get("host", cfg.host))
    cfg.port = int(data.get("port", cfg.port))
    cfg.online_window = float(data.get("online_window", cfg.online_window))
    cfg.hsts_max_age = int(data.get("hsts_max_age", cfg.hsts_max_age))
    cfg.force_https = bool(data.get("force_https", cfg.force_https))
    cfg.trust_proxy_headers = bool(
        data.get("trust_proxy_headers", cfg.trust_proxy_headers)
    )
    cfg.session_cookie_secure = bool(
        data.get("session_cookie_secure", cfg.session_cookie_secure)
    )
    cfg.min_password_length = int(
        data.get("min_password_length", cfg.min_password_length)
    )
    allowlist = data.get("admin_ip_allowlist")
    if isinstance(allowlist, list):
        cfg.admin_ip_allowlist = [str(x) for x in allowlist]
    cfg.content_security_policy = str(
        data.get("content_security_policy", cfg.content_security_policy)
    )
    cfg.referrer_policy = str(data.get("referrer_policy", cfg.referrer_policy))
    return cfg.finalize()


__all__ = ["ServerConfig", "default_server_config_paths", "load_server_config"]
