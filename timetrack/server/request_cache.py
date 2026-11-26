"""Short-lived in-process caches for hot read paths (no external infra)."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import TypeVar

from .extensions import db
from .models import OfflineRequest

T = TypeVar("T")

_lock = threading.Lock()
_pending_by_org: dict[int, tuple[float, int]] = {}
_PENDING_TTL_SEC = 45.0
_generic_cache: dict[str, tuple[float, object]] = {}


def cached_pending_offline_count(org_id: int) -> int:
    """Count pending offline approvals for an org; cached ~45s per worker."""
    now = time.time()
    with _lock:
        entry = _pending_by_org.get(org_id)
        if entry is not None and entry[0] > now:
            return entry[1]

    count = (
        db.session.execute(
            db.select(db.func.count(OfflineRequest.id)).filter_by(status="pending")
        ).scalar_one()
        or 0
    )
    value = int(count)
    with _lock:
        _pending_by_org[org_id] = (now + _PENDING_TTL_SEC, value)
    return value


def invalidate_pending_offline_cache(org_id: int | None = None) -> None:
    """Clear pending count cache after approve/deny (optional org scope)."""
    with _lock:
        if org_id is None:
            _pending_by_org.clear()
        else:
            _pending_by_org.pop(org_id, None)


def cached_value(key: str, ttl_sec: float, factory: Callable[[], T]) -> T:
    """Thread-safe TTL cache for expensive read-only computations."""
    now = time.time()
    with _lock:
        entry = _generic_cache.get(key)
        if entry is not None and entry[0] > now:
            return entry[1]  # type: ignore[return-value]

    value = factory()
    with _lock:
        _generic_cache[key] = (now + ttl_sec, value)
    return value


def team_trend_cache_key(
    org_id: int, day_s: str, team_id: int | None, user_ids: list[int]
) -> str:
    ids = ",".join(str(i) for i in sorted(user_ids))
    return f"team_trend:{org_id}:{day_s}:{team_id or 0}:{ids}"


__all__ = [
    "cached_pending_offline_count",
    "cached_value",
    "invalidate_pending_offline_cache",
    "team_trend_cache_key",
]
