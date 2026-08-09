from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from src.batch.models import BatchWebhookOutboxRecord
from src.batch.repository import BatchRepository
from src.batch.webhooks.crypto import BatchWebhookCipher, BatchWebhookCryptoError
from src.batch.webhooks.delivery import (
    BatchWebhookHTTPSender,
    BatchWebhookTransportError,
)
from src.batch.webhooks.network_policy import (
    BatchWebhookNetworkPolicy,
    BatchWebhookNetworkPolicyError,
    BatchWebhookResolutionError,
)
from src.batch.webhooks.observability import (
    bounded_webhook_reason,
    observe_webhook_attempt,
    observe_webhook_exhausted_lease,
    observe_webhook_lease_recovery,
    webhook_status_class,
)
from src.batch.webhooks.retry import (
    batch_webhook_status_is_retryable,
    batch_webhook_status_is_success,
    calculate_batch_webhook_retry_delay,
)
from src.batch.webhooks.signing import (
    BatchWebhookPayloadIntegrityError,
    batch_webhook_raw_body,
    build_batch_webhook_headers,
)
from src.batch.webhooks.terminalization import BatchWebhookTerminalRecorder
from src.services.audit_service import AuditService

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class BatchWebhookOutboxWorkerConfig:
    worker_id: str
    poll_interval_seconds: float = 1.0
    max_concurrency: int = 4
    lease_seconds: int = 30
    retry_initial_seconds: int = 5
    retry_max_seconds: int = 3_600


@dataclass(frozen=True, slots=True)
class _AttemptOutcome:
    disposition: Literal["delivered", "retryable", "permanent"]
    reason: str
    status_code: int | None = None
    retry_after: str | None = None
    observed_at: datetime | None = None


