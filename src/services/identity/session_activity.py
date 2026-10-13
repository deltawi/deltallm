from __future__ import annotations

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
