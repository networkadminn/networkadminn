"""Server configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from ..security_headers import DEFAULT_CONTENT_SECURITY_POLICY
from .security import PASSWORD_MIN_LENGTH


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
    # Consider a user "online" if seen within this many seconds.
    online_window: float = 300.0

    # --- Security hardening ---
    # HSTS / CSP / X-Frame-Options / X-Content-Type-Options / Referrer-Policy.
    # Applied at the app level so they work regardless of hosting (no
    # server-level / .htaccess access required). See timetrack.security_headers.
    enable_security_headers: bool = True
    content_security_policy: str = DEFAULT_CONTENT_SECURITY_POLICY
    hsts_max_age: int = 63072000  # 2 years

    # Minimum password strength enforced whenever a password is (re)set.
    password_min_length: int = PASSWORD_MIN_LENGTH

    # Optional IP allow-list (single IPs or CIDR ranges) for the admin
    # dashboard, e.g. ["10.0.0.0/8", "203.0.113.5"]. Empty = unrestricted.
    admin_ip_allowlist: list[str] = field(default_factory=list)

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