class BatchWebhookOutboxWorker:
    def __init__(
        self,
        *,
        repository: BatchRepository,
        cipher: BatchWebhookCipher,
        network_policy: BatchWebhookNetworkPolicy,
        sender: BatchWebhookHTTPSender,
        config: BatchWebhookOutboxWorkerConfig,
        clock: Callable[[], datetime] | None = None,
        random_source: random.Random | None = None,
        audit_service: AuditService | None = None,
    ) -> None:
        self.repository = repository
        self.cipher = cipher
        self.network_policy = network_policy
        self.sender = sender
        self.config = config
        self.clock = clock or (lambda: datetime.now(tz=UTC))
        self.random_source = random_source
        self.audit_service = audit_service
        self.terminal_recorder = BatchWebhookTerminalRecorder(
            repository=repository,
            audit_service=audit_service,
            worker_id=config.worker_id,
        )
        self._stop_event = asyncio.Event()
        self._active_tasks: set[asyncio.Task[None]] = set()

    def stop(self) -> None:
        self._stop_event.set()

    async def run(self) -> None:
        try:
            while not self._stop_event.is_set():
                capacity = max(0, self.config.max_concurrency - len(self._active_tasks))
                claimed: list[BatchWebhookOutboxRecord] = []
                if capacity:
                    await self._fail_exhausted_leases(limit=capacity)
                    try:
                        claimed = await self.repository.claim_webhook_outbox_due(
                            worker_id=self.config.worker_id,
                            lease_seconds=self.config.lease_seconds,
                            limit=capacity,
                        )
                    except Exception:
                        logger.warning(
                            "batch webhook claim failed",
                            extra={"reason": "repository_error"},
                        )
                for record in claimed:
                    observe_webhook_lease_recovery(record)
                    self._active_tasks.add(asyncio.create_task(self._process_record_safely(record)))
                if self._active_tasks:
                    done, _pending = await asyncio.wait(
                        self._active_tasks,
                        timeout=max(0.01, float(self.config.poll_interval_seconds)),
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for task in done:
                        self._active_tasks.discard(task)
                        task.result()
                    continue

                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=max(0.01, float(self.config.poll_interval_seconds)),
                    )
                except asyncio.TimeoutError:
                    continue
        except BaseException:
            await self._cancel_active_tasks()
            raise
        await self._drain_active_tasks()

    async def _drain_active_tasks(self) -> None:
        tasks = tuple(self._active_tasks)
        if not tasks:
            return
        try:
            await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            self._active_tasks.difference_update(tasks)

    async def _cancel_active_tasks(self) -> None:
        tasks = tuple(self._active_tasks)
        if not tasks:
            return
        for task in tasks:
            task.cancel()
        try:
            await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            self._active_tasks.difference_update(tasks)

    async def process_once(self) -> int:
        await self._fail_exhausted_leases(limit=max(1, int(self.config.max_concurrency)))
        claimed = await self.repository.claim_webhook_outbox_due(
            worker_id=self.config.worker_id,
            lease_seconds=self.config.lease_seconds,
            limit=max(1, int(self.config.max_concurrency)),
        )
        for record in claimed:
            observe_webhook_lease_recovery(record)
        await asyncio.gather(*(self._process_record_safely(record) for record in claimed))
        return len(claimed)

    async def _fail_exhausted_leases(self, *, limit: int) -> None:
        try:
            failed = await self.terminal_recorder.fail_exhausted_leases(limit=limit)
        except Exception:
            logger.warning(
                "batch webhook exhausted lease scan failed",
                extra={"reason": "repository_error"},
            )
            return
        for record in failed:
            reason = record.last_error or "max_attempts_exhausted_after_lease_expiry"
            observe_webhook_exhausted_lease(record, now=self._now())
            logger.info(
                "batch webhook exhausted lease failed",
                extra={
                    "event_id": record.event_id,
                    "batch_id": record.batch_id,
                    "event_type": record.event_type.value,
                    "attempt": record.attempt_count,
                    "status_class": webhook_status_class(record.last_status_code),
                    "reason": bounded_webhook_reason(reason),
                },
            )

    async def _process_record_safely(self, record: BatchWebhookOutboxRecord) -> None:
        started_at = time.monotonic()
        try:
            await self._process_record(record)
        except asyncio.CancelledError:
            raise
        except Exception:
            observe_webhook_attempt(
                record,
                outcome="internal_error",
                reason="internal_error",
                status_code=None,
                latency_seconds=max(0.0, time.monotonic() - started_at),
                now=self._now(),
            )
            logger.warning(
                "batch webhook attempt failed unexpectedly",
                extra={
                    "event_id": record.event_id,
                    "batch_id": record.batch_id,
                    "event_type": record.event_type.value,
                    "attempt": record.attempt_count,
                    "status_class": "none",
                    "reason": "internal_error",
                },
            )

    async def _process_record(self, record: BatchWebhookOutboxRecord) -> None:
        started_at = time.monotonic()
        lease_lost = asyncio.Event()
        heartbeat_task = asyncio.create_task(self._heartbeat(record, lease_lost))
        try:
            outcome = await self._deliver_once(record)
            observed_at = (outcome.observed_at or self._now()) + timedelta(
                seconds=max(0.0, time.monotonic() - started_at)
            )
            if lease_lost.is_set():
                observe_webhook_attempt(
                    record,
                    outcome="lease_lost",
                    reason="lease_lost",
                    status_code=outcome.status_code,
                    latency_seconds=max(0.0, time.monotonic() - started_at),
                    now=observed_at,
                )
                self._log_outcome(
                    record,
                    reason="lease_lost",
                    status_code=outcome.status_code,
                    started_at=started_at,
                )
                return
            persisted = await self._record_outcome(record, outcome)
            persisted_outcome, persisted_reason = persisted or ("lease_lost", "lease_lost")
            observe_webhook_attempt(
                record,
                outcome=persisted_outcome,
                reason=persisted_reason,
                status_code=outcome.status_code,
                latency_seconds=max(0.0, time.monotonic() - started_at),
                now=observed_at,
            )
            self._log_outcome(
                record,
                reason=persisted_reason,
                status_code=outcome.status_code,
                started_at=started_at,
            )
        finally:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass

    async def _deliver_once(self, record: BatchWebhookOutboxRecord) -> _AttemptOutcome:
        attempted_at = self._now()
        try:
            webhook = self.cipher.decrypt(record.target_config_ciphertext)
        except BatchWebhookCryptoError:
            return _AttemptOutcome(
                "permanent",
                "encrypted_configuration_invalid",
                observed_at=attempted_at,
            )

        try:
            raw_body = batch_webhook_raw_body(record)
        except BatchWebhookPayloadIntegrityError:
            return _AttemptOutcome(
                "permanent",
                "payload_integrity_failed",
                observed_at=attempted_at,
            )

        try:
            target = await self.network_policy.resolve(
                webhook,
                attempt_count=record.attempt_count,
            )
        except BatchWebhookNetworkPolicyError as exc:
            return _AttemptOutcome("permanent", exc.reason, observed_at=attempted_at)
        except BatchWebhookResolutionError as exc:
            return _AttemptOutcome("retryable", exc.reason, observed_at=attempted_at)

        headers = build_batch_webhook_headers(
            record,
            signing_secret=webhook.signing_secret,
            timestamp=int(attempted_at.timestamp()),
            raw_body=raw_body,
        )
        try:
            response = await self.sender.send(
                target=target,
                raw_body=raw_body,
                headers=headers,
            )
        except BatchWebhookTransportError as exc:
            return _AttemptOutcome("retryable", exc.reason, observed_at=attempted_at)

        if batch_webhook_status_is_success(response.status_code):
            return _AttemptOutcome(
                "delivered",
                "delivered",
                status_code=response.status_code,
                observed_at=attempted_at,
            )
        if batch_webhook_status_is_retryable(response.status_code):
            return _AttemptOutcome(
                "retryable",
                "http_retryable_status",
                status_code=response.status_code,
                retry_after=response.retry_after,
                observed_at=attempted_at,
            )
        return _AttemptOutcome(
            "permanent",
            "http_permanent_status",
            status_code=response.status_code,
            observed_at=attempted_at,
        )

    async def _record_outcome(
        self,
        record: BatchWebhookOutboxRecord,
        outcome: _AttemptOutcome,
    ) -> tuple[Literal["delivered", "retrying", "failed"], str] | None:
        fence = {
            "event_id": record.event_id,
            "worker_id": self.config.worker_id,
            "attempt_count": record.attempt_count,
        }
        if outcome.disposition == "delivered":
            assert outcome.status_code is not None
            updated = await self.terminal_recorder.mark_delivered(
                record,
                status_code=outcome.status_code,
            )
            return ("delivered", outcome.reason) if updated else None

        if outcome.disposition == "retryable" and record.attempt_count < record.max_attempts:
            now = self._now()
            delay = calculate_batch_webhook_retry_delay(
                attempt_count=record.attempt_count,
                initial_seconds=self.config.retry_initial_seconds,
                maximum_seconds=self.config.retry_max_seconds,
                retry_after=outcome.retry_after,
                now=now,
                random_source=self.random_source,
            )
            updated = await self.repository.mark_webhook_outbox_retrying(
                **fence,
                status_code=outcome.status_code,
                error=outcome.reason,
                next_attempt_at=now + timedelta(seconds=delay),
            )
            return ("retrying", outcome.reason) if updated else None

        reason = "max_attempts_exhausted" if outcome.disposition == "retryable" else outcome.reason
        updated = await self.terminal_recorder.mark_failed(
            record,
            status_code=outcome.status_code,
            reason=reason,
        )
        return ("failed", reason) if updated else None

    async def _heartbeat(
        self,
        record: BatchWebhookOutboxRecord,
        lease_lost: asyncio.Event,
    ) -> None:
        interval = max(0.1, float(self.config.lease_seconds) / 3)
        while True:
            await asyncio.sleep(interval)
            try:
                renewed = await self.repository.renew_webhook_outbox_lease(
                    event_id=record.event_id,
                    worker_id=self.config.worker_id,
                    attempt_count=record.attempt_count,
                    lease_seconds=self.config.lease_seconds,
                )
            except Exception:
                lease_lost.set()
                return
            if not renewed:
                lease_lost.set()
                return

    def _now(self) -> datetime:
        now = self.clock()
        return now if now.tzinfo is not None else now.replace(tzinfo=UTC)

    def _log_outcome(
        self,
        record: BatchWebhookOutboxRecord,
        *,
        reason: str,
        status_code: int | None,
        started_at: float,
    ) -> None:
        status_class = webhook_status_class(status_code)
        duration_ms = max(0, round((time.monotonic() - started_at) * 1_000))
        logger.info(
            "batch webhook attempt finished",
            extra={
                "event_id": record.event_id,
                "batch_id": record.batch_id,
                "event_type": record.event_type.value,
                "attempt": record.attempt_count,
                "status_class": status_class,
                "duration_ms": duration_ms,
                "reason": bounded_webhook_reason(reason),
            },
        )
