from __future__ import annotations

from contextlib import suppress
import json
import time
from dataclasses import dataclass, field
from typing import Any

from src.models.errors import RateLimitError
from src.services.limit_counter import (
    LegacyParallelLease,
    LimitCounter,
    ParallelLimitCheck,
    ParallelLimitLease,
    RateLimitCheck,
    RateLimitResult,
)
from src.tier_rate_limit_policy import RateLimitMode, build_tier_limit_controls, build_tier_rate_limit_checks


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
    legacy_parallel_lease: LegacyParallelLease | None = None
    parallel_leases: tuple[ParallelLimitLease, ...] = ()
    _pending_parallel_leases: list[ParallelLimitLease] = field(init=False, repr=False)
    _legacy_parallel_pending: bool = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._pending_parallel_leases = list(self.parallel_leases)
        self._legacy_parallel_pending = self.legacy_parallel_lease is not None

    @property
    def pending_parallel_acquisitions(self) -> tuple[ParallelLimitCheck, ...]:
        checks = []
        if self._legacy_parallel_pending and self.legacy_parallel_lease is not None:
            checks.append(self.legacy_parallel_lease.check)
        checks.extend(lease.check for lease in self._pending_parallel_leases)
        return tuple(checks)

    @property
    def pending_parallel_leases(self) -> tuple[ParallelLimitLease, ...]:
        return tuple(self._pending_parallel_leases)

    @property
    def pending_legacy_parallel_lease(self) -> LegacyParallelLease | None:
        if not self._legacy_parallel_pending:
            return None
        return self.legacy_parallel_lease

    @property
    def refreshable_parallel_leases(self) -> tuple[ParallelLimitLease, ...]:
        return tuple(lease for lease in self._pending_parallel_leases if lease.backend == "redis")

    @property
    def refreshable_legacy_parallel_lease(self) -> LegacyParallelLease | None:
        if not self._legacy_parallel_pending:
            return None
        if self.legacy_parallel_lease is None or self.legacy_parallel_lease.backend != "redis":
            return None
        return self.legacy_parallel_lease

    def mark_parallel_released(self, lease: ParallelLimitLease) -> None:
        with suppress(ValueError):
            self._pending_parallel_leases.remove(lease)

    def mark_legacy_parallel_released(self) -> None:
        self._legacy_parallel_pending = False


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


def _build_standard_rate_limit_checks(
    *,
    auth: Any,
    tokens: int,
    model: str | None,
) -> list[RateLimitCheck]:
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


def build_rate_limit_checks(
    *,
    auth: Any,
    tokens: int,
    model: str | None,
    tier_policy_service: Any | None = None,
    tier_policy_mode: str = "disabled",
    tier_policy_missing_service_mode: str = "fail_open",
    mode: RateLimitMode | str = "sync",
) -> list[RateLimitCheck]:
    checks = _build_standard_rate_limit_checks(auth=auth, tokens=tokens, model=model)
    checks.extend(
        build_tier_rate_limit_checks(
            auth=auth,
            tokens=tokens,
            model=model,
            tier_policy_service=tier_policy_service,
            tier_policy_mode=tier_policy_mode,
            tier_policy_missing_service_mode=tier_policy_missing_service_mode,
            mode=mode,
        )
    )

    return checks


def build_parallel_limit_checks(
    *,
    auth: Any,
    tier_parallel_checks: tuple[ParallelLimitCheck, ...] = (),
) -> list[ParallelLimitCheck]:
    checks: list[ParallelLimitCheck] = []
    key_check = _parallel_limit_check("key", getattr(auth, "api_key", None), getattr(auth, "max_parallel_requests", None))
    if key_check is not None:
        checks.append(key_check)
    checks.extend(tier_parallel_checks)
    return checks


async def acquire_rate_limit_controls(
    *,
    limiter: LimitCounter,
    auth: Any,
    tokens: int,
    model: str | None,
    tier_policy_service: Any | None = None,
    tier_policy_mode: str = "disabled",
    tier_policy_missing_service_mode: str = "fail_open",
    mode: RateLimitMode | str = "sync",
) -> tuple[RateLimitLease, RateLimitState]:
    checks = _build_standard_rate_limit_checks(auth=auth, tokens=tokens, model=model)
    tier_controls = build_tier_limit_controls(
        auth=auth,
        tokens=tokens,
        model=model,
        tier_policy_service=tier_policy_service,
        tier_policy_mode=tier_policy_mode,
        tier_policy_missing_service_mode=tier_policy_missing_service_mode,
        mode=mode,
    )
    checks.extend(tier_controls.rate_checks)
    try:
        result = await limiter.check_rate_limits_atomic(checks)
    except RateLimitError as exc:
        _annotate_rate_limit_error(exc, checks=checks)
        raise
    rate_limit_state = compute_rate_limit_state(result, checks)

    legacy_parallel_check = _parallel_limit_check(
        "key",
        getattr(auth, "api_key", None),
        getattr(auth, "max_parallel_requests", None),
    )
    acquired_legacy_parallel_lease = None
    tier_parallel_leases: list[ParallelLimitLease] = []
    try:
        if legacy_parallel_check is not None:
            acquired_legacy_parallel_lease = await _acquire_legacy_parallel_limit_check(
                limiter=limiter,
                check=legacy_parallel_check,
                rate_checks=checks,
            )
        tier_parallel_leases = await _acquire_parallel_limit_checks(
            limiter=limiter,
            checks=list(tier_controls.parallel_checks),
            rate_checks=checks,
        )
    except Exception:
        await _release_acquired_parallel_controls(
            limiter=limiter,
            legacy_lease=acquired_legacy_parallel_lease,
            leases=tier_parallel_leases,
        )
        raise

    return RateLimitLease(
        legacy_parallel_lease=acquired_legacy_parallel_lease,
        parallel_leases=tuple(tier_parallel_leases),
    ), rate_limit_state


