from __future__ import annotations

import asyncio
from dataclasses import dataclass
import math
import time
from typing import Any, Literal

from src.models.errors import RateLimitError, ServiceUnavailableError
from src.services.tier_capacity_fair_share import (
    FAIR_SHARE_ACTIVE_CLEANUP_LIMIT,
    FAIR_SHARE_WEIGHT_SCALE,
    DEFAULT_ACTIVE_TTL_SECONDS,
    FAIR_SHARE_WINDOW_SECONDS,
    TierFairShareCheck,
    TierFairShareDecision,
    fair_share_active_count_key,
    fair_share_active_key,
    fair_share_boost_key,
    fair_share_cleanup_lag_key,
    fair_share_limit_hit_heatmap_key,
    fair_share_limit_hit_heatmap_rank_key,
    fair_share_limit_hit_total_key,
    fair_share_org_counter_key,
    fair_share_pool_counter_key,
    fair_share_total_weight_key,
    fair_share_usage_rank_key,
    fair_share_weight_key,
    normalized_burst_multiplier,
    normalized_saturation_threshold,
)
from src.services.tier_fair_share_admission_lua import RATE_AND_FAIR_SHARE_LUA, RATE_AND_FAIR_SHARE_SCRIPT


FAIR_SHARE_SCRIPT = RATE_AND_FAIR_SHARE_SCRIPT


@dataclass(slots=True)
class _FallbackPoolState:
    active_orgs: dict[str, tuple[float, float]]
    active_count: int
    total_weight: float
    effective_weight: float


