from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any

from src.callbacks.base import CustomLogger
from src.metrics import (
    increment_cache_hit,
    increment_cache_miss,
    increment_request,
    increment_request_failure,
    increment_spend,
    increment_usage,
    observe_api_latency,
    observe_request_latency,
)


class PrometheusCallback(CustomLogger):
    """Prometheus callback backed by existing metrics registry/functions."""

    async def async_log_success_event(
        self,
        kwargs: dict[str, Any],
        response_obj: Any,
        start_time: datetime,
        end_time: datetime,
    ) -> None:
        del response_obj
        model = str(kwargs.get("model") or "unknown")
        provider = str(kwargs.get("api_provider") or "unknown")
        api_key = str(kwargs.get("api_key") or "")
        user = kwargs.get("user")
        team = kwargs.get("team_id")
        usage = kwargs.get("usage") or {}

        increment_request(
            model=model,
            api_provider=provider,
            api_key=api_key,
            user=user,
            team=team,
            status_code=200,
        )
        increment_usage(
            model=model,
            api_provider=provider,
            api_key=api_key,
            user=user,
            team=team,
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
        )
        increment_spend(
            model=model,
            api_provider=provider,
            api_key=api_key,
            user=user,
            team=team,
            spend=float(kwargs.get("response_cost") or 0.0),
        )

        if kwargs.get("cache_hit"):
            increment_cache_hit(model=model, cache_type="proxy")
        else:
            increment_cache_miss(model=model, cache_type="proxy")

        observe_request_latency(
            model=model,
            api_provider=provider,
            status_code=200,
            latency_seconds=max(0.0, (end_time - start_time).total_seconds()),
        )
        api_latency_ms = kwargs.get("api_latency_ms")
        if api_latency_ms is not None:
            observe_api_latency(
                model=model,
                api_provider=provider,
                latency_seconds=max(0.0, float(api_latency_ms) / 1000.0),
            )

    async def async_log_failure_event(
        self,
        kwargs: dict[str, Any],
        exception: Exception,
        start_time: datetime,
        end_time: datetime,
    ) -> None:
        model = str(kwargs.get("model") or "unknown")
        provider = str(kwargs.get("api_provider") or "unknown")
        api_key = str(kwargs.get("api_key") or "")
        user = kwargs.get("user")
        team = kwargs.get("team_id")

        increment_request(
            model=model,
            api_provider=provider,
            api_key=api_key,
            user=user,
            team=team,
            status_code=500,
        )
        increment_request_failure(
            model=model,
            api_provider=provider,
            error_type=exception.__class__.__name__,
        )
        observe_request_latency(
            model=model,
            api_provider=provider,
            status_code=500,
            latency_seconds=max(0.0, (end_time - start_time).total_seconds()),
        )

    @staticmethod
    def _hash_key(key: str) -> str:
        return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
