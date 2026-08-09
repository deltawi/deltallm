from __future__ import annotations

import asyncio
import logging

import pytest

from src.batch.models import BatchWebhookDeliveryStatus, BatchWebhookQueueSummary
from src.batch.webhooks.observability import (
    BatchWebhookObservabilityWorker,
    BatchWebhookObservabilityWorkerConfig,
    publish_webhook_queue_summary,
)


class _Repository:
    def __init__(self) -> None:
        self.calls = 0
        self.error: Exception | None = None

    async def summarize_webhook_outbox(self) -> BatchWebhookQueueSummary:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return BatchWebhookQueueSummary(
            counts={status: 0 for status in BatchWebhookDeliveryStatus},
            oldest_pending_age_seconds=0.0,
        )


@pytest.mark.asyncio
async def test_webhook_observability_worker_publishes_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _Repository()
    published: list[BatchWebhookQueueSummary] = []
    monkeypatch.setattr(
        "src.batch.webhooks.observability.publish_webhook_queue_summary",
        published.append,
    )
    worker = BatchWebhookObservabilityWorker(
        repository=repository,  # type: ignore[arg-type]
        config=BatchWebhookObservabilityWorkerConfig(),
    )

    await worker.process_once()

    assert repository.calls == 1
    assert len(published) == 1


def test_webhook_queue_summary_publishes_due_depth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    due_depths: list[int] = []
    monkeypatch.setattr(
        "src.batch.webhooks.observability.set_batch_webhook_queue_depth",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        "src.batch.webhooks.observability.set_batch_webhook_oldest_pending_age",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        "src.batch.webhooks.observability.set_batch_webhook_due_depth",
        lambda *, count: due_depths.append(count),
    )

    publish_webhook_queue_summary(
        BatchWebhookQueueSummary(
            counts={status: 0 for status in BatchWebhookDeliveryStatus},
            oldest_pending_age_seconds=0.0,
            due_count=3,
        )
    )

    assert due_depths == [3]


@pytest.mark.asyncio
async def test_webhook_observability_worker_survives_refresh_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    repository = _Repository()
    repository.error = RuntimeError("database unavailable")
    worker = BatchWebhookObservabilityWorker(
        repository=repository,  # type: ignore[arg-type]
        config=BatchWebhookObservabilityWorkerConfig(
            refresh_interval_seconds=60,
            failure_interval_seconds=1,
        ),
    )

    with caplog.at_level(logging.DEBUG, logger="src.batch.webhooks.observability"):
        task = asyncio.create_task(worker.run())
        while repository.calls == 0:
            await asyncio.sleep(0)
        worker.stop()
        await task

    assert "batch webhook observability refresh failed" in caplog.text
    assert "database unavailable" not in caplog.text
