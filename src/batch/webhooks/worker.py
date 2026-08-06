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
    ) -> None:
        self.repository = repository
        self.cipher = cipher
        self.network_policy = network_policy
        self.sender = sender
        self.config = config
        self.clock = clock or (lambda: datetime.now(tz=UTC))
        self.random_source = random_source
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
                    try:
                        claimed = await self.repository.claim_webhook_outbox_due(
                            worker_id=self.config.worker_id,
                            lease_seconds=self.config.lease_seconds,
                            limit=capacity,
                        )
                    except Exception:
                        logger.warning("batch webhook claim failed reason=repository_error")
                for record in claimed:
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
        claimed = await self.repository.claim_webhook_outbox_due(
            worker_id=self.config.worker_id,
            lease_seconds=self.config.lease_seconds,
            limit=max(1, int(self.config.max_concurrency)),
        )
        await asyncio.gather(*(self._process_record_safely(record) for record in claimed))
        return len(claimed)

    async def _process_record_safely(self, record: BatchWebhookOutboxRecord) -> None:
        try:
            await self._process_record(record)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning(
                "batch webhook attempt failed unexpectedly "
                "event_id=%s batch_id=%s event_type=%s attempt=%s reason=internal_error",
                record.event_id,
                record.batch_id,
                record.event_type.value,
                record.attempt_count,
            )

    async def _process_record(self, record: BatchWebhookOutboxRecord) -> None:
        started_at = time.monotonic()
        lease_lost = asyncio.Event()
        heartbeat_task = asyncio.create_task(self._heartbeat(record, lease_lost))
        try:
            outcome = await self._deliver_once(record)
            if lease_lost.is_set():
                self._log_outcome(
                    record,
                    reason="lease_lost",
                    status_code=outcome.status_code,
                    started_at=started_at,
                )
                return
            updated = await self._record_outcome(record, outcome)
            self._log_outcome(
                record,
                reason=outcome.reason if updated else "lease_lost",
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
        try:
            webhook = self.cipher.decrypt(record.target_config_ciphertext)
        except BatchWebhookCryptoError:
            return _AttemptOutcome("permanent", "encrypted_configuration_invalid")

        try:
            raw_body = batch_webhook_raw_body(record)
        except BatchWebhookPayloadIntegrityError:
            return _AttemptOutcome("permanent", "payload_integrity_failed")

        try:
            target = await self.network_policy.resolve(
                webhook,
                attempt_count=record.attempt_count,
            )
        except BatchWebhookNetworkPolicyError as exc:
            return _AttemptOutcome("permanent", exc.reason)
        except BatchWebhookResolutionError as exc:
            return _AttemptOutcome("retryable", exc.reason)

        now = self._now()
        headers = build_batch_webhook_headers(
            record,
            signing_secret=webhook.signing_secret,
            timestamp=int(now.timestamp()),
            raw_body=raw_body,
        )
        try:
            response = await self.sender.send(
                target=target,
                raw_body=raw_body,
                headers=headers,
            )
        except BatchWebhookTransportError as exc:
            return _AttemptOutcome("retryable", exc.reason)

        if batch_webhook_status_is_success(response.status_code):
            return _AttemptOutcome(
                "delivered",
                "delivered",
                status_code=response.status_code,
            )
        if batch_webhook_status_is_retryable(response.status_code):
            return _AttemptOutcome(
                "retryable",
                "http_retryable_status",
                status_code=response.status_code,
                retry_after=response.retry_after,
            )
        return _AttemptOutcome(
            "permanent",
            "http_permanent_status",
            status_code=response.status_code,
        )

    async def _record_outcome(
        self,
        record: BatchWebhookOutboxRecord,
        outcome: _AttemptOutcome,
    ) -> bool:
        fence = {
            "event_id": record.event_id,
            "worker_id": self.config.worker_id,
            "attempt_count": record.attempt_count,
        }
        if outcome.disposition == "delivered":
            assert outcome.status_code is not None
            return await self.repository.mark_webhook_outbox_delivered(
                **fence,
                status_code=outcome.status_code,
            )

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
            return await self.repository.mark_webhook_outbox_retrying(
                **fence,
                status_code=outcome.status_code,
                error=outcome.reason,
                next_attempt_at=now + timedelta(seconds=delay),
            )

        reason = "max_attempts_exhausted" if outcome.disposition == "retryable" else outcome.reason
        return await self.repository.mark_webhook_outbox_failed(
            **fence,
            status_code=outcome.status_code,
            error=reason,
        )

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
        status_class = f"{status_code // 100}xx" if status_code is not None else "none"
        duration_ms = max(0, round((time.monotonic() - started_at) * 1_000))
        logger.info(
            "batch webhook attempt finished event_id=%s batch_id=%s event_type=%s "
            "attempt=%s status_class=%s duration_ms=%s reason=%s",
            record.event_id,
            record.batch_id,
            record.event_type.value,
            record.attempt_count,
            status_class,
            duration_ms,
            reason,
        )
