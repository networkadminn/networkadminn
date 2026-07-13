"""Minimal HTTP client for talking to the TimeTrack server (stdlib only)."""

from __future__ import annotations

import json
import mimetypes
import urllib.error
import urllib.request
import uuid


class ServerClient:
    def __init__(self, base_url: str, token: str, timeout: float = 15.0):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def _headers(self, extra: dict | None = None) -> dict:
        headers = {"Authorization": f"Bearer {self.token}"}
        if extra:
            headers.update(extra)
        return headers

    def ping(self) -> dict | None:
        req = urllib.request.Request(
            self._url("/api/v1/ping"), headers=self._headers(), method="GET"
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError):
            return None

    def post_activities(self, activities: list[dict]) -> bool:
        body = json.dumps({"activities": activities}).encode("utf-8")
        req = urllib.request.Request(
            self._url("/api/v1/activities"),
            data=body,
            headers=self._headers({"Content-Type": "application/json"}),
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return 200 <= resp.status < 300
        except (urllib.error.URLError, OSError):
            return False

    def post_screenshot(self, image_bytes: bytes, meta: dict) -> bool:
        boundary = uuid.uuid4().hex
        body = _encode_multipart(boundary, meta, image_bytes)
        req = urllib.request.Request(
            self._url("/api/v1/screenshots"),
            data=body,
            headers=self._headers(
                {"Content-Type": f"multipart/form-data; boundary={boundary}"}
            ),
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return 200 <= resp.status < 300
        except (urllib.error.URLError, OSError):
            return False


def _encode_multipart(boundary: str, fields: dict, image_bytes: bytes) -> bytes:
    lines: list[bytes] = []
    b = boundary.encode()
    for key, value in fields.items():
        lines.append(b"--" + b)
        lines.append(
            f'Content-Disposition: form-data; name="{key}"'.encode()
        )
        lines.append(b"")
        lines.append(str(value).encode())

    ctype = mimetypes.types_map.get(".jpg", "image/jpeg")
    lines.append(b"--" + b)
    lines.append(
        b'Content-Disposition: form-data; name="image"; filename="shot.jpg"'
    )
    lines.append(f"Content-Type: {ctype}".encode())
    lines.append(b"")
    lines.append(image_bytes)
    lines.append(b"--" + b + b"--")
    lines.append(b"")
    return b"\r\n".join(lines)


__all__ = ["ServerClient"]
