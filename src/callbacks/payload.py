from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class TokenUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    prompt_tokens_cached: int | None = None
    completion_tokens_cached: int | None = None


class StandardLoggingPayload(BaseModel):
    litellm_call_id: str
    request_id: str
    call_type: str
    model: str
    deployment_model: str | None = None
    messages: list[dict[str, Any]] | None = None
    response_obj: dict[str, Any] | None = None
    usage: TokenUsage = Field(default_factory=TokenUsage)
    response_cost: float = 0.0
    stream: bool = False
    temperature: float | None = None
    max_tokens: int | None = None
    top_p: float | None = None
    api_key: str = ""
    user: str | None = None
    team_id: str | None = None
    organization_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    cache_hit: bool = False
    cache_key: str | None = None
    start_time: datetime
    end_time: datetime
    total_latency_ms: float
    api_latency_ms: float | None = None
    api_provider: str
    api_base: str | None = None
    error_info: dict[str, Any] | None = None


def _usage_from_response(response_obj: dict[str, Any] | None) -> TokenUsage:
    usage = (response_obj or {}).get("usage") if isinstance(response_obj, dict) else None
    usage = usage or {}
    return TokenUsage(
        prompt_tokens=int(usage.get("prompt_tokens") or 0),
        completion_tokens=int(usage.get("completion_tokens") or 0),
        total_tokens=int(usage.get("total_tokens") or 0),
        prompt_tokens_cached=(
            int(usage.get("prompt_tokens_cached")) if usage.get("prompt_tokens_cached") is not None else None
        ),
        completion_tokens_cached=(
            int(usage.get("completion_tokens_cached")) if usage.get("completion_tokens_cached") is not None else None
        ),
    )


def _api_provider(deployment_model: str | None) -> str:
    if deployment_model and "/" in deployment_model:
        return deployment_model.split("/", 1)[0]
    return "unknown"


def build_standard_logging_payload(
    *,
    call_type: str,
    request_id: str | None,
    model: str,
    deployment_model: str | None,
    request_payload: dict[str, Any],
    response_obj: dict[str, Any] | None,
    user_api_key_dict: dict[str, Any],
    start_time: datetime,
    end_time: datetime,
    api_base: str | None,
    cache_hit: bool = False,
    cache_key: str | None = None,
    response_cost: float = 0.0,
    api_latency_ms: float | None = None,
    error_info: dict[str, Any] | None = None,
    turn_off_message_logging: bool = False,
) -> StandardLoggingPayload:
    metadata = dict(request_payload.get("metadata") or {})
    tags = metadata.get("tags") if isinstance(metadata.get("tags"), list) else []

    if start_time.tzinfo is None:
        start_time = start_time.replace(tzinfo=UTC)
    if end_time.tzinfo is None:
        end_time = end_time.replace(tzinfo=UTC)

    litellm_call_id = str(metadata.get("litellm_call_id") or uuid4())
    messages = None if turn_off_message_logging else request_payload.get("messages")

    return StandardLoggingPayload(
        litellm_call_id=litellm_call_id,
        request_id=request_id or litellm_call_id,
        call_type=call_type,
        model=model,
        deployment_model=deployment_model,
        messages=messages if isinstance(messages, list) else None,
        response_obj=response_obj,
        usage=_usage_from_response(response_obj),
        response_cost=float(response_cost),
        stream=bool(request_payload.get("stream") or False),
        temperature=request_payload.get("temperature"),
        max_tokens=request_payload.get("max_tokens"),
        top_p=request_payload.get("top_p"),
        api_key=str(user_api_key_dict.get("api_key") or ""),
        user=request_payload.get("user") or user_api_key_dict.get("user_id"),
        team_id=user_api_key_dict.get("team_id"),
        organization_id=(user_api_key_dict.get("metadata") or {}).get("organization_id")
        if isinstance(user_api_key_dict.get("metadata"), dict)
        else None,
        metadata=metadata,
        tags=[str(tag) for tag in tags],
        cache_hit=cache_hit,
        cache_key=cache_key,
        start_time=start_time,
        end_time=end_time,
        total_latency_ms=max(0.0, (end_time - start_time).total_seconds() * 1000),
        api_latency_ms=api_latency_ms,
        api_provider=_api_provider(deployment_model),
        api_base=api_base,
        error_info=error_info,
    )
