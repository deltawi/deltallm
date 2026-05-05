from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from src.services.limit_counter import LimitCounter, RateLimitCheck, RateLimitResult


def estimate_tokens(payload: Any) -> int:
    if payload is None:
        return 0
    if isinstance(payload, (str, bytes)):
        text = payload.decode("utf-8", errors="ignore") if isinstance(payload, bytes) else payload
        return max(1, len(text) // 4)
    return max(1, len(json.dumps(payload, default=str)) // 4)


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


@dataclass(slots=True)
class RateLimitLease:
    parallel_scope: str | None = None
    parallel_entity_id: str | None = None


def compute_rate_limit_state(result: RateLimitResult, checks: list[RateLimitCheck]) -> RateLimitState:
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

        is_rpm = (
            check.scope.endswith("_rpm")
            or check.scope.endswith("_rpm_limit")
            or check.scope.endswith("_rph")
            or check.scope.endswith("_rpd")
        )
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


def build_rate_limit_checks(*, auth: Any, tokens: int, model: str | None) -> list[RateLimitCheck]:
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

    def _add_h(scope: str, entity_id: str | None, limit: int | None, amount: int) -> None:
        if not entity_id or limit is None or limit <= 0 or amount <= 0:
            return
        checks.append(
            RateLimitCheck(scope=scope, entity_id=entity_id, limit=int(limit), amount=int(amount), window_seconds=3600)
        )

    def _add_d(scope: str, entity_id: str | None, limit: int | None, amount: int) -> None:
        if not entity_id or limit is None or limit <= 0 or amount <= 0:
            return
        checks.append(
            RateLimitCheck(scope=scope, entity_id=entity_id, limit=int(limit), amount=int(amount), window_seconds=86400)
        )

    _add_h("org_rph", auth.organization_id, auth.org_rph_limit, 1)
    _add_h("team_rph", auth.team_id, auth.team_rph_limit, 1)
    _add_h("user_rph", auth.user_id, auth.user_rph_limit, 1)
    _add_h("key_rph", auth.api_key, auth.key_rph_limit, 1)

    _add_d("org_rpd", auth.organization_id, auth.org_rpd_limit, 1)
    _add_d("team_rpd", auth.team_id, auth.team_rpd_limit, 1)
    _add_d("user_rpd", auth.user_id, auth.user_rpd_limit, 1)
    _add_d("key_rpd", auth.api_key, auth.key_rpd_limit, 1)

    _add_d("org_tpd", auth.organization_id, auth.org_tpd_limit, tokens)
    _add_d("team_tpd", auth.team_id, auth.team_tpd_limit, tokens)
    _add_d("user_tpd", auth.user_id, auth.user_tpd_limit, tokens)
    _add_d("key_tpd", auth.api_key, auth.key_tpd_limit, tokens)

    if model:
        team_model_rpm = _model_limit(auth.team_model_rpm_limit, model)
        team_model_tpm = _model_limit(auth.team_model_tpm_limit, model)
        org_model_rpm = _model_limit(auth.org_model_rpm_limit, model)
        org_model_tpm = _model_limit(auth.org_model_tpm_limit, model)

        _add("team_model_rpm", f"{auth.team_id}:{model}" if auth.team_id else None, team_model_rpm, 1)
        _add("team_model_tpm", f"{auth.team_id}:{model}" if auth.team_id else None, team_model_tpm, tokens)
        _add("org_model_rpm", f"{auth.organization_id}:{model}" if auth.organization_id else None, org_model_rpm, 1)
        _add("org_model_tpm", f"{auth.organization_id}:{model}" if auth.organization_id else None, org_model_tpm, tokens)

    return checks


async def acquire_rate_limit_controls(
    *,
    limiter: LimitCounter,
    auth: Any,
    tokens: int,
    model: str | None,
) -> tuple[RateLimitLease, RateLimitState]:
    checks = build_rate_limit_checks(auth=auth, tokens=tokens, model=model)
    result = await limiter.check_rate_limits_atomic(checks)
    rate_limit_state = compute_rate_limit_state(result, checks)

    await limiter.acquire_parallel("key", auth.api_key, auth.max_parallel_requests)
    parallel_entity_id = auth.api_key if auth.api_key and auth.max_parallel_requests and auth.max_parallel_requests > 0 else None
    return RateLimitLease(
        parallel_scope="key" if parallel_entity_id else None,
        parallel_entity_id=parallel_entity_id,
    ), rate_limit_state


async def release_rate_limit_controls(*, limiter: LimitCounter, lease: RateLimitLease) -> None:
    if not lease.parallel_scope or not lease.parallel_entity_id:
        return
    await limiter.release_parallel(lease.parallel_scope, lease.parallel_entity_id)