class TierFairShareCounter:
    def __init__(self, *, redis_client: Any | None = None, degraded_mode: str = "fail_open") -> None:
        self.redis = redis_client
        self.degraded_mode = degraded_mode if degraded_mode in {"fail_open", "fail_closed"} else "fail_open"
        self._fallback_counters: dict[str, tuple[int, int]] = {}
        self._fallback_active_orgs: dict[str, dict[str, tuple[float, float]]] = {}
        self._fallback_lock = asyncio.Lock()

    @staticmethod
    def _window_id(window_seconds: int) -> int:
        return math.floor(time.time() / window_seconds)

    async def check(
        self,
        checks: list[TierFairShareCheck],
        *,
        active_ttl_seconds: int = DEFAULT_ACTIVE_TTL_SECONDS,
    ) -> tuple[TierFairShareDecision, ...]:
        normalized = [
            check
            for check in checks
            if (
                (check.rpm_capacity is not None and check.rpm_capacity > 0 and check.request_amount > 0)
                or (check.tpm_capacity is not None and check.tpm_capacity > 0 and check.token_amount > 0)
            )
        ]
        if not normalized:
            return ()

        ttl_seconds = max(1, int(active_ttl_seconds))
        if self.redis is None:
            return await self._check_fallback(normalized, active_ttl_seconds=ttl_seconds)

        now = time.time()
        window_id = self._window_id(FAIR_SHARE_WINDOW_SECONDS)
        keys: list[str] = []
        fair_args: list[str] = []
        for check in normalized:
            keys.extend(_fair_share_script_keys(check, window_id=window_id))
            fair_args.extend(_fair_share_script_args(check, now=now, ttl_seconds=ttl_seconds))
        argv = [
            "0",
            str(len(normalized)),
            "0",
            "0",
            str(int(now * 1000)),
            str(int(now * 1000)),
            "1",
            *fair_args,
        ]
        try:
            raw = await RATE_AND_FAIR_SHARE_LUA.eval(self.redis, len(keys), *keys, *argv)
        except Exception:
            await self._handle_redis_degraded()
            return await self._check_fallback(normalized, active_ttl_seconds=ttl_seconds)

        values = list(raw) if isinstance(raw, (list, tuple)) else []
        ok = int(values[0]) if values else 1
        if ok == 0:
            failed_index = int(_raw_at(values, 2, 1)) - 1
            failed_index = max(0, min(failed_index, len(normalized) - 1))
            decision = _fair_share_decision_from_raw(normalized[failed_index], values[3:15])
            raise _fair_share_rate_limit_error(decision, now=now)

        fair_count = int(_raw_at(values, 2, len(normalized)))
        fair_start = 3
        return tuple(
            _fair_share_decision_from_raw(
                normalized[index],
                values[fair_start + (index * 12) : fair_start + ((index + 1) * 12)],
            )
            for index in range(fair_count)
        )

    async def record_limit_hit(
        self,
        *,
        pool_key: str,
        callable_key: str,
        organization_id: str,
        scope: str,
        tier_key: str | None,
    ) -> None:
        field = "|".join([pool_key, callable_key, organization_id, scope, tier_key or "none"])
        window_id = self._window_id(FAIR_SHARE_WINDOW_SECONDS)
        key = fair_share_limit_hit_heatmap_key(window_id)
        if self.redis is None:
            async with self._fallback_lock:
                fallback_key = f"{key}:{field}"
                expiry, current = self._fallback_counters.get(
                    fallback_key,
                    (int(time.time()) + FAIR_SHARE_WINDOW_SECONDS, 0),
                )
                self._fallback_counters[fallback_key] = (expiry, current + 1)
            return
        try:
            rank_key = fair_share_limit_hit_heatmap_rank_key(window_id)
            total_key = fair_share_limit_hit_total_key(window_id)
            pipe = self.redis.pipeline()
            pipe.hincrby(key, field, 1)
            pipe.zincrby(rank_key, 1, field)
            pipe.incr(total_key)
            pipe.expire(key, FAIR_SHARE_WINDOW_SECONDS)
            pipe.expire(rank_key, FAIR_SHARE_WINDOW_SECONDS)
            pipe.expire(total_key, FAIR_SHARE_WINDOW_SECONDS)
            await pipe.execute()
        except Exception:
            return

    async def _handle_redis_degraded(self) -> None:
        if self.degraded_mode == "fail_closed":
            raise ServiceUnavailableError(message="Rate limit backend unavailable")

    async def _check_fallback(
        self,
        checks: list[TierFairShareCheck],
        *,
        active_ttl_seconds: int,
    ) -> tuple[TierFairShareDecision, ...]:
        if self.degraded_mode == "fail_closed":
            raise ServiceUnavailableError(message="Rate limit backend unavailable")

        decisions: list[TierFairShareDecision] = []
        now = time.time()
        expires_at = now + max(1, int(active_ttl_seconds))
        window_id = self._window_id(FAIR_SHARE_WINDOW_SECONDS)
        window_reset_at = int((math.floor(now / FAIR_SHARE_WINDOW_SECONDS) + 1) * FAIR_SHARE_WINDOW_SECONDS)

        async with self._fallback_lock:
            counter_updates: dict[str, tuple[int, int]] = {}
            active_updates: dict[str, dict[str, tuple[float, float]]] = {}
            for check in checks:
                pool_state = self._fallback_pool_state_for_check(
                    check=check,
                    active_updates=active_updates,
                    now=now,
                    expires_at=expires_at,
                )

                decision = self._evaluate_dimension_fallback(
                    check=check,
                    dimension="rpm",
                    capacity=check.rpm_capacity,
                    amount=check.request_amount,
                    window_id=window_id,
                    active_count=pool_state.active_count,
                    total_weight=pool_state.total_weight,
                    effective_weight=pool_state.effective_weight,
                    window_reset_at=window_reset_at,
                    now=int(now),
                    counter_updates=counter_updates,
                )
                if decision.allowed:
                    decision = self._evaluate_dimension_fallback(
                        check=check,
                        dimension="tpm",
                        capacity=check.tpm_capacity,
                        amount=check.token_amount,
                        window_id=window_id,
                        active_count=pool_state.active_count,
                        total_weight=pool_state.total_weight,
                        effective_weight=pool_state.effective_weight,
                        window_reset_at=window_reset_at,
                        now=int(now),
                        counter_updates=counter_updates,
                    )
                decisions.append(decision)
                if not decision.allowed:
                    raise _fair_share_rate_limit_error(decision, now=now)

            for key, (expiry, value) in counter_updates.items():
                if value <= 0:
                    self._fallback_counters.pop(key, None)
                    continue
                self._fallback_counters[key] = (expiry, value)
            for pool_key, active_orgs in active_updates.items():
                if active_orgs:
                    self._fallback_active_orgs[pool_key] = active_orgs
                else:
                    self._fallback_active_orgs.pop(pool_key, None)
        return tuple(decisions)

    def _fallback_pool_state_for_check(
        self,
        *,
        check: TierFairShareCheck,
        active_updates: dict[str, dict[str, tuple[float, float]]],
        now: float,
        expires_at: float,
    ) -> _FallbackPoolState:
        active_key = f"{check.pool_key}:{check.callable_key}"
        active = active_updates.get(active_key)
        if active is None:
            active = dict(self._fallback_active_orgs.get(active_key, {}))
            active_updates[active_key] = active
        for organization_id, (expiry, _weight) in list(active.items()):
            if expiry <= now:
                active.pop(organization_id, None)

        effective_weight = float(max(1, int(check.assignment_weight or 1)))
        active[check.organization_id] = (expires_at, effective_weight)
        total_weight = sum(max(1.0, weight) for _expiry, weight in active.values())
        return _FallbackPoolState(
            active_orgs=active,
            active_count=len(active),
            total_weight=total_weight,
            effective_weight=effective_weight,
        )

    def _evaluate_dimension_fallback(
        self,
        *,
        check: TierFairShareCheck,
        dimension: Literal["rpm", "tpm"],
        capacity: int | None,
        amount: int,
        window_id: int,
        active_count: int,
        total_weight: float,
        effective_weight: float,
        window_reset_at: int,
        now: int,
        counter_updates: dict[str, tuple[int, int]],
    ) -> TierFairShareDecision:
        scope = f"tier_pool_fair_share_{dimension}"
        if capacity is None or capacity <= 0 or amount <= 0:
            return TierFairShareDecision(
                allowed=True,
                pool_key=check.pool_key,
                callable_key=check.callable_key,
                organization_id=check.organization_id,
                tier_key=check.tier_key,
                scope=scope,
                reason="not_configured",
                dimension=dimension,
                active_org_count=active_count,
                total_weight=total_weight,
                effective_weight=effective_weight,
                pool_current=0,
                org_current=0,
                pool_limit=0,
                share_limit=0,
                saturation=0.0,
            )

        pool_key = fair_share_pool_counter_key(
            dimension=dimension,
            pool_key=check.pool_key,
            callable_key=check.callable_key,
            window_id=window_id,
        )
        org_key = fair_share_org_counter_key(
            dimension=dimension,
            pool_key=check.pool_key,
            callable_key=check.callable_key,
            organization_id=check.organization_id,
            window_id=window_id,
        )
        pool_expiry, pool_current = self._fallback_counter_state(
            counter_updates=counter_updates,
            key=pool_key,
            window_reset_at=window_reset_at,
            now=now,
        )
        org_expiry, org_current = self._fallback_counter_state(
            counter_updates=counter_updates,
            key=org_key,
            window_reset_at=window_reset_at,
            now=now,
        )

        next_pool = pool_current + amount
        saturation = next_pool / capacity if capacity > 0 else 0.0
        share_multiplier = (
            normalized_burst_multiplier(check.burst_multiplier)
            if str(check.strategy or "").strip().lower() == "reserved_burst"
            else 1.0
        )
        share_limit = max(1, math.floor((capacity * effective_weight * share_multiplier) / max(1.0, total_weight)))
        share_limit = min(capacity, share_limit)
        allowed = True
        reason = "allowed"
        if next_pool > capacity:
            allowed = False
            reason = "pool_capacity_exceeded"
        elif (
            saturation > normalized_saturation_threshold(check.saturation_threshold)
            and org_current + amount > share_limit
        ):
            allowed = False
            reason = "weighted_share_exceeded"

        decision = TierFairShareDecision(
            allowed=allowed,
            pool_key=check.pool_key,
            callable_key=check.callable_key,
            organization_id=check.organization_id,
            tier_key=check.tier_key,
            scope=scope,
            reason=reason,
            dimension=dimension,
            active_org_count=active_count,
            total_weight=total_weight,
            effective_weight=effective_weight,
            pool_current=pool_current,
            org_current=org_current,
            pool_limit=capacity,
            share_limit=share_limit,
            saturation=saturation,
        )
        if allowed:
            counter_updates[pool_key] = (pool_expiry, next_pool)
            counter_updates[org_key] = (org_expiry, org_current + amount)
        return decision

    def _fallback_counter_state(
        self,
        *,
        counter_updates: dict[str, tuple[int, int]],
        key: str,
        window_reset_at: int,
        now: int,
    ) -> tuple[int, int]:
        expiry, current = counter_updates.get(
            key,
            self._fallback_counters.get(key, (window_reset_at, 0)),
        )
        if expiry <= now:
            expiry, current = window_reset_at, 0
        counter_updates[key] = (expiry, current)
        return expiry, current


