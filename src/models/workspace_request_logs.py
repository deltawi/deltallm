"""Customer-facing request log rows for one external workspace.

The field list is a fixed allowlist. Platform-internal columns (metadata,
api_base, cache keys, end users, owners, tags and provider error bodies) are
not part of this contract.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class WorkspaceRequestLog(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    request_id: str | None
    start_time: datetime
    end_time: datetime | None
    latency_ms: int | None
    call_type: str | None
    model: str | None
    api_key: str | None
    status: str
    http_status_code: int | None
    error_type: str | None
    prompt_tokens: int
    completion_tokens: int
    prompt_tokens_cached: int
    completion_tokens_cached: int
    spend: float
    cache_hit: bool


class WorkspaceRequestLogPagination(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    limit: int
    count: int
    has_more: bool
    next_cursor: str | None


class WorkspaceRequestLogPage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    logs: list[WorkspaceRequestLog]
    pagination: WorkspaceRequestLogPagination
    start: datetime
    end: datetime
