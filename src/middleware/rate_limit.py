from __future__ import annotations

import json
from typing import Any

from fastapi import Request

from src.models.errors import InvalidRequestError, RateLimitError
from src.services.limit_counter import LimitCounter, RateLimitCheck


def estimate_tokens(payload: Any) -> int:
    if payload is None:
        return 0
    if isinstance(payload, (str, bytes)):
        text = payload.decode("utf-8", errors="ignore") if isinstance(payload, bytes) else payload
        return max(1, len(text) // 4)
    return max(1, len(json.dumps(payload, default=str)) // 4)


async def enforce_rate_limits(request: Request):
    await _check_and_acquire_rate_limits(request)

    try:
        yield
    except RateLimitError:
        raise
    finally:
        await _release_rate_limits(request)


async def _check_and_acquire_rate_limits(request: Request) -> None:
    if bool(getattr(request.state, "_rate_limit_checked", False)):
        return
    auth = getattr(request.state, "user_api_key", None)
    if auth is None:
        request.state._rate_limit_checked = True
        return

    limiter: LimitCounter = request.app.state.limit_counter
    try:
        body = await request.body()
        request._body = body  # noqa: SLF001 - FastAPI-compatible caching of request body
        tokens = estimate_tokens(body)
    except RuntimeError as exc:
        if "Stream consumed" in str(exc):
            # For multipart/form-data requests (file uploads), FastAPI may have already
            # consumed the request stream while parsing `UploadFile`/`Form` params.
            # Fall back to a minimal TPM estimate so we can still enforce RPM and
            # max-parallel limits without failing the request.
            tokens = 1
        else:
            raise InvalidRequestError(message="Could not parse request body for rate limiting") from exc

    key_rpm_limit = auth.key_rpm_limit if auth.key_rpm_limit is not None else auth.rpm_limit
    key_tpm_limit = auth.key_tpm_limit if auth.key_tpm_limit is not None else auth.tpm_limit
    checks: list[RateLimitCheck] = []

    def _add(scope: str, entity_id: str | None, limit: int | None, amount: int) -> None:
        if not entity_id or limit is None or limit <= 0 or amount <= 0:
            return
        checks.append(RateLimitCheck(scope=scope, entity_id=entity_id, limit=int(limit), amount=int(amount)))

    _add("org_rpm", auth.organization_id, auth.org_rpm_limit, 1)
    _add("team_rpm", auth.team_id, auth.team_rpm_limit, 1)
    _add("user_rpm", auth.user_id, auth.user_rpm_limit, 1)
    _add("key_rpm", auth.api_key, key_rpm_limit, 1)

    _add("org_tpm", auth.organization_id, auth.org_tpm_limit, tokens)
    _add("team_tpm", auth.team_id, auth.team_tpm_limit, tokens)
    _add("user_tpm", auth.user_id, auth.user_tpm_limit, tokens)
    _add("key_tpm", auth.api_key, key_tpm_limit, tokens)

    await limiter.check_rate_limits_atomic(checks)
    await limiter.acquire_parallel("key", auth.api_key, auth.max_parallel_requests)
    request.state._rate_limit_checked = True
    request.state._rate_limit_parallel_key = auth.api_key


async def _release_rate_limits(request: Request) -> None:
    if bool(getattr(request.state, "_rate_limit_released", False)):
        return
    api_key = getattr(request.state, "_rate_limit_parallel_key", None)
    if not api_key:
        request.state._rate_limit_released = True
        return
    limiter: LimitCounter = request.app.state.limit_counter
    await limiter.release_parallel("key", api_key)
    request.state._rate_limit_released = True