def _fair_share_script_keys(check: TierFairShareCheck, *, window_id: int) -> list[str]:
    return [
        fair_share_active_key(check.pool_key, check.callable_key),
        fair_share_weight_key(check.pool_key, check.callable_key),
        fair_share_active_count_key(check.pool_key, check.callable_key),
        fair_share_total_weight_key(check.pool_key, check.callable_key),
        fair_share_pool_counter_key(
            dimension="rpm",
            pool_key=check.pool_key,
            callable_key=check.callable_key,
            window_id=window_id,
        ),
        fair_share_org_counter_key(
            dimension="rpm",
            pool_key=check.pool_key,
            callable_key=check.callable_key,
            organization_id=check.organization_id,
            window_id=window_id,
        ),
        fair_share_pool_counter_key(
            dimension="tpm",
            pool_key=check.pool_key,
            callable_key=check.callable_key,
            window_id=window_id,
        ),
        fair_share_org_counter_key(
            dimension="tpm",
            pool_key=check.pool_key,
            callable_key=check.callable_key,
            organization_id=check.organization_id,
            window_id=window_id,
        ),
        fair_share_boost_key(
            pool_key=check.pool_key,
            callable_key=check.callable_key,
            organization_id=check.organization_id,
        ),
        fair_share_usage_rank_key(
            pool_key=check.pool_key,
            callable_key=check.callable_key,
            window_id=window_id,
        ),
        fair_share_cleanup_lag_key(check.pool_key, check.callable_key),
        fair_share_limit_hit_heatmap_key(window_id),
        fair_share_limit_hit_heatmap_rank_key(window_id),
        fair_share_limit_hit_total_key(window_id),
    ]


