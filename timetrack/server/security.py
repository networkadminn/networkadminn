"""HTTP security headers, admin IP allowlisting, and password policy."""

from __future__ import annotations

import ipaddress
from functools import wraps
from urllib.parse import urlparse

from flask import abort, current_app, redirect, request, url_for

from .config import ServerConfig

_DEFAULT_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; "
    "connect-src 'self'"
)


def validate_password(password: str, *, min_length: int) -> str | None:
    """Return an error message when *password* fails policy, else ``None``."""
    if len(password) < min_length:
        return f"Password must be at least {min_length} characters."
    return None


def is_safe_redirect(target: str | None) -> bool:
    """Only allow same-host relative redirects after login."""
    if not target:
        return False
    ref = urlparse(target)
    return not ref.netloc and ref.scheme == "" and target.startswith("/")


def _client_ip() -> str:
    """Best-effort client IP (honours X-Forwarded-For when present)."""
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or ""


def _ip_allowed(client: str, allowlist: list[str]) -> bool:
    if not allowlist:
        return True
    try:
        addr = ipaddress.ip_address(client)
    except ValueError:
        return False
    for entry in allowlist:
        entry = entry.strip()
        if not entry:
            continue
        try:
            if "/" in entry:
                if addr in ipaddress.ip_network(entry, strict=False):
                    return True
            elif addr == ipaddress.ip_address(entry):
                return True
        except ValueError:
            continue
    return False


def admin_ip_required(view):
    """Restrict a view to clients on the configured admin IP allowlist."""

    @wraps(view)
    def wrapped(*args, **kwargs):
        cfg: ServerConfig = current_app.config["TIMETRACK_SERVER_CONFIG"]
        if cfg.admin_ip_allowlist and not _ip_allowed(
            _client_ip(), cfg.admin_ip_allowlist
        ):
            abort(403)
        return view(*args, **kwargs)

    return wrapped


def _request_is_secure(cfg: ServerConfig) -> bool:
    if request.is_secure:
        return True
    if cfg.trust_proxy_headers:
        proto = request.headers.get("X-Forwarded-Proto", "").lower()
        return proto == "https"
    return False


def init_security(app) -> None:
    """Register global security hooks (headers, HTTPS redirect, admin IP gate)."""

    @app.before_request
    def _enforce_https():
        cfg: ServerConfig = app.config["TIMETRACK_SERVER_CONFIG"]
        if not cfg.force_https or _request_is_secure(cfg):
            return None
        if request.endpoint == "static":
            return None
        url = request.url.replace("http://", "https://", 1)
        return redirect(url, code=301)

    @app.before_request
    def _gate_admin_by_ip():
        cfg: ServerConfig = app.config["TIMETRACK_SERVER_CONFIG"]
        if not cfg.admin_ip_allowlist:
            return None
        path = request.path or ""
        if not (
            path.startswith("/admin")
            or path == "/login"
            or path.startswith("/api/")
        ):
            return None
        if path.startswith("/api/"):
            return None  # agents use bearer tokens, not admin-panel IP rules
        if _ip_allowed(_client_ip(), cfg.admin_ip_allowlist):
            return None
        abort(403)

    @app.after_request
    def _security_headers(response):
        cfg: ServerConfig = app.config["TIMETRACK_SERVER_CONFIG"]

        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault(
            "Referrer-Policy", cfg.referrer_policy
        )
        csp = cfg.content_security_policy or _DEFAULT_CSP
        response.headers.setdefault("Content-Security-Policy", csp)
        response.headers.setdefault("X-XSS-Protection", "0")
        response.headers.setdefault(
            "Permissions-Policy", "camera=(), microphone=(), geolocation=()"
        )

        if cfg.hsts_max_age > 0 and _request_is_secure(cfg):
            response.headers.setdefault(
                "Strict-Transport-Security",
                f"max-age={cfg.hsts_max_age}; includeSubDomains",
            )
        return response


def login_success_redirect():
    """Safe post-login redirect target."""
    nxt = request.args.get("next")
    if is_safe_redirect(nxt):
        return nxt
    return url_for("views.home")


__all__ = [
    "admin_ip_required",
    "init_security",
    "is_safe_redirect",
    "login_success_redirect",
    "validate_password",
]
