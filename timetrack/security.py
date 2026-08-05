"""Shared HTTP security-header support for the TimeTrack web apps.

This module centralises the browser-facing hardening that can be applied at the
*application* level, independent of the web server / hosting platform:

- **HSTS** (``Strict-Transport-Security``) — force HTTPS on subsequent visits.
- **CSP** (``Content-Security-Policy``) — restrict where scripts/styles/images
  may load from. A per-request *nonce* is generated so inline ``<script>``
  blocks can be allow-listed without resorting to ``'unsafe-inline'``.
- ``X-Frame-Options`` — clickjacking protection.
- ``X-Content-Type-Options: nosniff`` — stop MIME sniffing.
- ``Referrer-Policy`` — limit referrer leakage.
- ``Permissions-Policy`` — disable unused browser features by default.
- ``Cross-Origin-Opener-Policy`` / ``Cross-Origin-Resource-Policy``.

These are the same headers an operator would otherwise add per-site via Apache
/ ``.htaccess`` (see ``deploy/apache-security.htaccess``); applying them in the
app means they work regardless of the front-end web server.
"""

from __future__ import annotations

import ipaddress
import secrets
from dataclasses import dataclass


def client_ip(request, *, trust_proxy: bool = False) -> str:
    """Return the client IP, honouring ``X-Forwarded-For`` only when trusted."""
    if trust_proxy:
        forwarded = request.headers.get("X-Forwarded-For", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.remote_addr or ""


def ip_in_allowlist(ip: str, allowlist: list[str] | tuple[str, ...]) -> bool:
    """Check ``ip`` against a list of literal IPs and/or CIDR networks.

    An empty allow-list means "allow everything".
    """
    if not allowlist:
        return True
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    for raw in allowlist:
        entry = (raw or "").strip()
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


def generate_nonce() -> str:
    """Return a fresh, URL-safe CSP nonce."""
    return secrets.token_urlsafe(16)


def request_is_secure(request) -> bool:
    """Best-effort HTTPS detection, honouring a terminating reverse proxy."""
    if getattr(request, "is_secure", False):
        return True
    forwarded = request.headers.get("X-Forwarded-Proto", "")
    return forwarded.split(",")[0].strip().lower() == "https"


@dataclass
class SecurityHeaders:
    """Configuration for the response security headers.

    All fields have safe defaults. Set ``enabled=False`` to turn the whole
    feature off, or tweak individual directives. ``csp`` may contain a
    ``{nonce}`` placeholder which is substituted per request; leave it empty to
    use the built-in policy.
    """

    enabled: bool = True

    # --- HSTS ---
    hsts: bool = True
    hsts_max_age: int = 31_536_000  # 1 year
    hsts_include_subdomains: bool = True
    hsts_preload: bool = False
    # Send HSTS even when the request is not detected as HTTPS. Useful when the
    # app always sits behind TLS but the proxy headers are not forwarded.
    hsts_force: bool = False

    # --- CSP ---
    csp: str = ""  # empty -> default_csp(); may contain "{nonce}"
    # Extra sources appended to ``script-src`` in the default policy (e.g. a CDN).
    csp_script_src: tuple[str, ...] = ()
    # Extra sources appended to ``style-src`` in the default policy.
    csp_style_src: tuple[str, ...] = ()

    # --- Misc headers ---
    frame_options: str = "SAMEORIGIN"
    content_type_options: str = "nosniff"
    referrer_policy: str = "strict-origin-when-cross-origin"
    permissions_policy: str = "geolocation=(), microphone=(), camera=()"
    cross_origin_opener_policy: str = "same-origin"
    cross_origin_resource_policy: str = "same-origin"

    def default_csp(self, nonce: str) -> str:
        script_src = ["'self'", f"'nonce-{nonce}'", *self.csp_script_src]
        # Inline ``style="..."`` attributes are used for progress bars / rings;
        # keep 'unsafe-inline' for styles only (attributes can't carry a nonce).
        style_src = ["'self'", "'unsafe-inline'", *self.csp_style_src]
        directives = {
            "default-src": "'self'",
            "base-uri": "'self'",
            "frame-ancestors": "'self'",
            "form-action": "'self'",
            "object-src": "'none'",
            "img-src": "'self' data:",
            "font-src": "'self'",
            "connect-src": "'self'",
            "style-src": " ".join(style_src),
            "script-src": " ".join(script_src),
        }
        return "; ".join(f"{name} {value}" for name, value in directives.items())

    def resolved_csp(self, nonce: str) -> str:
        if self.csp:
            return self.csp.replace("{nonce}", nonce)
        return self.default_csp(nonce)

    def build(self, *, nonce: str, is_secure: bool) -> dict[str, str]:
        """Return the header name/value pairs to apply to a response."""
        headers: dict[str, str] = {}
        headers["Content-Security-Policy"] = self.resolved_csp(nonce)
        if self.frame_options:
            headers["X-Frame-Options"] = self.frame_options
        if self.content_type_options:
            headers["X-Content-Type-Options"] = self.content_type_options
        if self.referrer_policy:
            headers["Referrer-Policy"] = self.referrer_policy
        if self.permissions_policy:
            headers["Permissions-Policy"] = self.permissions_policy
        if self.cross_origin_opener_policy:
            headers["Cross-Origin-Opener-Policy"] = self.cross_origin_opener_policy
        if self.cross_origin_resource_policy:
            headers["Cross-Origin-Resource-Policy"] = self.cross_origin_resource_policy
        if self.hsts and (is_secure or self.hsts_force):
            value = f"max-age={self.hsts_max_age}"
            if self.hsts_include_subdomains:
                value += "; includeSubDomains"
            if self.hsts_preload:
                value += "; preload"
            headers["Strict-Transport-Security"] = value
        return headers


def install_security(app, headers: SecurityHeaders | None = None) -> SecurityHeaders:
    """Wire ``SecurityHeaders`` into a Flask ``app``.

    Registers a per-request CSP nonce, an ``after_request`` hook that stamps the
    headers on every response, and a ``csp_nonce()`` template helper. Returns
    the (possibly defaulted) ``SecurityHeaders`` in effect.
    """
    from flask import g, request

    headers = headers or SecurityHeaders()

    @app.context_processor
    def _inject_nonce() -> dict:
        return {"csp_nonce": lambda: getattr(g, "csp_nonce", "")}

    if not headers.enabled:
        return headers

    @app.before_request
    def _assign_nonce() -> None:
        g.csp_nonce = generate_nonce()

    @app.after_request
    def _apply_headers(response):
        nonce = getattr(g, "csp_nonce", "")
        built = headers.build(nonce=nonce, is_secure=request_is_secure(request))
        for name, value in built.items():
            response.headers[name] = value
        return response

    return headers


__all__ = [
    "SecurityHeaders",
    "install_security",
    "generate_nonce",
    "request_is_secure",
    "client_ip",
    "ip_in_allowlist",
]
