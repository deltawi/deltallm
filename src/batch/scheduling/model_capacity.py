from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Mapping

from src.batch.backpressure import BatchBackpressureCoordinator, BatchModelGroupDeferral
from src.batch.models import BatchModelBacklogRecord, BatchModelInFlightRecord
from src.metrics import (
    clear_batch_model_capacity_metrics,
    increment_batch_model_capacity_snapshot_failure,
    increment_batch_scheduler_model_claim,
    increment_batch_scheduler_model_skip,
    observe_batch_scheduler_model_selection_latency,
    publish_batch_model_capacity_snapshot,
    set_batch_model_backlog_work_units,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class BatchModelCapacityConfig:
    enabled: bool = False
    default_model_max_in_flight: int = 16
    default_model_max_claim_work_units: int = 64
    capacity_fraction: float = 0.25
    refresh_seconds: float = 5.0
    fail_open: bool = False

    @classmethod
    def from_settings(cls, settings: Any) -> "BatchModelCapacityConfig":
        return cls(
            enabled=bool(getattr(settings, "embeddings_batch_model_capacity_enabled", False)),
            default_model_max_in_flight=max(
                1,
                int(getattr(settings, "embeddings_batch_default_model_max_in_flight", 16) or 16),
            ),
            default_model_max_claim_work_units=max(
                1,
                int(getattr(settings, "embeddings_batch_default_model_max_claim_work_units", 64) or 64),
            ),
            capacity_fraction=min(
                1.0,
                max(0.0001, float(getattr(settings, "embeddings_batch_model_capacity_fraction", 0.25) or 0.25)),
            ),
            refresh_seconds=max(
                0.1,
                float(getattr(settings, "embeddings_batch_model_capacity_refresh_seconds", 5.0) or 5.0),
            ),
            fail_open=bool(getattr(settings, "embeddings_batch_model_capacity_fail_open", False)),
        )


@dataclass(slots=True)
class BatchModelCapacitySnapshot:
    model_group: str
    service_tier: str
    max_in_flight_items: int
    max_claim_work_units: int
    available_in_flight_items: int
    available_work_units: int
    rpm_remaining: int | None
    tpm_remaining: int | None
    healthy_deployments: int
    backpressure_until: datetime | None
    reason: str | None
    capacity_source: str
    queued_jobs: int = 0
    queued_work_units: int = 0
    in_flight_items: int = 0
    in_flight_work_units: int = 0
    oldest_queue_entered_at: datetime | None = None
    last_selected_at: datetime | None = None
    skip_reasons: dict[str, int] = field(default_factory=dict)

    @property
    def eligible(self) -> bool:
        return (
            self.reason is None
            and self.available_in_flight_items > 0
            and self.available_work_units > 0
            and self.queued_jobs > 0
        )


@dataclass(frozen=True, slots=True)
class BatchModelCapacitySelection:
    snapshot: BatchModelCapacitySnapshot
    max_items: int
    max_work_units: int


@dataclass(slots=True)
class _BacklogAggregate:
    model_group: str
    service_tier: str
    queued_jobs: int = 0
    queued_work_units: int = 0
    oldest_queue_entered_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class _CapacityLimits:
    max_in_flight_items: int
    max_claim_work_units: int
    rpm_remaining: int | None
    tpm_remaining: int | None
    healthy_deployments: int
    source: str
    reason: str | None = None


class BatchModelCapacityResolver:
    def __init__(
        self,
        *,
        repository: Any,
        config: BatchModelCapacityConfig,
        router: Any | None = None,
        router_state_backend: Any | None = None,
        backpressure: BatchBackpressureCoordinator | None = None,
    ) -> None:
        self.repository = repository
        self.config = config
        self.router = router
        self.router_state_backend = router_state_backend
        self.backpressure = backpressure
        self._last_selected_at: dict[tuple[str, str], datetime] = {}
        self._skip_reasons: dict[tuple[str, str], dict[str, int]] = {}
        self._snapshot_cache: list[BatchModelCapacitySnapshot] | None = None
        self._snapshot_cache_expires_at = 0.0

    async def select_model_group(
        self,
        *,
        max_items: int,
        max_work_units: int,
    ) -> BatchModelCapacitySelection | None:
        selections = await self.select_model_groups(max_items=max_items, max_work_units=max_work_units)
        selection = selections[0] if selections else None
        if selection is not None:
            self.record_selection(selection.snapshot)
        return selection

    async def select_model_groups(
        self,
        *,
        max_items: int,
        max_work_units: int,
    ) -> list[BatchModelCapacitySelection]:
        started = time.perf_counter()
        try:
            snapshots = await self.build_snapshots(force_refresh=True)
            eligible = [snapshot for snapshot in snapshots if snapshot.eligible]
            for snapshot in snapshots:
                if snapshot.eligible:
                    continue
                reason = snapshot.reason or "no_available_capacity"
                self._record_skip(snapshot, reason)
            if not eligible:
                return []
            ordered = sorted(
                eligible,
                key=lambda snapshot: (
                    snapshot.oldest_queue_entered_at or datetime.max.replace(tzinfo=UTC),
                    snapshot.model_group,
                    snapshot.service_tier,
                ),
            )
            return [
                BatchModelCapacitySelection(
                    snapshot=snapshot,
                    max_items=max(1, min(int(max_items), snapshot.available_in_flight_items)),
                    max_work_units=max(1, min(int(max_work_units), snapshot.available_work_units)),
                )
                for snapshot in ordered
            ]
        finally:
            observe_batch_scheduler_model_selection_latency(
                latency_seconds=time.perf_counter() - started,
            )

    def record_selection(self, snapshot: BatchModelCapacitySnapshot) -> None:
        selected_at = datetime.now(tz=UTC)
        self._last_selected_at[(snapshot.model_group, snapshot.service_tier)] = selected_at
        snapshot.last_selected_at = selected_at

    async def build_snapshots(self, *, force_refresh: bool = False) -> list[BatchModelCapacitySnapshot]:
        now = time.monotonic()
        if (
            not force_refresh
            and self._snapshot_cache is not None
            and now < self._snapshot_cache_expires_at
        ):
            return self._snapshot_cache

        clear_batch_model_capacity_metrics()
        backlog_rows = await self.repository.list_model_group_backlog()
        in_flight_rows = await self.repository.list_model_group_in_flight()
        backlog = self._aggregate_backlog(backlog_rows)
        in_flight = {
            (row.model_group, row.service_tier): row
            for row in in_flight_rows
            if row.model_group and row.service_tier
        }
        snapshots: list[BatchModelCapacitySnapshot] = []
        for key, aggregate in sorted(
            backlog.items(),
            key=lambda item: (
                item[1].oldest_queue_entered_at or datetime.max.replace(tzinfo=UTC),
                item[0][0],
                item[0][1],
            ),
        ):
            in_flight_record = in_flight.get(key)
            snapshot = await self._snapshot_for(
                aggregate=aggregate,
                in_flight=in_flight_record,
            )
            snapshot.last_selected_at = self._last_selected_at.get((snapshot.model_group, snapshot.service_tier))
            snapshot.skip_reasons = dict(self._skip_reasons.get((snapshot.model_group, snapshot.service_tier), {}))
            snapshots.append(snapshot)
            publish_batch_model_capacity_snapshot(snapshot)
        self._snapshot_cache = snapshots
        self._snapshot_cache_expires_at = now + max(0.1, float(self.config.refresh_seconds))
        return snapshots

    def record_claim_result(self, *, model_group: str, result: str) -> None:
        increment_batch_scheduler_model_claim(model_group=model_group, result=result)

    def _record_skip(self, snapshot: BatchModelCapacitySnapshot, reason: str) -> None:
        key = (snapshot.model_group, snapshot.service_tier)
        reasons = self._skip_reasons.setdefault(key, {})
        reasons[reason] = reasons.get(reason, 0) + 1
        snapshot.skip_reasons = dict(reasons)
        increment_batch_scheduler_model_skip(model_group=snapshot.model_group, reason=reason)

    def _aggregate_backlog(
        self,
        rows: list[BatchModelBacklogRecord],
    ) -> dict[tuple[str, str], _BacklogAggregate]:
        aggregates: dict[tuple[str, str], _BacklogAggregate] = {}
        for row in rows:
            model_group = str(row.model_group or "").strip()
            service_tier = str(row.service_tier or "standard").strip() or "standard"
            if not model_group:
                continue
            set_batch_model_backlog_work_units(
                model_group=model_group,
                service_tier=service_tier,
                size_class=str(row.size_class or "unknown"),
                work_units=row.queued_work_units,
            )
            key = (model_group, service_tier)
            aggregate = aggregates.setdefault(
                key,
                _BacklogAggregate(model_group=model_group, service_tier=service_tier),
            )
            aggregate.queued_jobs += max(0, int(row.queued_jobs))
            aggregate.queued_work_units += max(0, int(row.queued_work_units))
            if row.oldest_queue_entered_at is not None and (
                aggregate.oldest_queue_entered_at is None
                or row.oldest_queue_entered_at < aggregate.oldest_queue_entered_at
            ):
                aggregate.oldest_queue_entered_at = row.oldest_queue_entered_at
        return aggregates

    async def _snapshot_for(
        self,
        *,
        aggregate: _BacklogAggregate,
        in_flight: BatchModelInFlightRecord | None,
    ) -> BatchModelCapacitySnapshot:
        deferral = await self._get_deferral(aggregate.model_group)
        if deferral is not None:
            until = datetime.fromtimestamp(deferral.until_epoch_seconds, tz=UTC)
            return BatchModelCapacitySnapshot(
                model_group=aggregate.model_group,
                service_tier=aggregate.service_tier,
                max_in_flight_items=0,
                max_claim_work_units=0,
                available_in_flight_items=0,
                available_work_units=0,
                rpm_remaining=None,
                tpm_remaining=None,
                healthy_deployments=0,
                backpressure_until=until,
                reason=deferral.reason or "backpressure",
                capacity_source="backpressure",
                queued_jobs=aggregate.queued_jobs,
                queued_work_units=aggregate.queued_work_units,
                in_flight_items=max(0, int(getattr(in_flight, "in_flight_items", 0) or 0)),
                in_flight_work_units=max(0, int(getattr(in_flight, "in_flight_work_units", 0) or 0)),
                oldest_queue_entered_at=aggregate.oldest_queue_entered_at,
            )

        limits = await self._resolve_limits(aggregate.model_group)
        in_flight_items = max(0, int(getattr(in_flight, "in_flight_items", 0) or 0))
        in_flight_work_units = max(0, int(getattr(in_flight, "in_flight_work_units", 0) or 0))
        available_slots = max(0, limits.max_in_flight_items - in_flight_items)
        available_work_units = 0
        reason = limits.reason
        if reason is None:
            if limits.rpm_remaining is not None:
                if limits.rpm_remaining <= 0:
                    available_slots = 0
                    reason = "rpm_exhausted"
                else:
                    available_slots = min(available_slots, limits.rpm_remaining)
            if available_slots <= 0:
                reason = reason or "no_available_slots"
            else:
                available_work_units = min(
                    max(0, aggregate.queued_work_units),
                    max(0, limits.max_claim_work_units),
                )
                if limits.tpm_remaining is not None:
                    if limits.tpm_remaining <= 0:
                        available_slots = 0
                        available_work_units = 0
                        reason = "tpm_exhausted"
                    else:
                        available_work_units = min(available_work_units, limits.tpm_remaining)
                if available_work_units <= 0:
                    reason = reason or "no_available_work_units"

        return BatchModelCapacitySnapshot(
            model_group=aggregate.model_group,
            service_tier=aggregate.service_tier,
            max_in_flight_items=limits.max_in_flight_items,
            max_claim_work_units=limits.max_claim_work_units,
            available_in_flight_items=available_slots,
            available_work_units=available_work_units,
            rpm_remaining=limits.rpm_remaining,
            tpm_remaining=limits.tpm_remaining,
            healthy_deployments=limits.healthy_deployments,
            backpressure_until=None,
            reason=reason,
            capacity_source=limits.source,
            queued_jobs=aggregate.queued_jobs,
            queued_work_units=aggregate.queued_work_units,
            in_flight_items=in_flight_items,
            in_flight_work_units=in_flight_work_units,
            oldest_queue_entered_at=aggregate.oldest_queue_entered_at,
        )

    async def _get_deferral(self, model_group: str) -> BatchModelGroupDeferral | None:
        if self.backpressure is None:
            return None
        try:
            return await self.backpressure.get_model_group_deferral(model_group)
        except Exception:
            logger.warning("batch model capacity backpressure lookup failed", exc_info=True)
            increment_batch_model_capacity_snapshot_failure(reason="backpressure_lookup_failed")
            return None

    async def _resolve_limits(self, model_group: str) -> _CapacityLimits:
        deployments = self._deployments_for_model_group(model_group)
        if not deployments:
            return self._blocked_limits(reason="unknown_model_group")

        healthy = await self._healthy_deployments(deployments)
        if healthy is None:
            return self._blocked_limits(reason="health_state_unavailable")
        if not healthy:
            return _CapacityLimits(
                max_in_flight_items=0,
                max_claim_work_units=0,
                rpm_remaining=None,
                tpm_remaining=None,
                healthy_deployments=0,
                source="router_health",
                reason="no_healthy_deployments",
            )

        max_in_flight, source = self._max_in_flight_from_deployments(healthy)
        max_claim_work_units = self._max_claim_work_units_from_deployments(healthy)
        rpm_remaining, tpm_remaining = await self._remaining_usage(healthy)
        if max_in_flight is None:
            if self.config.fail_open:
                max_in_flight = self.config.default_model_max_in_flight
                source = "default"
            else:
                return _CapacityLimits(
                    max_in_flight_items=0,
                    max_claim_work_units=max_claim_work_units,
                    rpm_remaining=rpm_remaining,
                    tpm_remaining=tpm_remaining,
                    healthy_deployments=len(healthy),
                    source="unknown",
                    reason="unknown_capacity",
                )
        return _CapacityLimits(
            max_in_flight_items=max(1, int(max_in_flight)),
            max_claim_work_units=max(1, int(max_claim_work_units)),
            rpm_remaining=rpm_remaining,
            tpm_remaining=tpm_remaining,
            healthy_deployments=len(healthy),
            source=source,
        )

    def _blocked_limits(self, *, reason: str) -> _CapacityLimits:
        increment_batch_model_capacity_snapshot_failure(reason=reason)
        return _CapacityLimits(
            max_in_flight_items=0,
            max_claim_work_units=self.config.default_model_max_claim_work_units,
            rpm_remaining=None,
            tpm_remaining=None,
            healthy_deployments=0,
            source="unknown",
            reason=reason,
        )

    def _deployments_for_model_group(self, model_group: str) -> list[Any]:
        registry = getattr(self.router, "deployment_registry", None)
        if not isinstance(registry, Mapping):
            return []
        deployments = registry.get(model_group, [])
        return list(deployments or [])

    async def _healthy_deployments(self, deployments: list[Any]) -> list[Any] | None:
        state = self._router_state()
        if state is None:
            increment_batch_model_capacity_snapshot_failure(reason="router_state_unavailable")
            return None
        deployment_ids = [str(getattr(deployment, "deployment_id", "") or "") for deployment in deployments]
        deployment_ids = [deployment_id for deployment_id in deployment_ids if deployment_id]
        try:
            health = await state.get_health_batch(deployment_ids)
            cooldown = await state.get_cooldown_batch(deployment_ids)
        except Exception:
            logger.warning("batch model capacity router state lookup failed", exc_info=True)
            increment_batch_model_capacity_snapshot_failure(reason="router_state_lookup_failed")
            return None
        healthy: list[Any] = []
        for deployment in deployments:
            deployment_id = str(getattr(deployment, "deployment_id", "") or "")
            if not deployment_id:
                continue
            if cooldown.get(deployment_id):
                continue
            deployment_health = health.get(deployment_id, {})
            if deployment_health.get("healthy", "true") == "false":
                continue
            healthy.append(deployment)
        return healthy

    def _router_state(self) -> Any | None:
        return self.router_state_backend or getattr(self.router, "state", None)

    def _max_in_flight_from_deployments(self, deployments: list[Any]) -> tuple[int | None, str]:
        explicit = [
            value
            for deployment in deployments
            if (value := self._batch_capacity_int(deployment, "max_in_flight")) is not None
        ]
        if explicit:
            return sum(explicit), "model_metadata"

        known = [
            value
            for deployment in deployments
            if (value := self._router_max_in_flight(deployment)) is not None
        ]
        if known:
            fraction = self._capacity_fraction(deployments)
            return max(1, math.floor(sum(known) * fraction)), "router_limit"
        return None, "unknown"

    def _max_claim_work_units_from_deployments(self, deployments: list[Any]) -> int:
        explicit = [
            value
            for deployment in deployments
            if (value := self._batch_capacity_int(deployment, "max_claim_work_units")) is not None
        ]
        if explicit:
            return max(1, sum(explicit))

        upstream_inputs = [
            value
            for deployment in deployments
            if (value := self._model_info_int(deployment, "upstream_max_batch_inputs")) is not None
        ]
        if upstream_inputs:
            return max(1, sum(upstream_inputs))

        return self.config.default_model_max_claim_work_units

    def _capacity_fraction(self, deployments: list[Any]) -> float:
        fractions = [
            value
            for deployment in deployments
            if (value := self._batch_capacity_float(deployment, "capacity_fraction")) is not None
        ]
        if fractions:
            return min(1.0, max(0.0001, min(fractions)))
        return self.config.capacity_fraction

    async def _remaining_usage(self, deployments: list[Any]) -> tuple[int | None, int | None]:
        state = self._router_state()
        deployment_ids = [str(getattr(deployment, "deployment_id", "") or "") for deployment in deployments]
        deployment_ids = [deployment_id for deployment_id in deployment_ids if deployment_id]
        if state is None or not deployment_ids:
            return None, None
        try:
            usage = await state.get_usage_batch(deployment_ids)
        except Exception:
            logger.debug("batch model capacity usage lookup failed", exc_info=True)
            increment_batch_model_capacity_snapshot_failure(reason="usage_lookup_failed")
            return None, None

        rpm_limit = 0
        tpm_limit = 0
        rpm_known = False
        tpm_known = False
        rpm_used = 0
        tpm_used = 0
        for deployment in deployments:
            deployment_id = str(getattr(deployment, "deployment_id", "") or "")
            deployment_usage = usage.get(deployment_id, {})
            rpm = getattr(deployment, "rpm_limit", None)
            tpm = getattr(deployment, "tpm_limit", None)
            if rpm is not None:
                rpm_known = True
                rpm_limit += max(0, int(rpm))
                rpm_used += max(0, int(deployment_usage.get("rpm") or 0))
            if tpm is not None:
                tpm_known = True
                tpm_limit += max(0, int(tpm))
                tpm_used += max(0, int(deployment_usage.get("tpm") or 0))
        rpm_remaining = max(0, rpm_limit - rpm_used) if rpm_known else None
        tpm_remaining = max(0, tpm_limit - tpm_used) if tpm_known else None
        return rpm_remaining, tpm_remaining

    @staticmethod
    def _model_info(deployment: Any) -> Mapping[str, Any]:
        model_info = getattr(deployment, "model_info", {}) or {}
        return model_info if isinstance(model_info, Mapping) else {}

    def _batch_capacity(self, deployment: Any) -> Mapping[str, Any]:
        batch_capacity = self._model_info(deployment).get("batch_capacity")
        return batch_capacity if isinstance(batch_capacity, Mapping) else {}

    def _batch_capacity_int(self, deployment: Any, key: str) -> int | None:
        return self._positive_int(self._batch_capacity(deployment).get(key))

    def _batch_capacity_float(self, deployment: Any, key: str) -> float | None:
        value = self._batch_capacity(deployment).get(key)
        if value is None:
            return None
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None
        if parsed <= 0:
            return None
        return parsed

    def _model_info_int(self, deployment: Any, key: str) -> int | None:
        return self._positive_int(self._model_info(deployment).get(key))

    def _router_max_in_flight(self, deployment: Any) -> int | None:
        params = getattr(deployment, "deltallm_params", {}) or {}
        if not isinstance(params, Mapping):
            return None
        chat_batching = params.get("chat_batching")
        if isinstance(chat_batching, Mapping):
            return self._positive_int(chat_batching.get("max_in_flight"))
        return None

    @staticmethod
    def _positive_int(value: Any) -> int | None:
        if value is None:
            return None
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed > 0 else None
