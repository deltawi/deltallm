from __future__ import annotations

from email.parser import BytesParser
from email.policy import default
import json
import math
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs

from fastapi import Request

from src.models.errors import InvalidRequestError, RateLimitError
from src.services.limit_counter import LimitCounter, RateLimitCheck, RateLimitResult


def estimate_tokens(payload: Any) -> int:
    if payload is None:
        return 0
    if isinstance(payload, (str, bytes)):
        text = payload.decode("utf-8", errors="ignore") if isinstance(payload, bytes) else payload
        return max(1, len(text) // 4)
    return max(1, len(json.dumps(payload, default=str)) // 4)


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


def _model_limit(limits: dict[str, int] | None, model: str | None) -> int | None:
    if limits is None or not model:
        return None
    exact = limits.get(model)
    if exact is not None:
        try:
            v = int(exact)
            return v if v > 0 else None
        except (TypeError, ValueError):
            return None
    best_match: tuple[int, int | None] = (-1, None)
    for pattern, value in limits.items():
        if pattern.endswith("*"):
            prefix = pattern[:-1]
            if model.startswith(prefix) and len(prefix) > best_match[0]:
                try:
                    v = int(value)
                    best_match = (len(prefix), v if v > 0 else None)
                except (TypeError, ValueError):
                    pass
    return best_match[1] if best_match[0] >= 0 else None


@dataclass
class RateLimitState:
    rpm_limit: int = 0
    rpm_remaining: int = 0
    rpm_reset: int = 0
    rpm_scope: str = ""
    tpm_limit: int = 0
    tpm_remaining: int = 0
    tpm_reset: int = 0
    tpm_scope: str = ""
    warning: str | None = None


def _compute_rate_limit_state(result: RateLimitResult, checks: list[RateLimitCheck]) -> RateLimitState:
    if not result.checks or not result.current_values:
        return RateLimitState()

    state = RateLimitState()
    reset_at = result.window_reset_at

    best_rpm_ratio = -1.0
    best_tpm_ratio = -1.0
    max_usage_ratio = 0.0

    for i, check in enumerate(result.checks):
        if i >= len(result.current_values):
            break
        current = result.current_values[i]
        remaining = max(0, check.limit - current)
        ratio = current / check.limit if check.limit > 0 else 0.0

        if ratio > max_usage_ratio:
            max_usage_ratio = ratio

        is_rpm = check.scope.endswith("_rpm") or check.scope.endswith("_rpm_limit") or check.scope.endswith("_rph") or check.scope.endswith("_rpd")
        is_tpm = check.scope.endswith("_tpm") or check.scope.endswith("_tpm_limit") or check.scope.endswith("_tpd")

        if not is_rpm and not is_tpm:
            if check.amount == 1:
                is_rpm = True
            else:
                is_tpm = True

        check_reset = result.window_resets[i] if i < len(result.window_resets) else reset_at

        if is_rpm and ratio > best_rpm_ratio:
            best_rpm_ratio = ratio
            state.rpm_limit = check.limit
            state.rpm_remaining = remaining
            state.rpm_reset = check_reset
            state.rpm_scope = check.scope

        if is_tpm and ratio > best_tpm_ratio:
            best_tpm_ratio = ratio
            state.tpm_limit = check.limit
            state.tpm_remaining = remaining
            state.tpm_reset = check_reset
            state.tpm_scope = check.scope

    if max_usage_ratio >= 0.95:
        state.warning = "near_limit"
    elif max_usage_ratio >= 0.80:
        state.warning = "approaching_limit"

    return state


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

    key_rph = auth.key_rph_limit
    key_rpd = auth.key_rpd_limit
    key_tpd = auth.key_tpd_limit

    def _add_h(scope: str, entity_id: str | None, limit: int | None, amount: int) -> None:
        if not entity_id or limit is None or limit <= 0 or amount <= 0:
            return
        checks.append(RateLimitCheck(scope=scope, entity_id=entity_id, limit=int(limit), amount=int(amount), window_seconds=3600))

    def _add_d(scope: str, entity_id: str | None, limit: int | None, amount: int) -> None:
        if not entity_id or limit is None or limit <= 0 or amount <= 0:
            return
        checks.append(RateLimitCheck(scope=scope, entity_id=entity_id, limit=int(limit), amount=int(amount), window_seconds=86400))

    _add_h("org_rph", auth.organization_id, auth.org_rph_limit, 1)
    _add_h("team_rph", auth.team_id, auth.team_rph_limit, 1)
    _add_h("user_rph", auth.user_id, auth.user_rph_limit, 1)
    _add_h("key_rph", auth.api_key, key_rph, 1)

    _add_d("org_rpd", auth.organization_id, auth.org_rpd_limit, 1)
    _add_d("team_rpd", auth.team_id, auth.team_rpd_limit, 1)
    _add_d("user_rpd", auth.user_id, auth.user_rpd_limit, 1)
    _add_d("key_rpd", auth.api_key, key_rpd, 1)

    _add_d("org_tpd", auth.organization_id, auth.org_tpd_limit, tokens)
    _add_d("team_tpd", auth.team_id, auth.team_tpd_limit, tokens)
    _add_d("user_tpd", auth.user_id, auth.user_tpd_limit, tokens)
    _add_d("key_tpd", auth.api_key, key_tpd, tokens)

    if model:
        team_model_rpm = _model_limit(auth.team_model_rpm_limit, model)
        team_model_tpm = _model_limit(auth.team_model_tpm_limit, model)
        org_model_rpm = _model_limit(auth.org_model_rpm_limit, model)
        org_model_tpm = _model_limit(auth.org_model_tpm_limit, model)

        _add("team_model_rpm", f"{auth.team_id}:{model}" if auth.team_id else None, team_model_rpm, 1)
        _add("team_model_tpm", f"{auth.team_id}:{model}" if auth.team_id else None, team_model_tpm, tokens)
        _add("org_model_rpm", f"{auth.organization_id}:{model}" if auth.organization_id else None, org_model_rpm, 1)
        _add("org_model_tpm", f"{auth.organization_id}:{model}" if auth.organization_id else None, org_model_tpm, tokens)

    now = time.time()
    min_window = min((c.window_seconds for c in checks), default=60)
    window_reset_at = int((math.floor(now / min_window) + 1) * min_window)
    try:
        result = await limiter.check_rate_limits_atomic(checks)
        rate_limit_state = _compute_rate_limit_state(result, checks)
        request.state._rate_limit_state = rate_limit_state
    except RateLimitError as exc:
        state_429 = _build_429_state(checks, exc, window_reset_at)
        request.state._rate_limit_state = state_429
        raise

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
