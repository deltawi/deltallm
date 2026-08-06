from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
import logging
from types import SimpleNamespace

import pytest

from src.batch.models import (
    BatchWebhookDeliveryStatus,
    BatchWebhookEventType,
    BatchWebhookOutboxRecord,
)
from src.batch.webhooks.crypto import BatchWebhookCipher
from src.batch.webhooks.delivery import (
    BatchWebhookHTTPResponse,
    BatchWebhookTransportError,
)
from src.batch.webhooks.events import batch_webhook_event_payload_sha256
from src.batch.webhooks.models import parse_batch_webhook_request
from src.batch.webhooks.network_policy import BatchWebhookNetworkPolicy
from src.batch.webhooks.worker import (
    BatchWebhookOutboxWorker,
    BatchWebhookOutboxWorkerConfig,
)


SECRET = "customer-signing-secret-value-123456"
ENCRYPTION_KEY = bytes(range(32))
NOW = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)


def _record(
    *,
    event_id: str = "evt-1",
    attempt_count: int = 1,
    max_attempts: int = 3,
    url: str = "https://customer.example/webhook",
    metadata: dict | None = None,
) -> BatchWebhookOutboxRecord:
    payload = {
        "id": event_id,
        "object": "event",
        "type": "batch.completed",
        "created_at": int(NOW.timestamp()),
        "data": {
            "batch": {
                "id": "batch-1",
                "metadata": metadata or {"customer_job_id": "job-1"},
            }
        },
    }
    config = parse_batch_webhook_request(
        {"url": url, "signing_secret": SECRET},
        allowed_ports=[443],
    )
    return BatchWebhookOutboxRecord(
        event_id=event_id,
        batch_id="batch-1",
        event_type=BatchWebhookEventType.COMPLETED,
        target_config_ciphertext=BatchWebhookCipher(ENCRYPTION_KEY).encrypt(config),
        payload_json=payload,
        payload_sha256=batch_webhook_event_payload_sha256(payload),
        status=BatchWebhookDeliveryStatus.PROCESSING,
        attempt_count=attempt_count,
        max_attempts=max_attempts,
        next_attempt_at=NOW,
        last_status_code=None,
        last_error=None,
        locked_by="worker-1",
        lease_expires_at=NOW,
        created_at=NOW,
        updated_at=NOW,
        delivered_at=None,
    )


class _Repository:
    def __init__(self, records: list[BatchWebhookOutboxRecord]) -> None:
        self.records = list(records)
        self.delivered: list[dict] = []
        self.retrying: list[dict] = []
        self.failed: list[dict] = []
        self.renewed: list[dict] = []
        self.renew_result = True
        self.all_processed = asyncio.Event()

    async def claim_webhook_outbox_due(self, **kwargs):  # noqa: ANN003, ANN201
        limit = int(kwargs["limit"])
        claimed = self.records[:limit]
        del self.records[:limit]
        return claimed

    async def mark_webhook_outbox_delivered(self, **kwargs) -> bool:  # noqa: ANN003
        self.delivered.append(kwargs)
        self._signal_if_done()
        return True

    async def mark_webhook_outbox_retrying(self, **kwargs) -> bool:  # noqa: ANN003
        self.retrying.append(kwargs)
        self._signal_if_done()
        return True

    async def mark_webhook_outbox_failed(self, **kwargs) -> bool:  # noqa: ANN003
        self.failed.append(kwargs)
        self._signal_if_done()
        return True

    async def renew_webhook_outbox_lease(self, **kwargs) -> bool:  # noqa: ANN003
        self.renewed.append(kwargs)
        return self.renew_result

    def _signal_if_done(self) -> None:
        if not self.records:
            self.all_processed.set()


class _Sender:
    def __init__(
        self,
        *,
        status_code: int = 200,
        retry_after: str | None = None,
        delay: float = 0,
        error: str | None = None,
    ) -> None:
        self.status_code = status_code
        self.retry_after = retry_after
        self.delay = delay
        self.error = error
        self.calls: list[dict] = []
        self.active = 0
        self.max_active = 0

    async def send(self, **kwargs) -> BatchWebhookHTTPResponse:  # noqa: ANN003
        self.calls.append(kwargs)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            if self.error:
                raise BatchWebhookTransportError(self.error)
            return BatchWebhookHTTPResponse(
                self.status_code,
                retry_after=self.retry_after,
            )
        finally:
            self.active -= 1


