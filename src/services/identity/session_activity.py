from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from fastapi import Request

BACKGROUND_REQUEST_HEADER = "X-DeltaLLM-Background"
# A session's last_seen_at is written at most once per this many seconds, so a busy
# dashboard costs one write per minute instead of one per request. The idle check
# therefore has up to this much slack.
LAST_SEEN_WRITE_INTERVAL_SECONDS = 60


def renews_session(request: Request) -> bool:
    """Background requests (auto-refresh, session checks) never count as activity."""
    return request.headers.get(BACKGROUND_REQUEST_HEADER, "").strip() != "1"


def _general_settings(request: Request) -> Any:
    return getattr(getattr(request.app.state, "app_config", None), "general_settings", None)


def session_ttl_seconds(request: Request) -> int:
    hours = getattr(_general_settings(request), "auth_session_ttl_hours", 12)
    return int(hours * 3600)


def master_session_ttl_seconds(request: Request) -> int:
    hours = getattr(_general_settings(request), "auth_master_session_ttl_hours", None)
    return int(hours * 3600) if hours else session_ttl_seconds(request)


def idle_timeout_seconds(request: Request) -> int:
    minutes = getattr(_general_settings(request), "auth_session_idle_timeout_minutes", 0)
    return max(0, int(minutes or 0)) * 60


def nonnegative_seconds(value: object) -> float | None:
    """Parse a duration the database computed; None when it is missing or unreadable."""
    if value is None:
        return None
    try:
        return max(0.0, float(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def idle_expiry(
    *,
    now: datetime,
    idle_timeout_seconds: int,
    idle_seconds: float | None,
    renewed: bool,
    expires_at: datetime | None,
) -> datetime | None:
    """When an idle session ends, never later than its hard cap; None when idle is off."""
    if not idle_timeout_seconds:
        return None
    unused = 0.0 if renewed or idle_seconds is None else idle_seconds
    idle_expires_at = now + timedelta(seconds=idle_timeout_seconds - unused)
    return idle_expires_at if expires_at is None else min(idle_expires_at, expires_at)