def fair_share_script_keys(check: TierFairShareCheck, *, window_id: int) -> list[str]:
    return _fair_share_script_keys(check, window_id=window_id)


def _fair_share_script_args(check: TierFairShareCheck, *, now: float, ttl_seconds: int) -> list[str]:
    return [
        str(int(now * 1000)),
        str(ttl_seconds * 1000),
        check.organization_id,
        str(max(1, int(check.assignment_weight or 1)) * FAIR_SHARE_WEIGHT_SCALE),
        str(normalized_saturation_threshold(check.saturation_threshold)),
        str(normalized_burst_multiplier(check.burst_multiplier)),
        str(int(check.rpm_capacity or 0)),
        str(max(0, int(check.request_amount or 0))),
        str(int(check.tpm_capacity or 0)),
        str(max(0, int(check.token_amount or 0))),
        str(FAIR_SHARE_WINDOW_SECONDS),
        str(check.strategy or "weighted_fair").strip().lower(),
        str(FAIR_SHARE_ACTIVE_CLEANUP_LIMIT),
        "|".join(
            [
                check.pool_key,
                check.callable_key,
                check.organization_id,
                "",
            ]
        ),
        check.tier_key or "none",
    ]


def fair_share_script_args(check: TierFairShareCheck, *, now: float, ttl_seconds: int) -> list[str]:
    return _fair_share_script_args(check, now=now, ttl_seconds=ttl_seconds)


def _fair_share_rate_limit_error(decision: TierFairShareDecision, *, now: float) -> RateLimitError:
    retry_after = FAIR_SHARE_WINDOW_SECONDS - int(now % FAIR_SHARE_WINDOW_SECONDS)
    exc = RateLimitError(
        message=f"Rate limit exceeded for scope '{decision.scope}'",
        param=decision.scope,
        code=f"{decision.scope}_exceeded",
        retry_after=retry_after,
    )
    setattr(exc, "tier_fair_share_decision", decision)
    return exc


def fair_share_rate_limit_error(decision: TierFairShareDecision, *, now: float) -> RateLimitError:
    return _fair_share_rate_limit_error(decision, now=now)


def _fair_share_decision_from_raw(check: TierFairShareCheck, raw: Any) -> TierFairShareDecision:
    values = list(raw) if isinstance(raw, (list, tuple)) else []
    ok = int(_raw_at(values, 0, 1))
    scope = str(_raw_at(values, 1, "allowed"))
    reason = str(_raw_at(values, 2, "allowed"))
    active_count = int(float(_raw_at(values, 3, 1)))
    total_weight = float(_raw_at(values, 4, max(1, check.assignment_weight)))
    effective_weight = float(_raw_at(values, 5, max(1, check.assignment_weight)))
    pool_current = int(float(_raw_at(values, 6, 0)))
    org_current = int(float(_raw_at(values, 7, 0)))
    pool_limit = int(float(_raw_at(values, 8, 0)))
    share_limit = int(float(_raw_at(values, 9, 0)))
    saturation = float(_raw_at(values, 10, 0.0))
    dimension = str(_raw_at(values, 11, "all"))
    return TierFairShareDecision(
        allowed=ok == 1,
        pool_key=check.pool_key,
        callable_key=check.callable_key,
        organization_id=check.organization_id,
        tier_key=check.tier_key,
        scope=scope,
        reason=reason,
        dimension=dimension,
        active_org_count=active_count,
        total_weight=total_weight,
        effective_weight=effective_weight,
        pool_current=pool_current,
        org_current=org_current,
        pool_limit=pool_limit,
        share_limit=share_limit,
        saturation=saturation,
    )


def fair_share_decision_from_raw(check: TierFairShareCheck, raw: Any) -> TierFairShareDecision:
    return _fair_share_decision_from_raw(check, raw)


def _raw_at(values: list[Any], index: int, default: Any) -> Any:
    if index >= len(values):
        return default
    return values[index]
