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
        headers = {
            "Authorization": f"Bearer {self.token}",
            # Cloudflare / reverse proxies often block the default Python-urllib UA.
            "User-Agent": "TimeTrack-Agent/0.1 (+https://tracker.euclideesolutions.com)",
            "Accept": "application/json, */*",
        }
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

    def set_private(self, active: bool) -> dict | None:
        body = json.dumps({"active": active}).encode("utf-8")
        req = urllib.request.Request(
            self._url("/api/v1/private"),
            data=body,
            headers=self._headers({"Content-Type": "application/json"}),
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError):
            return None

    def get_private(self) -> dict | None:
        req = urllib.request.Request(
            self._url("/api/v1/private"), headers=self._headers(), method="GET"
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError):
            return None

    def login(self, username: str, password: str) -> dict | None:
        """Exchange username/password for an API token (first-run desktop login)."""
        body = json.dumps({"username": username, "password": password}).encode("utf-8")
        req = urllib.request.Request(
            self._url("/api/v1/agent/login"),
            data=body,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "esstracker-Agent/0.1 (+https://tracker.euclideesolutions.com)",
                "Accept": "application/json, */*",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            if data.get("api_token"):
                self.token = str(data["api_token"])
            return data
        except urllib.error.HTTPError as exc:
            try:
                err = json.loads(exc.read().decode("utf-8"))
            except Exception:
                err = {"error": f"HTTP {exc.code}"}
            return {"ok": False, "error": err.get("error") or f"HTTP {exc.code}"}
        except (urllib.error.URLError, OSError, ValueError) as exc:
            return {"ok": False, "error": str(exc) or "network error"}


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
