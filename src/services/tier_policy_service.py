from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from src.db.tiers import TierPolicyLoadResult
from src.services.tier_policy_compiler import compile_tier_policy_snapshot
from src.services.tier_policy_models import (
    CompiledTierCapacityPoolPolicy,
    CompiledTierModelPolicy,
    CompiledTierPricingPolicy,
    CompiledTierRateLimitDescriptor,
    TierPolicySnapshot,
    empty_tier_policy_snapshot,
)

logger = logging.getLogger(__name__)

TierPolicyMode = Literal["disabled", "shadow", "enforce"]
TierPolicyMissingServiceMode = Literal["fail_open", "fail_closed"]


class TierPolicyBackendUnavailableError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class TierPolicySnapshotInfo:
    etag: str
    generated_at: datetime
    org_count: int
    assignment_count: int
    model_policy_count: int
    capacity_pool_count: int
    next_transition_at: datetime | None
    mode: str
    snapshot_stale: bool
    last_reload_failed: bool
    last_reload_error_at: datetime | None


@dataclass(frozen=True, slots=True)
class TierPolicyAvailabilityDecision:
    allowed: bool
    reason: str
    mode: str
    missing_service_mode: str
    explicit_tier_policy: bool | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "mode": self.mode,
            "missing_service_mode": self.missing_service_mode,
            "explicit_tier_policy": self.explicit_tier_policy,
        }


