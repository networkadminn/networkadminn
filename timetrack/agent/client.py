"""Minimal HTTP client for talking to the TimeTrack server (stdlib only)."""

from __future__ import annotations

import json
import mimetypes
import ssl
import sys
import urllib.error
import urllib.request
import uuid

from .. import __version__

_ssl_context: ssl.SSLContext | None = None


def ssl_context() -> ssl.SSLContext:
    """System trust store plus the certifi bundle.

    Packaged macOS/Linux builds carry an OpenSSL whose default CA path points at
    the build machine, so without certifi every HTTPS call can fail with
    CERTIFICATE_VERIFY_FAILED. The system store is kept so corporate CAs still work.
    """
    global _ssl_context
    if _ssl_context is None:
        ctx = ssl.create_default_context()
        try:
            import certifi  # type: ignore

            ctx.load_verify_locations(cafile=certifi.where())
        except Exception:
            pass
        _ssl_context = ctx
    return _ssl_context


def urlopen(req, timeout: float):
    """``urllib.request.urlopen`` with :func:`ssl_context` for HTTPS."""
    return urllib.request.urlopen(req, timeout=timeout, context=ssl_context())


def agent_platform() -> str:
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "mac"
    return "linux"


def device_identity() -> tuple[str, str]:
    """(stable id, readable name) for this machine, sent on sign-in."""
    import getpass
    import hashlib
    import platform

    name = platform.node() or "unknown"
    try:
        os_user = getpass.getuser()
    except Exception:
        os_user = ""
    raw = f"{name}|{os_user}|{agent_platform()}|{uuid.getnode()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32], name


class ServerClient:
    def __init__(self, base_url: str, token: str, timeout: float = 15.0):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.last_status: int | None = None
        self.last_post_status: int | None = None

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def _headers(self, extra: dict | None = None) -> dict:
        headers = {
            "Authorization": f"Bearer {self.token}",
            # Cloudflare / reverse proxies often block the default Python-urllib UA.
            "User-Agent": f"esstracker-Agent/{__version__} (+https://tracker.euclideesolutions.com)",
            "Accept": "application/json, */*",
            "X-Agent-Version": __version__,
            "X-Agent-Platform": agent_platform(),
        }
        if extra:
            headers.update(extra)
        return headers

    def ping(self) -> dict | None:
        req = urllib.request.Request(
            self._url("/api/v1/ping"), headers=self._headers(), method="GET"
        )
        try:
            with urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                self.last_status = resp.status
                return data
        except urllib.error.HTTPError as exc:
            self.last_status = exc.code
            return None
        except (urllib.error.URLError, OSError, ValueError):
            self.last_status = None
            return None

    @property
    def unauthorized(self) -> bool:
        """Last ping was refused by the server (signed out / 30-day session over)."""
        return self.last_status == 401

    def post_activities(self, activities: list[dict]) -> bool:
        body = json.dumps({"activities": activities}).encode("utf-8")
        req = urllib.request.Request(
            self._url("/api/v1/activities"),
            data=body,
            headers=self._headers({"Content-Type": "application/json"}),
            method="POST",
        )
        return self._post(req, self.timeout)

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
        # Screenshots are larger than activity batches; allow slow uplinks.
        return self._post(req, max(self.timeout, 60.0))

    def _post(self, req: urllib.request.Request, timeout: float) -> bool:
        """POST and record ``last_post_status`` (None = network error, no reply)."""
        try:
            with urlopen(req, timeout=timeout) as resp:
                self.last_post_status = resp.status
                return 200 <= resp.status < 300
        except urllib.error.HTTPError as exc:
            self.last_post_status = exc.code
            if exc.code == 401:
                self.last_status = 401
            return False
        except (urllib.error.URLError, OSError):
            self.last_post_status = None
            return False

    @property
    def last_post_rejected(self) -> bool:
        """The server read the last upload and refused it for good (bad/oversized data).

        401 (sign-in), 409 (Private Time), 408/429 and 5xx are temporary: keep the data.
        """
        code = self.last_post_status
        return code is not None and 400 <= code < 500 and code not in (401, 403, 408, 409, 429)

    def notify_stop(self, reason: str = "quit", timeout: float = 5.0) -> bool:
        """Tell the server the tracker is stopping so dashboards go offline at once."""
        body = json.dumps({"reason": reason}).encode("utf-8")
        req = urllib.request.Request(
            self._url("/api/v1/agent/stop"),
            data=body,
            headers=self._headers({"Content-Type": "application/json"}),
            method="POST",
        )
        try:
            with urlopen(req, timeout=timeout) as resp:
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
            with urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError):
            return None

    def get_private(self) -> dict | None:
        req = urllib.request.Request(
            self._url("/api/v1/private"), headers=self._headers(), method="GET"
        )
        try:
            with urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError):
            return None

    def fetch_tray_day(self) -> dict | None:
        req = urllib.request.Request(
            self._url("/api/v1/tray-day"), headers=self._headers(), method="GET"
        )
        try:
            with urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                self.last_status = resp.status
                return data if data.get("ok") else None
        except urllib.error.HTTPError as exc:
            self.last_status = exc.code
            return None
        except (urllib.error.URLError, OSError, ValueError):
            self.last_status = None
            return None

    def login(self, username: str, password: str) -> dict | None:
        """Exchange username/password for an API token (first-run desktop login)."""
        device_id, device_name = device_identity()
        body = json.dumps(
            {
                "username": username,
                "password": password,
                "device_id": device_id,
                "device_name": device_name,
                "platform": agent_platform(),
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            self._url("/api/v1/agent/login"),
            data=body,
            headers={
                "Content-Type": "application/json",
                "User-Agent": f"esstracker-Agent/{__version__} (+https://tracker.euclideesolutions.com)",
                "Accept": "application/json, */*",
                "X-Agent-Version": __version__,
                "X-Agent-Platform": agent_platform(),
            },
            method="POST",
        )
        try:
            with urlopen(req, timeout=self.timeout) as resp:
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


__all__ = ["ServerClient", "agent_platform"]
