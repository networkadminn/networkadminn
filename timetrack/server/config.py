"""Server configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from ..security import SecurityHeaders

# Chart.js is loaded from jsDelivr on the dashboards; allow it in the CSP.
_CHARTJS_CDN = "https://cdn.jsdelivr.net"


def _default_data_dir() -> str:
    return os.environ.get(
        "TIMETRACK_SERVER_DATA",
        os.path.join(os.path.expanduser("~"), ".local", "share", "timetrack-server"),
    )


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_list(name: str) -> list[str]:
    raw = os.environ.get(name, "")
    return [part.strip() for part in raw.split(",") if part.strip()]


def _default_security() -> SecurityHeaders:
    return SecurityHeaders(
        enabled=_env_bool("TIMETRACK_SECURITY_HEADERS", True),
        hsts=_env_bool("TIMETRACK_HSTS", True),
        hsts_force=_env_bool("TIMETRACK_HSTS_FORCE", False),
        csp_script_src=(_CHARTJS_CDN,),
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
    # Consider a user "online" if seen within this many seconds.
    online_window: float = 300.0

    # --- Security ---
    security: SecurityHeaders = field(default_factory=_default_security)
    # Optional IP / CIDR allow-list guarding the admin dashboard. Empty = allow
    # all. Also configurable via TIMETRACK_ADMIN_IP_ALLOWLIST (comma separated).
    admin_ip_allowlist: list[str] = field(
        default_factory=lambda: _env_list("TIMETRACK_ADMIN_IP_ALLOWLIST")
    )
    # Trust X-Forwarded-For for client-IP checks (only enable behind a proxy you
    # control, otherwise clients can spoof their address).
    trust_proxy: bool = field(
        default_factory=lambda: _env_bool("TIMETRACK_TRUST_PROXY", False)
    )

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


__all__ = ["ServerConfig"]
