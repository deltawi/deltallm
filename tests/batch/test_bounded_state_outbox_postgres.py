"""The batch completion outbox drains across pods (docs/operations/bounded-state.md).

The outbox is a table, not memory, so any batch pod can finish another pod's
row. This checks the part that makes that true under load: rows a pod claimed
and then lost (pod killed mid-delivery) come back once their lease runs out,
the surviving pods deliver every row exactly once, and the dead pod's late
answer is refused.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from src.batch.completion_outbox import (
    BatchCompletionOutboxWorker,
    BatchCompletionOutboxWorkerConfig,
)
from src.batch.models import BatchCompletionOutboxCreate
from src.batch.repository import BatchRepository
from tests import test_batch_db_integration as batch_fixtures

pytestmark = pytest.mark.postgres
batch_db = batch_fixtures.batch_db

ROWS = 120
LEASE_SECONDS = 1


class _Pod(BatchCompletionOutboxWorker):
    """A real claim and delivery loop; delivery itself only marks the row sent."""

    def __init__(self, repository: BatchRepository, name: str, delivered: list[str]) -> None:
        super().__init__(
            app=SimpleNamespace(state=SimpleNamespace()),
            repository=repository,
            config=BatchCompletionOutboxWorkerConfig(
                worker_id=name,
                poll_interval_seconds=0.05,
                max_batch_size=16,
                max_concurrency=4,
                lease_seconds=LEASE_SECONDS,
                heartbeat_interval_seconds=0.2,
            ),
        )
        self._delivered = delivered

    async def _record_durable_success(self, record, payload) -> bool:
        await asyncio.sleep(0.005)
        sent = await self.repository.mark_completion_outbox_sent(
            record.completion_id, worker_id=self.config.worker_id
        )
        if sent:
            self._delivered.append(record.item_id)
        return sent


async def _open_rows(db) -> int:
    rows = await db.query_raw(
        "SELECT COUNT(*)::int AS count FROM deltallm_batch_completion_outbox WHERE status <> 'sent'"
    )
    return int(rows[0]["count"])


async def test_rows_a_dead_pod_claimed_are_delivered_once_by_the_other_pods(batch_db):
    repository = BatchRepository(batch_db)
    await repository.enqueue_completion_outbox_many(
        [
            BatchCompletionOutboxCreate(
                batch_id="bounded-state",
                item_id=f"item-{index:03d}",
                payload_json={"request_id": f"batch:bounded-state:{index}"},
            )
            for index in range(ROWS)
        ]
    )
    # Pod 0 claims a share of the rows and dies before delivering any.
    lost = await repository.claim_completion_outbox_due(
        worker_id="pod-0", lease_seconds=LEASE_SECONDS, limit=30
    )
    assert len(lost) == 30

    delivered: list[str] = []
    pods = [_Pod(BatchRepository(batch_db), f"pod-{index}", delivered) for index in (1, 2)]
    tasks = [asyncio.create_task(pod.run()) for pod in pods]
    try:
        loop = asyncio.get_running_loop()
        until = loop.time() + LEASE_SECONDS + 15
        while await _open_rows(batch_db) and loop.time() < until:
            await asyncio.sleep(0.1)
        assert await _open_rows(batch_db) == 0
    finally:
        for pod in pods:
            pod.stop()
        await asyncio.gather(*tasks)

    assert sorted(delivered) == sorted(f"item-{index:03d}" for index in range(ROWS))
    assert await repository.count_pending_completion_outbox() == 0
    # The dead pod's lease is gone: its late answer changes nothing.
    assert not await repository.mark_completion_outbox_sent(
        lost[0].completion_id, worker_id="pod-0"
    )