async def _public_resolver(hostname: str, port: int) -> tuple[str, ...]:
    assert hostname
    assert port == 443
    return ("93.184.216.34",)


def _worker(
    repository: _Repository,
    sender: _Sender,
    *,
    concurrency: int = 2,
    lease_seconds: float = 30,
    clock: Callable[[], datetime] | None = None,
) -> BatchWebhookOutboxWorker:
    return BatchWebhookOutboxWorker(
        repository=repository,  # type: ignore[arg-type]
        cipher=BatchWebhookCipher(ENCRYPTION_KEY),
        network_policy=BatchWebhookNetworkPolicy(resolver=_public_resolver),
        sender=sender,  # type: ignore[arg-type]
        config=BatchWebhookOutboxWorkerConfig(
            worker_id="worker-1",
            poll_interval_seconds=0.01,
            max_concurrency=concurrency,
            lease_seconds=lease_seconds,  # type: ignore[arg-type]
            retry_initial_seconds=5,
            retry_max_seconds=60,
        ),
        clock=clock or (lambda: NOW),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [200, 201, 204, 299])
async def test_worker_marks_2xx_delivered(status_code: int) -> None:
    repository = _Repository([_record()])
    sender = _Sender(status_code=status_code)

    assert await _worker(repository, sender).process_once() == 1

    assert repository.delivered[0]["status_code"] == status_code
    assert repository.retrying == []
    assert repository.failed == []
    assert sender.calls[0]["headers"]["X-DeltaLLM-Signature"].startswith("v1=")
    assert sender.calls[0]["headers"]["X-DeltaLLM-Event-Id"] == "evt-1"


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [301, 302, 400, 404, 422])
async def test_worker_marks_redirects_and_nonretryable_4xx_failed(status_code: int) -> None:
    repository = _Repository([_record()])

    await _worker(repository, _Sender(status_code=status_code)).process_once()

    assert repository.failed[0]["status_code"] == status_code
    assert repository.failed[0]["error"] == "http_permanent_status"
    assert repository.retrying == []


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [408, 425, 429, 500, 503])
async def test_worker_schedules_retryable_statuses(status_code: int) -> None:
    repository = _Repository([_record()])

    await _worker(
        repository,
        _Sender(status_code=status_code, retry_after="30"),
    ).process_once()

    assert repository.retrying[0]["status_code"] == status_code
    assert repository.retrying[0]["error"] == "http_retryable_status"
    assert repository.retrying[0]["next_attempt_at"] == NOW + timedelta(seconds=30)


@pytest.mark.asyncio
async def test_worker_marks_retryable_final_attempt_failed() -> None:
    repository = _Repository([_record(attempt_count=3, max_attempts=3)])

    await _worker(repository, _Sender(status_code=503)).process_once()

    assert repository.retrying == []
    assert repository.failed[0]["error"] == "max_attempts_exhausted"


@pytest.mark.asyncio
async def test_worker_retries_normalized_transport_error() -> None:
    repository = _Repository([_record()])

    await _worker(repository, _Sender(error="connect_timeout")).process_once()

    assert repository.retrying[0]["status_code"] is None
    assert repository.retrying[0]["error"] == "connect_timeout"


