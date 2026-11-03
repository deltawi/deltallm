"""Grouped native sent marks keep every per-row fence of the one-row statement."""

from __future__ import annotations

import asyncio

import pytest

from src.batch.completion_sent_marks import GroupedSentMarks
from src.batch.models import BatchCompletionOutboxCreate
from src.batch.repository import BatchRepository
from src.metrics.batch import deltallm_batch_write_group_size_metric
from tests import test_batch_db_integration as batch_fixtures

pytestmark = pytest.mark.postgres
batch_db = batch_fixtures.batch_db


@pytest.fixture
async def claimed(batch_db):
    repository = BatchRepository(batch_db)
    await repository.enqueue_completion_outbox_many(
        [
            BatchCompletionOutboxCreate(
                batch_id="b1",
                item_id=f"i{index}",
                payload_json={"request_id": f"batch:b1:i{index}", "item_id": f"i{index}"},
            )
            for index in range(4)
        ]
    )
    rows = await repository.claim_completion_outbox_due(worker_id="w1", lease_seconds=30, limit=10)
    assert len(rows) == 4
    return repository, sorted(rows, key=lambda row: row.item_id)


async def _statuses(batch_db) -> dict[str, str]:
    rows = await batch_db.query_raw(
        "SELECT item_id, status FROM deltallm_batch_completion_outbox ORDER BY item_id"
    )
    return {str(row["item_id"]): str(row["status"]) for row in rows}


def _statements(kind: str) -> float:
    for family in deltallm_batch_write_group_size_metric.collect():
        for sample in family.samples:
            if sample.name.endswith("_count") and sample.labels == {"kind": kind}:
                return sample.value
    return 0.0


async def test_one_statement_marks_only_the_rows_whose_own_fence_holds(batch_db, claimed):
    repository, rows = claimed
    # i3's lease has run out: its own fence fails, the others still pass.
    await batch_db.execute_raw(
        "UPDATE deltallm_batch_completion_outbox "
        "SET lease_expires_at = NOW() - INTERVAL '1 second' WHERE item_id = 'i3'"
    )
    marks = [(row.completion_id, row.attempt_count) for row in rows]
    # i2 names an attempt that is not the row's current one.
    marks[2] = (rows[2].completion_id, rows[2].attempt_count + 1)

    marked = await repository.mark_completion_outbox_sent_many(marks, worker_id="w1")

    assert marked == {
        (rows[0].completion_id, rows[0].attempt_count),
        (rows[1].completion_id, rows[1].attempt_count),
    }
    assert await _statuses(batch_db) == {
        "i0": "sent",
        "i1": "sent",
        "i2": "processing",
        "i3": "processing",
    }


async def test_another_owner_marks_nothing(batch_db, claimed):
    repository, rows = claimed
    marks = [(row.completion_id, row.attempt_count) for row in rows]
    assert await repository.mark_completion_outbox_sent_many(marks, worker_id="w2") == frozenset()
    assert set((await _statuses(batch_db)).values()) == {"processing"}


async def test_marks_that_wait_for_the_slot_share_one_statement(batch_db, claimed):
    repository, rows = claimed
    marks = GroupedSentMarks(repository, limit=1)
    statements = _statements("outbox_sent")

    results = await asyncio.gather(
        *(
            marks.mark_completion_outbox_sent(
                row.completion_id, worker_id="w1", attempt_count=row.attempt_count
            )
            for row in rows
        )
    )

    assert results == [True] * 4
    assert set((await _statuses(batch_db)).values()) == {"sent"}
    # The first mark went alone; the three that waited went out together.
    assert _statements("outbox_sent") == statements + 2
