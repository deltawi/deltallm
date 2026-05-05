from __future__ import annotations

from email.parser import BytesParser
from email.policy import default
import json
import math
import time
from typing import Any
from urllib.parse import parse_qs

from fastapi import Request

from src.models.errors import InvalidRequestError, RateLimitError
from src.rate_limit_policy import (
    RateLimitState,
    acquire_rate_limit_controls,
    build_rate_limit_checks,
    compute_rate_limit_state,
    estimate_tokens,
)
from src.services.limit_counter import LimitCounter, RateLimitCheck

_compute_rate_limit_state = compute_rate_limit_state


def _normalize_model(model: Any) -> str | None:
    if not isinstance(model, str):
        return None
    normalized = model.strip()
    return normalized or None


def _extract_model_from_json(body: bytes) -> str | None:
    try:
        parsed = json.loads(body)
        if isinstance(parsed, dict):
            return _normalize_model(parsed.get("model"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        pass
    return None


def _extract_model_from_form_urlencoded(body: bytes) -> str | None:
    try:
        parsed = parse_qs(body.decode("utf-8"), keep_blank_values=True)
    except UnicodeDecodeError:
        return None
    values = parsed.get("model")
    if not values:
        return None
    return _normalize_model(values[0])


def _extract_model_from_multipart(body: bytes, content_type: str) -> str | None:
    if not body or "boundary=" not in content_type.lower():
        return None

    try:
        message = BytesParser(policy=default).parsebytes(
            f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8") + body
        )
    except ValueError:
        return None

    if not message.is_multipart():
        return None

    for part in message.iter_parts():
        if part.get_content_disposition() != "form-data":
            continue
        if part.get_param("name", header="content-disposition") != "model":
            continue

        payload = part.get_payload(decode=True)
        if payload is None:
            continue

        charset = part.get_content_charset() or "utf-8"
        try:
            model = payload.decode(charset, errors="ignore")
        except LookupError:
            model = payload.decode("utf-8", errors="ignore")
        return _normalize_model(model)

    return None


def _extract_model(body: bytes, content_type: str | None = None) -> str | None:
    if not body:
        return None

    raw_content_type = content_type or ""
    media_type = raw_content_type.split(";", 1)[0].strip().lower()

    if media_type == "multipart/form-data":
        return _extract_model_from_multipart(body, raw_content_type)
    if media_type == "application/x-www-form-urlencoded":
        return _extract_model_from_form_urlencoded(body)

    return _extract_model_from_json(body)


async def _extract_model_from_request(request: Request, body: bytes) -> str | None:
    content_type = request.headers.get("content-type")
    model = _extract_model(body, content_type)
    if model is not None:
        return model

    media_type = (content_type or "").split(";", 1)[0].strip().lower()
    if media_type not in {"multipart/form-data", "application/x-www-form-urlencoded"}:
        return None

    try:
        form = await request.form()
    except RuntimeError as exc:
        if "Stream consumed" in str(exc):
            return None
        raise InvalidRequestError(message="Could not parse request form for rate limiting") from exc

    return _normalize_model(form.get("model"))


def _build_429_state(
    checks: list[RateLimitCheck], exc: RateLimitError, window_reset_at: int,
) -> RateLimitState:
    state = RateLimitState(warning="near_limit")
    violated_scope = getattr(exc, "param", None) or ""

    now = time.time()
    for check in checks:
        is_rpm = check.scope.endswith("_rpm") or check.scope.endswith("_rph") or check.scope.endswith("_rpd")
        is_tpm = check.scope.endswith("_tpm") or check.scope.endswith("_tpd")
        if not is_rpm and not is_tpm:
            is_rpm = check.amount == 1
            is_tpm = not is_rpm

        check_reset = int((math.floor(now / check.window_seconds) + 1) * check.window_seconds)

        if is_rpm and (state.rpm_limit == 0 or check.scope == violated_scope):
            state.rpm_limit = check.limit
            state.rpm_remaining = 0
            state.rpm_reset = check_reset
            state.rpm_scope = check.scope
        if is_tpm and (state.tpm_limit == 0 or check.scope == violated_scope):
            state.tpm_limit = check.limit
            state.tpm_remaining = 0
            state.tpm_reset = check_reset
            state.tpm_scope = check.scope

    return state


def build_rate_limit_headers(state: RateLimitState) -> dict[str, str]:
    headers: dict[str, str] = {}
    if state.rpm_limit > 0 or state.tpm_limit > 0:
        headers["x-ratelimit-limit-requests"] = str(state.rpm_limit)
        headers["x-ratelimit-remaining-requests"] = str(state.rpm_remaining)
        headers["x-ratelimit-reset-requests"] = str(state.rpm_reset)
        headers["x-ratelimit-limit-tokens"] = str(state.tpm_limit)
        headers["x-ratelimit-remaining-tokens"] = str(state.tpm_remaining)
        headers["x-ratelimit-reset-tokens"] = str(state.tpm_reset)

        scope_parts = []
        if state.rpm_scope:
            scope_parts.append(state.rpm_scope)
        if state.tpm_scope:
            scope_parts.append(state.tpm_scope)
        if scope_parts:
            headers["x-deltallm-ratelimit-scope"] = ",".join(scope_parts)

    if state.warning:
        headers["x-ratelimit-warning"] = state.warning

    return headers


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
            tokens = 1
            body = b""
        else:
            raise InvalidRequestError(message="Could not parse request body for rate limiting") from exc

    model = await _extract_model_from_request(request, body)
    request.state._rate_limit_model = model

    try:
        lease, rate_limit_state = await acquire_rate_limit_controls(
            limiter=limiter,
            auth=auth,
            tokens=tokens,
            model=model,
        )
        request.state._rate_limit_state = rate_limit_state
    except RateLimitError as exc:
        checks = build_rate_limit_checks(auth=auth, tokens=tokens, model=model)
        now = time.time()
        min_window = min((c.window_seconds for c in checks), default=60)
        window_reset_at = int((math.floor(now / min_window) + 1) * min_window)
        state_429 = _build_429_state(checks, exc, window_reset_at)
        request.state._rate_limit_state = state_429
        raise

    request.state._rate_limit_checked = True
    request.state._rate_limit_parallel_key = lease.parallel_entity_id


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