async def release_rate_limit_controls(*, limiter: LimitCounter, lease: RateLimitLease) -> None:
    pending = list(reversed(lease.pending_parallel_leases))
    legacy_lease = lease.pending_legacy_parallel_lease
    if not pending and legacy_lease is None:
        return

    release_error: Exception | None = None
    if pending:
        try:
            await limiter.release_parallel_leases(pending)
        except Exception as exc:
            release_error = exc
        else:
            for parallel_lease in pending:
                lease.mark_parallel_released(parallel_lease)

    if legacy_lease is not None:
        try:
            await limiter.release_legacy_parallel_lease(legacy_lease)
        except Exception as exc:
            if release_error is None:
                release_error = exc
        else:
            lease.mark_legacy_parallel_released()

    if release_error is not None:
        raise release_error


def _parallel_limit_check(scope: str, entity_id: object, limit: object) -> ParallelLimitCheck | None:
    normalized_entity_id = _normalize_id(entity_id)
    normalized_limit = _positive_int_or_none(limit)
    if normalized_entity_id is None or normalized_limit is None:
        return None
    return ParallelLimitCheck(scope=scope, entity_id=normalized_entity_id, limit=normalized_limit)


async def _acquire_parallel_limit_checks(
    *,
    limiter: LimitCounter,
    checks: list[ParallelLimitCheck],
    rate_checks: list[RateLimitCheck],
) -> list[ParallelLimitLease]:
    try:
        return list(await limiter.acquire_parallel_leases(checks))
    except RateLimitError as exc:
        failed_check = _parallel_check_for_error(exc, checks)
        _ensure_parallel_error_fields(exc, failed_check)
        _annotate_rate_limit_error(
            exc,
            checks=rate_checks,
            state=_parallel_429_state(failed_check, retry_after=getattr(exc, "retry_after", None)),
        )
        raise


async def _acquire_legacy_parallel_limit_check(
    *,
    limiter: LimitCounter,
    check: ParallelLimitCheck,
    rate_checks: list[RateLimitCheck],
) -> LegacyParallelLease | None:
    try:
        return await limiter.acquire_legacy_parallel_lease(check.scope, check.entity_id, check.limit)
    except RateLimitError as exc:
        _ensure_parallel_error_fields(exc, check)
        _annotate_rate_limit_error(
            exc,
            checks=rate_checks,
            state=_parallel_429_state(check, retry_after=getattr(exc, "retry_after", None)),
        )
        raise


async def _release_acquired_parallel_controls(
    *,
    limiter: LimitCounter,
    legacy_lease: LegacyParallelLease | None,
    leases: list[ParallelLimitLease],
) -> None:
    if leases:
        with suppress(Exception):
            await limiter.release_parallel_leases(list(reversed(leases)))
    if legacy_lease is not None:
        with suppress(Exception):
            await limiter.release_legacy_parallel_lease(legacy_lease)


def _parallel_check_for_error(exc: RateLimitError, checks: list[ParallelLimitCheck]) -> ParallelLimitCheck:
    failed_scope = getattr(exc, "param", None)
    if failed_scope is not None:
        for check in checks:
            if check.scope == failed_scope:
                return check
    return checks[0] if checks else ParallelLimitCheck(scope="key", entity_id="", limit=1)


def _annotate_rate_limit_error(
    exc: RateLimitError,
    *,
    checks: list[RateLimitCheck],
    state: RateLimitState | None = None,
) -> None:
    setattr(exc, "rate_limit_checks", list(checks))
    if state is not None:
        setattr(exc, "rate_limit_state", state)


def _ensure_parallel_error_fields(exc: RateLimitError, check: ParallelLimitCheck) -> None:
    if check.scope == "key":
        return
    if getattr(exc, "param", None) is None:
        exc.param = check.scope
    if getattr(exc, "code", None) is None:
        exc.code = _parallel_limit_error_code(check.scope)


def _parallel_429_state(check: ParallelLimitCheck, *, retry_after: object) -> RateLimitState:
    retry_after_seconds = _positive_int_or_none(retry_after) or 1
    return RateLimitState(
        rpm_limit=check.limit,
        rpm_remaining=0,
        rpm_reset=int(time.time()) + retry_after_seconds,
        rpm_scope=check.scope,
        warning="near_limit",
    )


def _normalize_id(value: object) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None


def _positive_int_or_none(value: object) -> int | None:
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return None
    return normalized if normalized > 0 else None


def _parallel_limit_error_code(scope: str) -> str:
    return f"{scope}_exceeded" if scope.endswith("_parallel") else f"{scope}_parallel_exceeded"
