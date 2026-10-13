"""Policy for the customer workspace request log view.

The workspace scope comes from the external session binding only. This module
owns the time window rules and the row projection that a customer can see.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
import re

from src.models.workspace_request_logs import WorkspaceRequestLog

DEFAULT_WORKSPACE_LOG_WINDOW = timedelta(hours=24)
_KEY_HASH = re.compile(r"[0-9a-f]{64}")


class InvalidWorkspaceLogWindow(ValueError):
    """The requested start and end do not form a valid time window."""


def is_key_hash(value: str) -> bool:
    return _KEY_HASH.fullmatch(value) is not None


def customer_api_key(value: object) -> str | None:
    """Return the key hash, or None for any other credential label.

    Spend rows also carry labels such as ``jwt:<user or email>`` and
    ``master_key``. Only a stored key hash is safe to show to a customer.
    """

    text = str(value or "")
    return text if is_key_hash(text) else None


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def resolve_workspace_log_window(
    start: datetime | None,
    end: datetime | None,
    *,
    now: datetime,
) -> tuple[datetime, datetime]:
    """Return a UTC window. Start is inclusive and end is exclusive."""

    resolved_end = _utc(end) if end is not None else _utc(now)
    resolved_start = (
        _utc(start) if start is not None else resolved_end - DEFAULT_WORKSPACE_LOG_WINDOW
    )
    if resolved_start >= resolved_end:
        raise InvalidWorkspaceLogWindow("start must be earlier than end")
    return resolved_start, resolved_end


def _int(value: object) -> int:
    return int(value or 0)


def to_workspace_request_log(row: Mapping[str, object]) -> WorkspaceRequestLog:
    latency = row.get("latency_ms")
    http_status = row.get("http_status_code")
    return WorkspaceRequestLog(
        id=str(row["id"]),
        request_id=_optional_text(row.get("request_id")),
        start_time=row["start_time"],
        end_time=row.get("end_time"),
        latency_ms=None if latency is None else _int(latency),
        call_type=_optional_text(row.get("call_type")),
        model=_optional_text(row.get("model")),
        api_key=customer_api_key(row.get("api_key")),
        status=str(row.get("status") or "success"),
        http_status_code=None if http_status is None else _int(http_status),
        error_type=_optional_text(row.get("error_type")),
        prompt_tokens=_int(row.get("prompt_tokens")),
        completion_tokens=_int(row.get("completion_tokens")),
        prompt_tokens_cached=_int(row.get("prompt_tokens_cached")),
        completion_tokens_cached=_int(row.get("completion_tokens_cached")),
        spend=float(row.get("spend") or 0),
        cache_hit=bool(row.get("cache_hit")),
    )


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text or None
