from __future__ import annotations

from dataclasses import dataclass, fields, replace
from datetime import datetime
from typing import Any


_TENANT_CAP_REASONS = frozenset({"tenant_in_flight_full"})
_MODEL_CAP_REASONS = frozenset(
    {
        "capacity_full_after_lock",
        "capacity_work_units_full_after_lock",
        "no_available_slots",
        "no_available_work_units",
        "rpm_exhausted",
        "tpm_exhausted",
        "unknown_capacity",
    }
)
_NO_WORKERS_FREE_REASONS = frozenset(
    {
        "health_state_unavailable",
        "no_healthy_deployments",
        "router_state_unavailable",
        "unknown_model_group",
    }
)
_LEASE_WAIT_REASONS = frozenset(
    {
        "all_items_locked",
        "flow_lock_busy",
        "job_terminal_or_leased",
        "lock_busy",
    }
)
_NO_QUEUE_WORK_REASONS = frozenset(
    {
        "empty_after_selection",
        "empty_flow",
        "missing_model_group",
        "no_active_flow",
        "no_available_work",
        "no_pending_items",
        "no_runnable_items_after_selection",
        "repository_unavailable",
        "transaction_unavailable",
    }
)


def claim_decision_reason_category(reason: object) -> str:
    normalized = str(reason or "").strip() or "unknown"
    if normalized in _TENANT_CAP_REASONS:
        return "tenant_cap"
    if normalized in _MODEL_CAP_REASONS:
        return "model_cap"
    if normalized in _NO_WORKERS_FREE_REASONS:
        return "no_workers_free"
    if normalized == "oversized_head_item":
        return "oversized_head_item"
    if normalized == "not_before_future":
        return "deferred_retry"
    if normalized in _LEASE_WAIT_REASONS:
        return "lease_wait"
    if normalized in _NO_QUEUE_WORK_REASONS:
        return "no_queue_work"
    if normalized == "flow_scan_limit_reached":
        return "scheduler_limit"
    if normalized == "insufficient_deficit":
        return "fair_share_deficit"
    return "unknown"


@dataclass(frozen=True, slots=True)
class BatchClaimDecisionDiagnostic:
    reason: str
    reason_category: str | None = None
    diagnostic_source: str | None = None
    diagnostic_probe_suppressed_count: int | None = None
    batch_id: str | None = None
    model_group: str | None = None
    service_tier: str | None = None
    tenant_scope_type: str | None = None
    tenant_scope_id: str | None = None
    head_item_work_units: int | None = None
    max_items: int | None = None
    max_work_units: int | None = None
    capacity_max_in_flight_items: int | None = None
    capacity_max_in_flight_work_units: int | None = None
    available_in_flight_items: int | None = None
    available_work_units: int | None = None
    in_flight_items: int | None = None
    in_flight_work_units: int | None = None
    tenant_max_in_flight_work_units: int | None = None
    tenant_in_flight_work_units: int | None = None
    queued_work_units: int | None = None
    active_flow_count: int | None = None
    scanned_flow_count: int | None = None
    total_in_flight_work_units: int | None = None
    rpm_remaining: int | None = None
    tpm_remaining: int | None = None
    healthy_deployments: int | None = None
    capacity_source: str | None = None
    deferred_until: datetime | None = None
    lease_expires_at: datetime | None = None

    def __post_init__(self) -> None:
        normalized_reason = str(self.reason or "").strip() or "unknown"
        object.__setattr__(self, "reason", normalized_reason)
        if not self.reason_category:
            object.__setattr__(
                self,
                "reason_category",
                claim_decision_reason_category(normalized_reason),
            )

    def with_defaults_from(
        self,
        defaults: BatchClaimDecisionDiagnostic,
    ) -> BatchClaimDecisionDiagnostic:
        values: dict[str, Any] = {}
        for field in fields(self):
            current = getattr(self, field.name)
            fallback = getattr(defaults, field.name)
            values[field.name] = fallback if _is_empty(current) else current
        return BatchClaimDecisionDiagnostic(**values)

    def with_overrides(self, **overrides: Any) -> BatchClaimDecisionDiagnostic:
        if "reason" in overrides and "reason_category" not in overrides:
            overrides["reason_category"] = None
        return replace(self, **overrides)

    def to_log_extra(
        self,
        *,
        decision: str,
        worker_id: str,
        claim_mode: str,
        scheduler_mode: str,
        decision_scope: str = "active",
        suppressed_count: int = 0,
        window_seconds: float | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "event": "batch_work_claim_decision",
            "decision": str(decision or "unknown"),
            "decision_scope": str(decision_scope or "active"),
            "worker_id": str(worker_id or ""),
            "claim_mode": str(claim_mode or ""),
            "scheduler_mode": str(scheduler_mode or ""),
            "reason": self.reason,
            "reason_category": self.reason_category or claim_decision_reason_category(self.reason),
            "suppressed_count": max(0, int(suppressed_count or 0)),
        }
        if window_seconds is not None:
            payload["window_seconds"] = max(0.0, float(window_seconds))
        for field in fields(self):
            if field.name == "reason_category":
                continue
            value = getattr(self, field.name)
            if value is None:
                continue
            payload[field.name] = _log_value(value)
        for key, value in extra.items():
            if value is not None:
                payload[key] = _log_value(value)
        return payload

    def log_key(
        self,
        *,
        decision: str,
        claim_mode: str,
        scheduler_mode: str,
        decision_scope: str = "active",
    ) -> tuple[str, ...]:
        return (
            str(decision or "unknown"),
            str(decision_scope or "active"),
            self.reason,
            str(self.reason_category or claim_decision_reason_category(self.reason)),
            str(claim_mode or ""),
            str(scheduler_mode or ""),
            str(self.model_group or ""),
            str(self.service_tier or ""),
        )


