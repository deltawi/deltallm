from __future__ import annotations

import json
from typing import Any

from fastapi import Request

from src.models.errors import RateLimitError
from src.services.limit_counter import LimitCounter


def estimate_tokens(payload: Any) -> int:
    if payload is None:
        return 0
    if isinstance(payload, (str, bytes)):
        text = payload.decode("utf-8", errors="ignore") if isinstance(payload, bytes) else payload
        return max(1, len(text) // 4)
    return max(1, len(json.dumps(payload, default=str)) // 4)


async def enforce_rate_limits(request: Request):
    auth = getattr(request.state, "user_api_key", None)
    if auth is None:
        yield
        return

    limiter: LimitCounter = request.app.state.limit_counter
    body = await request.body()
    request._body = body  # noqa: SLF001 - FastAPI-compatible caching of request body
    tokens = estimate_tokens(body)

    await limiter.check_rate_limit("key_rpm", auth.api_key, auth.rpm_limit, 1)
    await limiter.check_rate_limit("key_tpm", auth.api_key, auth.tpm_limit, tokens)
    await limiter.acquire_parallel("key", auth.api_key, auth.max_parallel_requests)

    try:
        yield
    except RateLimitError:
        raise
    finally:
        await limiter.release_parallel("key", auth.api_key)
