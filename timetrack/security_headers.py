"""Reusable HTTP security headers for TimeTrack's Flask apps.

Sets the response headers commonly recommended for hardening a web app
against clickjacking, MIME-sniffing, referrer leakage, and mixed-content
downgrade attacks:

- ``Strict-Transport-Security`` (HSTS)
- ``Content-Security-Policy`` (CSP)
- ``X-Frame-Options``
- ``X-Content-Type-Options``
- ``Referrer-Policy``

These are applied at the *application* level, so they take effect no
matter how the app is hosted (bare WSGI server, systemd service, behind
an Nginx/Apache reverse proxy, ...) -- useful when you don't have
server/root-level control over the host (e.g. a shared-hosting or
reseller cPanel/WHM account, where only per-site ``.htaccess`` changes
are available).
"""

from __future__ import annotations

from flask import Flask, Response, request

#: Baseline CSP: same-origin by default, with the small set of
#: allowances TimeTrack's own templates need (inline styles for
#: dynamically-computed widths/percentages, inline chart-init scripts,
#: and the Chart.js CDN bundle used by the user dashboard).
DEFAULT_CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "img-src 'self' data:; "
    "style-src 'self' 'unsafe-inline'; "
    "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "connect-src 'self'; "
    "frame-ancestors 'self'; "
    "base-uri 'self'; "
    "form-action 'self'"
)

DEFAULT_FRAME_OPTIONS = "SAMEORIGIN"
DEFAULT_REFERRER_POLICY = "strict-origin-when-cross-origin"
DEFAULT_HSTS_MAX_AGE = 63072000  # 2 years, the value most HSTS-preload guides recommend


def install_security_headers(
    app: Flask,
    *,
    enabled: bool = True,
    content_security_policy: str | None = DEFAULT_CONTENT_SECURITY_POLICY,
    frame_options: str | None = DEFAULT_FRAME_OPTIONS,
    referrer_policy: str | None = DEFAULT_REFERRER_POLICY,
    hsts_max_age: int = DEFAULT_HSTS_MAX_AGE,
    hsts_include_subdomains: bool = True,
) -> None:
    """Register an ``after_request`` hook that adds security headers.

    HSTS is only ever sent on responses served over HTTPS (directly, or
    via a reverse proxy that sets ``X-Forwarded-Proto: https``) --
    advertising it over plain HTTP has no effect for compliant browsers
    and, on a misconfigured proxy, could contribute to users getting
    locked out of a site that isn't actually served over TLS.
    """

    if not enabled:
        return

    @app.after_request
    def _set_security_headers(response: Response) -> Response:
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        if frame_options:
            response.headers.setdefault("X-Frame-Options", frame_options)
        if referrer_policy:
            response.headers.setdefault("Referrer-Policy", referrer_policy)
        if content_security_policy:
            response.headers.setdefault(
                "Content-Security-Policy", content_security_policy
            )

        is_https = (
            request.is_secure
            or request.headers.get("X-Forwarded-Proto", "").lower() == "https"
        )
        if is_https:
            value = f"max-age={hsts_max_age}"
            if hsts_include_subdomains:
                value += "; includeSubDomains"
            response.headers.setdefault("Strict-Transport-Security", value)

        return response


__all__ = [
    "install_security_headers",
    "DEFAULT_CONTENT_SECURITY_POLICY",
    "DEFAULT_FRAME_OPTIONS",
    "DEFAULT_REFERRER_POLICY",
    "DEFAULT_HSTS_MAX_AGE",
]