@pytest.mark.asyncio
async def test_worker_keeps_event_id_and_body_stable_across_retries() -> None:
    sender = _Sender()
    times = iter((NOW, NOW + timedelta(seconds=1)))
    first_repository = _Repository([_record(attempt_count=1)])
    second_repository = _Repository([_record(attempt_count=2)])

    await _worker(first_repository, sender, clock=lambda: next(times)).process_once()
    await _worker(second_repository, sender, clock=lambda: next(times)).process_once()

    first, second = sender.calls
    assert first["raw_body"] == second["raw_body"]
    assert first["headers"]["X-DeltaLLM-Event-Id"] == "evt-1"
    assert second["headers"]["X-DeltaLLM-Event-Id"] == "evt-1"
    assert first["headers"]["X-DeltaLLM-Webhook-Attempt"] == "1"
    assert second["headers"]["X-DeltaLLM-Webhook-Attempt"] == "2"
    assert first["headers"]["X-DeltaLLM-Timestamp"] != second["headers"]["X-DeltaLLM-Timestamp"]
    assert first["headers"]["X-DeltaLLM-Signature"] != second["headers"]["X-DeltaLLM-Signature"]


@pytest.mark.asyncio
async def test_worker_abandons_outcome_after_heartbeat_loses_fence() -> None:
    repository = _Repository([_record()])
    repository.renew_result = False
    sender = _Sender(delay=0.2)

    await _worker(repository, sender, lease_seconds=0.3).process_once()

    assert repository.renewed
    assert repository.delivered == []
    assert repository.retrying == []
    assert repository.failed == []


@pytest.mark.asyncio
async def test_worker_refills_capacity_without_exceeding_bound() -> None:
    records = [_record(event_id=f"evt-{index}") for index in range(5)]
    repository = _Repository(records)
    sender = _Sender(delay=0.02)
    worker = _worker(repository, sender, concurrency=2)
    task = asyncio.create_task(worker.run())

    await asyncio.wait_for(repository.all_processed.wait(), timeout=2)
    worker.stop()
    await asyncio.wait_for(task, timeout=2)

    assert len(repository.delivered) == 5
    assert sender.max_active == 2


@pytest.mark.asyncio
async def test_worker_stop_drains_an_in_flight_attempt() -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    class _BlockingSender(_Sender):
        async def send(self, **kwargs) -> BatchWebhookHTTPResponse:  # noqa: ANN003
            self.calls.append(kwargs)
            started.set()
            await release.wait()
            return BatchWebhookHTTPResponse(200)

    repository = _Repository([_record()])
    worker = _worker(repository, _BlockingSender())
    task = asyncio.create_task(worker.run())

    await asyncio.wait_for(started.wait(), timeout=1)
    worker.stop()
    await asyncio.sleep(0)
    assert task.done() is False

    release.set()
    await asyncio.wait_for(task, timeout=1)
    assert len(repository.delivered) == 1


@pytest.mark.asyncio
async def test_worker_cancellation_cancels_and_clears_active_attempts() -> None:
    started = asyncio.Event()
    cancelled = asyncio.Event()

    class _CancellationAwareSender(_Sender):
        async def send(self, **kwargs) -> BatchWebhookHTTPResponse:  # noqa: ANN003
            self.calls.append(kwargs)
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise
            raise AssertionError("unreachable")

    repository = _Repository([_record()])
    worker = _worker(repository, _CancellationAwareSender())
    task = asyncio.create_task(worker.run())

    await asyncio.wait_for(started.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1)

    assert cancelled.is_set()
    assert worker._active_tasks == set()
    assert repository.delivered == []
    assert repository.retrying == []
    assert repository.failed == []


@pytest.mark.asyncio
async def test_worker_logs_never_include_sensitive_delivery_material(
    caplog: pytest.LogCaptureFixture,
) -> None:
    url = "https://sensitive.example/private-customer-path"
    metadata_value = "private-metadata-value"
    record = _record(url=url, metadata={"private": metadata_value})
    repository = _Repository([record])
    sender = _Sender(error="connect_error")

    with caplog.at_level(logging.INFO, logger="src.batch.webhooks.worker"):
        await _worker(repository, sender).process_once()

    captured = caplog.text
    assert "evt-1" in captured
    for sensitive in (
        url,
        SECRET,
        metadata_value,
        "X-DeltaLLM-Signature",
        record.target_config_ciphertext,
        str(record.payload_json),
    ):
        assert sensitive not in captured


def test_worker_dependencies_do_not_repr_sensitive_targets() -> None:
    record = _record()
    assert SECRET not in repr(record)
    assert "customer.example" not in repr(SimpleNamespace(record=record))
