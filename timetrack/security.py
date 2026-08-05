"""Shared web security controls for TimeTrack Flask apps."""

from __future__ import annotations

from flask import Flask, Response

HSTS_HEADER = "max-age=31536000; includeSubDomains"
REFERRER_POLICY = "strict-origin-when-cross-origin"
X_FRAME_OPTIONS = "SAMEORIGIN"
X_CONTENT_TYPE_OPTIONS = "nosniff"


def init_security_headers(app: Flask, *, content_security_policy: str) -> None:
    """Attach common security response headers to every response."""

    @app.after_request
    def apply_security_headers(response: Response) -> Response:
        response.headers["Strict-Transport-Security"] = HSTS_HEADER
        response.headers["Content-Security-Policy"] = content_security_policy
        response.headers["X-Frame-Options"] = X_FRAME_OPTIONS
        response.headers["X-Content-Type-Options"] = X_CONTENT_TYPE_OPTIONS
        response.headers["Referrer-Policy"] = REFERRER_POLICY
        return response

