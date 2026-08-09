from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from src.batch.models import BatchWebhookOutboxRecord, BatchWebhookQueueSummary
from src.batch.repository import BatchRepository
from src.metrics import (
    increment_batch_webhook_delivery_attempt,
    increment_batch_webhook_lease_recovery,
    increment_batch_webhook_permanent_failure,
    increment_batch_webhook_retry_scheduled,
    observe_batch_webhook_delivery_latency,
    observe_batch_webhook_event_age,
    set_batch_webhook_due_depth,
    set_batch_webhook_oldest_pending_age,
    set_batch_webhook_queue_depth,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class BatchWebhookObservabilityWorkerConfig:
    refresh_interval_seconds: float = 15.0
    failure_interval_seconds: float = 5.0


class BatchWebhookObservabilityWorker:
    """Publish cluster-wide webhook queue gauges independently of delivery."""

    def __init__(
        self,
        *,
        repository: BatchRepository,
        config: BatchWebhookObservabilityWorkerConfig,
    ) -> None:
        self.repository = repository
        self.config = config
        self._stop_event = asyncio.Event()

    def stop(self) -> None:
        self._stop_event.set()

    async def process_once(self) -> None:
        summary = await self.repository.summarize_webhook_outbox()
        publish_webhook_queue_summary(summary)

    async def run(self) -> None:
        while not self._stop_event.is_set():
            failed = False
            try:
                await self.process_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                failed = True
                logger.debug(
                    "batch webhook observability refresh failed",
                    extra={"reason": "repository_error"},
                )
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=max(
                        1.0,
                        float(
                            self.config.failure_interval_seconds
                            if failed
                            else self.config.refresh_interval_seconds
                        ),
                    ),
                )
            except asyncio.TimeoutError:
                continue

_BOUNDED_REASONS = frozenset(
    {
        "connect_error",
        "connect_timeout",
        "delivered",
        "dns_resolution_empty",
        "dns_resolution_failed",
        "dns_resolution_invalid",
        "dns_resolution_timeout",
        "encrypted_configuration_invalid",
        "hostname_invalid",
        "http_not_allowed",
        "http_permanent_status",
        "http_retryable_status",
        "internal_error",
        "lease_lost",
        "max_attempts_exhausted",
        "max_attempts_exhausted_after_lease_expiry",
        "payload_integrity_failed",
        "pool_timeout",
        "port_not_allowed",
        "protocol_error",
        "read_error",
        "read_timeout",
        "request_timeout",
        "repository_error",
        "resolved_address_not_allowed",
        "scheme_not_allowed",
        "transport_error",
        "write_error",
        "write_timeout",
    }
)


def bounded_webhook_reason(reason: str) -> str:
    normalized = str(reason or "").strip().lower()
    return normalized if normalized in _BOUNDED_REASONS else "other"


def webhook_status_class(status_code: int | None) -> str:
    if status_code is None:
        return "none"
    value = int(status_code)
    return f"{value // 100}xx" if 100 <= value <= 599 else "other"


def publish_webhook_queue_summary(summary: BatchWebhookQueueSummary) -> None:
    for status, count in summary.counts.items():
        set_batch_webhook_queue_depth(status=status.value, count=count)
    set_batch_webhook_due_depth(count=summary.due_count)
    set_batch_webhook_oldest_pending_age(age_seconds=summary.oldest_pending_age_seconds)


def observe_webhook_lease_recovery(record: BatchWebhookOutboxRecord) -> None:
    if record.recovered_from_expired_lease:
        increment_batch_webhook_lease_recovery()


def observe_webhook_attempt(
    record: BatchWebhookOutboxRecord,
    *,
    outcome: str,
    reason: str,
    status_code: int | None,
    latency_seconds: float,
    now: datetime,
) -> None:
    bounded_outcome = (
        outcome
        if outcome in {"delivered", "retrying", "failed", "lease_lost"}
        else "internal_error"
    )
    bounded_reason = bounded_webhook_reason(reason)
    increment_batch_webhook_delivery_attempt(
        outcome=bounded_outcome,
        status_class=webhook_status_class(status_code),
    )
    observe_batch_webhook_delivery_latency(
        outcome=bounded_outcome,
        latency_seconds=latency_seconds,
    )
    if bounded_outcome == "retrying":
        increment_batch_webhook_retry_scheduled(reason=bounded_reason)
    elif bounded_outcome == "failed":
        increment_batch_webhook_permanent_failure(reason=bounded_reason)
    if bounded_outcome in {"delivered", "failed"}:
        current = now if now.tzinfo is not None else now.replace(tzinfo=UTC)
        created = (
            record.created_at
            if record.created_at.tzinfo is not None
            else record.created_at.replace(tzinfo=UTC)
        )
        observe_batch_webhook_event_age(
            outcome=bounded_outcome,
            age_seconds=max(0.0, (current - created).total_seconds()),
        )


def observe_webhook_exhausted_lease(
    record: BatchWebhookOutboxRecord,
    *,
    now: datetime,
) -> None:
    reason = bounded_webhook_reason(
        record.last_error or "max_attempts_exhausted_after_lease_expiry"
    )
    increment_batch_webhook_lease_recovery()
    increment_batch_webhook_delivery_attempt(
        outcome="failed",
        status_class="none",
    )
    increment_batch_webhook_permanent_failure(reason=reason)
    current = now if now.tzinfo is not None else now.replace(tzinfo=UTC)
    created = (
        record.created_at
        if record.created_at.tzinfo is not None
        else record.created_at.replace(tzinfo=UTC)
    )
    observe_batch_webhook_event_age(
        outcome="failed",
        age_seconds=max(0.0, (current - created).total_seconds()),
    )
