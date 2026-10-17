from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class RouteGroupMemberQualityResponse(BaseModel):
    deployment_id: str
    calls: int
    failed_calls: int
    provider_errors: int
    empty_answers: int
    bad_tool_calls: int
    bad_json: int
    failure_rate: float | None
    avg_first_token_ms: float | None
    output_tokens_per_second: float | None
    # Set only when the group sends low-quality models last and this one qualifies.
    sent_last_reason: Literal["failed_calls", "slow_first_token"] | None = None


class RouteGroupQualityResponse(BaseModel):
    group_key: str
    # "local" means Redis was unavailable, so the numbers cover one gateway process.
    source: Literal["cluster", "local", "unavailable"]
    window_seconds: int
    deprioritize: bool
    sensitivity: Literal["relaxed", "balanced", "strict"] | None
    members: list[RouteGroupMemberQualityResponse]