class ClaimDecisionLogLimiter:
    def __init__(self, *, window_seconds: float = 60.0, max_keys: int = 1024) -> None:
        self.window_seconds = max(1.0, float(window_seconds or 60.0))
        self.max_keys = max(1, int(max_keys or 1024))
        self._entries: dict[tuple[str, ...], tuple[float, int]] = {}

    def should_emit(self, key: tuple[str, ...], *, now: float) -> tuple[bool, int]:
        self._cleanup(now)
        started_at, suppressed_count = self._entries.get(key, (now, -1))
        if suppressed_count < 0 or now - started_at >= self.window_seconds:
            self._entries[key] = (now, 0)
            return True, max(0, suppressed_count)
        self._entries[key] = (started_at, suppressed_count + 1)
        return False, 0

    def _cleanup(self, now: float) -> None:
        if len(self._entries) < self.max_keys:
            return
        expired = [
            key
            for key, (started_at, _suppressed_count) in self._entries.items()
            if now - started_at >= self.window_seconds
        ]
        for key in expired:
            self._entries.pop(key, None)
        if len(self._entries) < self.max_keys:
            return
        for key in sorted(self._entries, key=lambda item: self._entries[item][0])[
            : max(1, len(self._entries) - self.max_keys + 1)
        ]:
            self._entries.pop(key, None)


@dataclass(frozen=True, slots=True)
class ClaimDecisionDiagnosticProbeDecision:
    should_probe: bool
    cached_diagnostic: BatchClaimDecisionDiagnostic | None
    suppressed_count: int = 0


@dataclass(slots=True)
class _DiagnosticProbeEntry:
    started_at: float
    suppressed_count: int = 0
    cached_diagnostic: BatchClaimDecisionDiagnostic | None = None
    failure_backoff_seconds: float | None = None


class ClaimDecisionDiagnosticProbeLimiter:
    def __init__(self, *, window_seconds: float = 60.0, max_keys: int = 1024) -> None:
        self.window_seconds = max(1.0, float(window_seconds or 60.0))
        self.max_keys = max(1, int(max_keys or 1024))
        self._entries: dict[tuple[str, ...], _DiagnosticProbeEntry] = {}
        self.failure_backoff_seconds = min(5.0, self.window_seconds)

    def should_probe(
        self,
        key: tuple[str, ...],
        *,
        now: float,
    ) -> ClaimDecisionDiagnosticProbeDecision:
        self._cleanup(now)
        entry = self._entries.get(key)
        if entry is None:
            self._entries[key] = _DiagnosticProbeEntry(started_at=now)
            return ClaimDecisionDiagnosticProbeDecision(
                should_probe=True,
                cached_diagnostic=None,
            )
        probe_interval = entry.failure_backoff_seconds or self.window_seconds
        if now - entry.started_at >= probe_interval:
            suppressed_count = max(0, entry.suppressed_count)
            self._entries[key] = _DiagnosticProbeEntry(
                started_at=now,
                cached_diagnostic=entry.cached_diagnostic,
            )
            return ClaimDecisionDiagnosticProbeDecision(
                should_probe=True,
                cached_diagnostic=entry.cached_diagnostic,
                suppressed_count=suppressed_count,
            )
        entry.suppressed_count += 1
        return ClaimDecisionDiagnosticProbeDecision(
            should_probe=False,
            cached_diagnostic=entry.cached_diagnostic,
            suppressed_count=entry.suppressed_count,
        )

    def record_probe_result(
        self,
        key: tuple[str, ...],
        diagnostic: BatchClaimDecisionDiagnostic,
    ) -> None:
        entry = self._entries.get(key)
        if entry is None:
            entry = _DiagnosticProbeEntry(started_at=0.0)
            self._entries[key] = entry
        entry.cached_diagnostic = self._cacheable_diagnostic(diagnostic)
        entry.failure_backoff_seconds = None

    def record_probe_failure(
        self,
        key: tuple[str, ...],
        *,
        now: float,
    ) -> None:
        entry = self._entries.get(key)
        if entry is None:
            self._entries[key] = _DiagnosticProbeEntry(
                started_at=now,
                failure_backoff_seconds=self.failure_backoff_seconds,
            )
            return
        entry.started_at = now
        entry.failure_backoff_seconds = self.failure_backoff_seconds

    def _cleanup(self, now: float) -> None:
        if len(self._entries) < self.max_keys:
            return
        expired = [
            key
            for key, entry in self._entries.items()
            if now - entry.started_at >= (entry.failure_backoff_seconds or self.window_seconds)
        ]
        for key in expired:
            self._entries.pop(key, None)
        if len(self._entries) < self.max_keys:
            return
        for key in sorted(self._entries, key=lambda item: self._entries[item].started_at)[
            : max(1, len(self._entries) - self.max_keys + 1)
        ]:
            self._entries.pop(key, None)

    @staticmethod
    def _cacheable_diagnostic(
        diagnostic: BatchClaimDecisionDiagnostic,
    ) -> BatchClaimDecisionDiagnostic:
        return BatchClaimDecisionDiagnostic(
            reason=diagnostic.reason,
            reason_category=diagnostic.reason_category,
            model_group=diagnostic.model_group,
            service_tier=diagnostic.service_tier,
        )


def _is_empty(value: Any) -> bool:
    return value is None or value == ""


def _log_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return value
