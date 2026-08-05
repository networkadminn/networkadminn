"""Shared web-security helpers.

Implements application-level hardening used by both the team-mode server and
the local dashboard:

- security response headers (HSTS, CSP, X-Frame-Options,
  X-Content-Type-Options, Referrer-Policy),
- a password strength policy for dashboard accounts,
- open-redirect protection for ``?next=`` style parameters,
- optional IP allowlisting for admin endpoints.
"""

from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlparse

from flask import Flask, request

#: Sent on HTTPS responses only (browsers ignore HSTS over plain HTTP, and
#: sending it there could lock users out of HTTP-only dev setups anyway).
HSTS_VALUE = "max-age=31536000; includeSubDomains"

#: CSP for the team-mode server. The dashboards use small inline scripts and
#: Chart.js from jsdelivr, so those two sources are allowed explicitly;
#: everything else is restricted to same-origin.
SERVER_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "font-src 'self'; "
    "object-src 'none'; "
    "frame-ancestors 'self'; "
    "base-uri 'self'; "
    "form-action 'self'"
)

#: Stricter CSP for the local dashboard, which loads no external scripts.
DASHBOARD_CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "object-src 'none'; "
    "frame-ancestors 'self'; "
    "base-uri 'self'; "
    "form-action 'self'"
)

MIN_PASSWORD_LENGTH = 12


def _request_is_https() -> bool:
    if request.is_secure:
        return True
    # Behind a reverse proxy (nginx/Apache) TLS terminates upstream.
    return request.headers.get("X-Forwarded-Proto", "").lower() == "https"


def apply_security_headers(
    app: Flask, *, csp: str = SERVER_CSP, hsts: bool = True
) -> Flask:
    """Attach an ``after_request`` hook that sets security headers.

    ``setdefault`` is used throughout so individual views can still override
    a header when they need to.
    """

    @app.after_request
    def _set_security_headers(response):
        headers = response.headers
        headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        headers.setdefault("X-Content-Type-Options", "nosniff")
        headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        if csp:
            headers.setdefault("Content-Security-Policy", csp)
        if hsts and _request_is_https():
            headers.setdefault("Strict-Transport-Security", HSTS_VALUE)
        return response

    return app


def password_policy_error(password: str) -> str | None:
    """Return a human-readable problem with ``password``, or ``None`` if OK."""
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"must be at least {MIN_PASSWORD_LENGTH} characters long"
    if not re.search(r"[a-z]", password):
        return "must contain a lowercase letter"
    if not re.search(r"[A-Z]", password):
        return "must contain an uppercase letter"
    if not re.search(r"\d", password):
        return "must contain a digit"
    return None


def is_safe_redirect_target(target: str | None) -> bool:
    """Only allow same-site, path-relative redirect targets.

    Rejects absolute URLs (``https://evil``), protocol-relative URLs
    (``//evil``) and backslash tricks (``/\\evil``) some browsers normalize
    into protocol-relative URLs.
    """
    if not target:
        return False
    parsed = urlparse(target)
    if parsed.scheme or parsed.netloc:
        return False
    return target.startswith("/") and not target.startswith(("//", "/\\"))


def parse_ip_allowlist(raw: str | None) -> list[ipaddress._BaseNetwork]:
    """Parse a comma/space-separated list of IPs or CIDR networks.

    Invalid entries raise ``ValueError`` so misconfiguration fails loudly at
    startup instead of silently allowing everyone.
    """
    networks: list[ipaddress._BaseNetwork] = []
    for part in re.split(r"[,\s]+", raw or ""):
        if part:
            networks.append(ipaddress.ip_network(part, strict=False))
    return networks


def ip_allowed(
    remote_addr: str | None, allowlist: list[ipaddress._BaseNetwork] | None
) -> bool:
    """True if ``remote_addr`` may pass. An empty allowlist allows everyone."""
    if not allowlist:
        return True
    if not remote_addr:
        return False
    try:
        addr = ipaddress.ip_address(remote_addr)
    except ValueError:
        return False
    return any(addr in network for network in allowlist)


__all__ = [
    "HSTS_VALUE",
    "SERVER_CSP",
    "DASHBOARD_CSP",
    "MIN_PASSWORD_LENGTH",
    "apply_security_headers",
    "password_policy_error",
    "is_safe_redirect_target",
    "parse_ip_allowlist",
    "ip_allowed",
]