class TierPolicyService:
    def __init__(
        self,
        *,
        repository: Any | None,
        mode: TierPolicyMode = "disabled",
        missing_service_mode: TierPolicyMissingServiceMode = "fail_open",
        refresh_interval_seconds: float = 300.0,
        refresh_jitter_seconds: float = 1.0,
        transition_grace_seconds: float = 0.05,
        refresh_retry_delay_seconds: float = 5.0,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.repository = repository
        self.mode = _normalize_mode(mode)
        self.missing_service_mode = _normalize_missing_service_mode(missing_service_mode)
        self.refresh_interval_seconds = _positive_float(
            refresh_interval_seconds,
            default=300.0,
        )
        self.refresh_jitter_seconds = _non_negative_float(
            refresh_jitter_seconds,
            default=1.0,
        )
        self.transition_grace_seconds = _non_negative_float(
            transition_grace_seconds,
            default=0.05,
        )
        self.refresh_retry_delay_seconds = _positive_float(
            refresh_retry_delay_seconds,
            default=5.0,
        )
        self._clock = clock or (lambda: datetime.now(tz=UTC))
        self._reload_lock = asyncio.Lock()
        self._refresh_wakeup = asyncio.Event()
        self._refresh_task: asyncio.Task[None] | None = None
        self._stopping = False
        self._retry_after: datetime | None = None
        self._snapshot_stale = self.mode != "disabled"
        self._last_reload_failed = False
        self._last_reload_error_at: datetime | None = None
        self._snapshot = empty_tier_policy_snapshot(generated_at=self._clock())

    async def start(self) -> None:
        if self.mode == "disabled":
            return
        if self._refresh_task is not None and not self._refresh_task.done():
            return
        self._stopping = False
        self._refresh_task = asyncio.create_task(self._refresh_loop())

    async def close(self) -> None:
        self._stopping = True
        self._refresh_wakeup.set()
        if self._refresh_task is None:
            return
        self._refresh_task.cancel()
        try:
            await self._refresh_task
        except asyncio.CancelledError:
            pass
        self._refresh_task = None

    async def reload(self, *, reason: str = "manual") -> TierPolicySnapshot:
        async with self._reload_lock:
            if self.mode == "disabled":
                self._snapshot = empty_tier_policy_snapshot(generated_at=self._clock())
                self._retry_after = None
                self._snapshot_stale = False
                self._last_reload_failed = False
                self._last_reload_error_at = None
                self._wake_refresh_loop()
                return self._snapshot

            generated_at = self._clock()
            if self.repository is None:
                self._mark_reload_failure()
                raise TierPolicyBackendUnavailableError("tier policy repository unavailable")

            loader = getattr(self.repository, "load_active_tier_policy_inputs", None)
            if loader is None:
                self._mark_reload_failure()
                raise TierPolicyBackendUnavailableError("tier policy repository loader unavailable")

            try:
                inputs = await loader(reference_time=generated_at)
                if not isinstance(inputs, TierPolicyLoadResult):
                    inputs = TierPolicyLoadResult(
                        assignments=tuple(getattr(inputs, "assignments", ()) or ()),
                        model_policies=tuple(getattr(inputs, "model_policies", ()) or ()),
                        capacity_pools=tuple(getattr(inputs, "capacity_pools", ()) or ()),
                        next_transition_at=getattr(inputs, "next_transition_at", None),
                    )
                snapshot = compile_tier_policy_snapshot(
                    inputs,
                    generated_at=generated_at,
                    reference_time=generated_at,
                )
            except Exception:
                self._mark_reload_failure()
                logger.exception("failed reloading tier policy snapshot")
                raise

            self._snapshot = snapshot
            self._retry_after = None
            self._snapshot_stale = False
            self._last_reload_failed = False
            self._last_reload_error_at = None
            self._wake_refresh_loop()
            logger.info(
                "tier policy snapshot reloaded",
                extra={
                    "reason": reason,
                    "etag": snapshot.etag,
                    "org_count": snapshot.org_count,
                    "assignment_count": snapshot.assignment_count,
                    "model_policy_count": snapshot.model_policy_count,
                    "capacity_pool_count": snapshot.capacity_pool_count,
                    "next_transition_at": (
                        snapshot.next_transition_at.isoformat()
                        if snapshot.next_transition_at is not None
                        else None
                    ),
                },
            )
            return snapshot

    async def invalidate_all(self) -> TierPolicySnapshot:
        return await self.reload(reason="invalidation")

    def get_snapshot(self) -> TierPolicySnapshot:
        return self._snapshot

    def snapshot_info(self) -> TierPolicySnapshotInfo:
        snapshot = self._snapshot
        return TierPolicySnapshotInfo(
            etag=snapshot.etag,
            generated_at=snapshot.generated_at,
            org_count=snapshot.org_count,
            assignment_count=snapshot.assignment_count,
            model_policy_count=snapshot.model_policy_count,
            capacity_pool_count=snapshot.capacity_pool_count,
            next_transition_at=snapshot.next_transition_at,
            mode=self.mode,
            snapshot_stale=self._snapshot_stale,
            last_reload_failed=self._last_reload_failed,
            last_reload_error_at=self._last_reload_error_at,
        )

    @property
    def snapshot_stale(self) -> bool:
        return self._snapshot_stale

    @property
    def last_reload_failed(self) -> bool:
        return self._last_reload_failed

    @property
    def last_reload_error_at(self) -> datetime | None:
        return self._last_reload_error_at

    def has_explicit_tier_policy(self, organization_id: str | None) -> bool:
        normalized = _normalize_id(organization_id)
        return bool(normalized and normalized in self._snapshot.org_has_explicit_tier_policy)

    def resolve_unavailable_decision(
        self,
        organization_id: str | None,
    ) -> TierPolicyAvailabilityDecision:
        return resolve_tier_policy_unavailable_decision(
            self,
            organization_id,
            mode=self.mode,
            missing_service_mode=self.missing_service_mode,
        )

    def resolve_org_allowed_callable_keys(
        self,
        organization_id: str | None,
    ) -> frozenset[str] | None:
        normalized = _normalize_id(organization_id)
        if not normalized:
            return None
        if normalized not in self._snapshot.org_has_explicit_tier_policy:
            return None
        return self._snapshot.org_allowed_callable_keys.get(normalized, frozenset())

    def get_model_policy(
        self,
        organization_id: str | None,
        callable_key: str | None,
    ) -> CompiledTierModelPolicy | None:
        key = _org_model_key(organization_id, callable_key)
        return self._snapshot.org_model_policy.get(key) if key is not None else None

    def get_pricing_policy(
        self,
        organization_id: str | None,
        callable_key: str | None,
        *,
        mode: str = "sync",
    ) -> CompiledTierPricingPolicy | None:
        key = _org_model_key(organization_id, callable_key)
        if key is None:
            return None
        return self._snapshot.pricing_policies.get((*key, str(mode or "sync").strip().lower()))

    def get_rate_limit_descriptors(
        self,
        organization_id: str | None,
        callable_key: str | None,
    ) -> tuple[CompiledTierRateLimitDescriptor, ...]:
        key = _org_model_key(organization_id, callable_key)
        if key is None:
            return ()
        return self._snapshot.rate_limit_descriptors.get(key, ())

    def get_capacity_pool_policy(
        self,
        pool_key: str | None,
        callable_key: str | None,
    ) -> CompiledTierCapacityPoolPolicy | None:
        normalized_pool_key = _normalize_id(pool_key)
        normalized_callable_key = _normalize_id(callable_key)
        if normalized_pool_key is None or normalized_callable_key is None:
            return None
        return self._snapshot.capacity_pool_policy.get(
            (normalized_pool_key, normalized_callable_key)
        )

    async def _refresh_loop(self) -> None:
        while not self._stopping:
            delay_seconds = self._next_refresh_delay_seconds()
            woke = await self._wait_for_wakeup(delay_seconds)
            if woke or self._stopping:
                continue
            try:
                await self.reload(reason="scheduled")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("scheduled tier policy snapshot reload failed: %s", exc)

    async def _wait_for_wakeup(self, delay_seconds: float) -> bool:
        if self._refresh_wakeup.is_set():
            self._refresh_wakeup.clear()
            return True
        if delay_seconds <= 0:
            await asyncio.sleep(0)
            return False
        try:
            await asyncio.wait_for(self._refresh_wakeup.wait(), timeout=delay_seconds)
        except TimeoutError:
            return False
        self._refresh_wakeup.clear()
        return True

    def _next_refresh_delay_seconds(self) -> float:
        now = _utc(self._clock())
        if self._retry_after is not None:
            return max((_utc(self._retry_after) - now).total_seconds(), 0.0)

        delay_seconds = self.refresh_interval_seconds
        transition_at = self._snapshot.next_transition_at
        if transition_at is not None:
            seconds_until_transition = (_utc(transition_at) - now).total_seconds()
            delay_seconds = min(
                max(seconds_until_transition + self.transition_grace_seconds, 0.0),
                self.refresh_interval_seconds,
            )
        if self.refresh_jitter_seconds > 0:
            delay_seconds += random.uniform(0.0, self.refresh_jitter_seconds)
        return delay_seconds

    def _wake_refresh_loop(self) -> None:
        current_task = asyncio.current_task()
        if self._refresh_task is not None and current_task is not self._refresh_task:
            self._refresh_wakeup.set()

    def _schedule_reload_retry(self, reference_time: datetime) -> None:
        self._retry_after = _utc(reference_time) + timedelta(
            seconds=self.refresh_retry_delay_seconds
        )
        self._wake_refresh_loop()

    def _mark_reload_failure(self) -> None:
        failed_at = _utc(self._clock())
        self._snapshot_stale = True
        self._last_reload_failed = True
        self._last_reload_error_at = failed_at
        self._schedule_reload_retry(failed_at)


def _org_model_key(
    organization_id: str | None,
    callable_key: str | None,
) -> tuple[str, str] | None:
    normalized_org = _normalize_id(organization_id)
    normalized_callable = _normalize_id(callable_key)
    if normalized_org is None or normalized_callable is None:
        return None
    return normalized_org, normalized_callable


def _normalize_id(value: str | None) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None


def _positive_float(value: float, *, default: float) -> float:
    try:
        normalized = float(value)
    except (TypeError, ValueError):
        return default
    return normalized if normalized > 0 else default


def _non_negative_float(value: float, *, default: float) -> float:
    try:
        normalized = float(value)
    except (TypeError, ValueError):
        return default
    return normalized if normalized >= 0 else default


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _normalize_mode(value: str) -> TierPolicyMode:
    normalized = str(value or "disabled").strip().lower()
    if normalized not in {"disabled", "shadow", "enforce"}:
        return "disabled"
    return normalized  # type: ignore[return-value]


def _normalize_missing_service_mode(value: str) -> TierPolicyMissingServiceMode:
    normalized = str(value or "fail_open").strip().lower()
    if normalized not in {"fail_open", "fail_closed"}:
        return "fail_open"
    return normalized  # type: ignore[return-value]


def resolve_tier_policy_unavailable_decision(
    service: TierPolicyService | None,
    organization_id: str | None,
    *,
    mode: str = "disabled",
    missing_service_mode: str = "fail_open",
) -> TierPolicyAvailabilityDecision:
    resolved_mode = _normalize_mode(str(getattr(service, "mode", mode) if service else mode))
    resolved_missing_service_mode = _normalize_missing_service_mode(
        str(
            getattr(service, "missing_service_mode", missing_service_mode)
            if service
            else missing_service_mode
        )
    )
    if resolved_mode == "disabled":
        return TierPolicyAvailabilityDecision(
            allowed=True,
            reason="tier_policy_disabled",
            mode=resolved_mode,
            missing_service_mode=resolved_missing_service_mode,
            explicit_tier_policy=False if service is not None else None,
        )
    if resolved_missing_service_mode == "fail_open":
        return TierPolicyAvailabilityDecision(
            allowed=True,
            reason="tier_policy_unavailable_fail_open",
            mode=resolved_mode,
            missing_service_mode=resolved_missing_service_mode,
            explicit_tier_policy=(
                service.has_explicit_tier_policy(organization_id) if service is not None else None
            ),
        )

    explicit_tier_policy = (
        service.has_explicit_tier_policy(organization_id) if service is not None else None
    )
    if service is not None and service.snapshot_stale:
        return TierPolicyAvailabilityDecision(
            allowed=False,
            reason="tier_policy_unavailable_snapshot_stale",
            mode=resolved_mode,
            missing_service_mode=resolved_missing_service_mode,
            explicit_tier_policy=explicit_tier_policy,
        )
    if explicit_tier_policy is False:
        return TierPolicyAvailabilityDecision(
            allowed=True,
            reason="tier_policy_unavailable_no_explicit_policy",
            mode=resolved_mode,
            missing_service_mode=resolved_missing_service_mode,
            explicit_tier_policy=False,
        )

    return TierPolicyAvailabilityDecision(
        allowed=False,
        reason="tier_policy_unavailable_fail_closed",
        mode=resolved_mode,
        missing_service_mode=resolved_missing_service_mode,
        explicit_tier_policy=explicit_tier_policy,
    )
