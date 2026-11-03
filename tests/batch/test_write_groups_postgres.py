"""Grouped batch writes keep every fence of the one-item statements."""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from src.batch.accounting_checkpoint import BatchAccountingUnavailable, BatchAccountingWrite
from src.batch.models import BatchItemCreate
from src.batch.repositories.accounting_repository import BatchAccountingRepository
from src.batch.repository import BatchRepository
from src.batch.selector_checkpoint import BatchSelectorClaim
from tests import test_batch_db_integration as batch_fixtures
from src.metrics.batch import deltallm_batch_write_group_size_metric
from tests.batch.accounting_fixtures import checkpoint_for

pytestmark = pytest.mark.postgres
batch_db = batch_fixtures.batch_db


def _deadline() -> float:
    return asyncio.get_running_loop().time() + 2


@pytest.fixture
async def claimed_items(batch_db):
    repository = BatchRepository(batch_db)
    file_id = await batch_fixtures._seed_batch_file(repository)
    job = await repository.create_job(
        endpoint="/v1/chat/completions",
        input_file_id=file_id,
        model="m",
        metadata=None,
        created_by_api_key="key-a",
        created_by_user_id=None,
        created_by_team_id=None,
        status="in_progress",
        total_items=4,
    )
    await repository.create_items(
        job.batch_id,
        [
            BatchItemCreate(
                line_number=line,
                custom_id=f"c{line}",
                request_body={"model": "m", "messages": []},
            )
            for line in range(1, 5)
        ],
    )
    items = await repository.claim_items(batch_id=job.batch_id, worker_id="worker-a", limit=4)
    claims = [
        BatchSelectorClaim(job.batch_id, item.item_id, "key-a", "worker-a", item.claim_epoch)
        for item in sorted(items, key=lambda item: item.item_id)
    ]
    return repository, claims


def _statements(kind: str) -> float:
    for family in deltallm_batch_write_group_size_metric.collect():
        for sample in family.samples:
            if sample.name.endswith("_count") and sample.labels == {"kind": kind}:
                return sample.value
    return 0.0


async def _stored(batch_db, item_id: str):
    (row,) = await batch_db.query_raw(
        "SELECT accounting_checkpoint FROM deltallm_batch_item WHERE item_id=$1", item_id
    )
    return row["accounting_checkpoint"]


async def test_grouped_proofs_save_together_and_a_bad_fence_only_fails_its_own(
    batch_db, claimed_items
):
    _, claims = claimed_items
    store = BatchAccountingRepository(batch_db, max_concurrent_writes=1, group_waiting_writes=True)
    proofs = [checkpoint_for(claim) for claim in claims]
    writes = [
        BatchAccountingWrite(claim, None, proof)
        for claim, proof in zip(claims, proofs, strict=True)
    ]
    # The third write claims another worker's item: its fence must fail alone.
    writes[2] = BatchAccountingWrite(replace(claims[2], worker_id="worker-b"), None, proofs[2])
    statements = _statements("accounting_checkpoint")
    results = await asyncio.gather(
        *(store.write_many([write], expires_at=_deadline()) for write in writes),
        return_exceptions=True,
    )
    assert results[0] is None and results[1] is None and results[3] is None
    assert isinstance(results[2], BatchAccountingUnavailable)
    # One alone, one rejected group of three, then those three alone.
    assert _statements("accounting_checkpoint") == statements + 5
    for index in (0, 1, 3):
        assert await _stored(batch_db, claims[index].item_id) == proofs[index].model_dump(
            mode="json"
        )
    assert await _stored(batch_db, claims[2].item_id) is None


async def test_a_grouped_statement_saves_every_proof_or_none(batch_db, claimed_items):
    _, claims = claimed_items
    store = BatchAccountingRepository(batch_db)
    batches = []
    for claim in claims[:3]:
        write = BatchAccountingWrite(claim, None, checkpoint_for(claim))
        batches.append(store._batch([write], expires_at=_deadline()))
    # A proof whose claim epoch is not the item's: the whole statement is rejected.
    stale = BatchAccountingWrite(claims[3], None, checkpoint_for(claims[3]))
    stale_batch = store._batch([stale], expires_at=_deadline())
    await batch_db.execute_raw(
        "UPDATE deltallm_batch_item SET claim_epoch=claim_epoch+1 WHERE item_id=$1",
        claims[3].item_id,
    )
    assert await store._write_group([*batches, stale_batch]) is False
    for claim in claims:
        assert await _stored(batch_db, claim.item_id) is None
    assert await store._write_group(batches) is True
    for claim in claims[:3]:
        assert await _stored(batch_db, claim.item_id) is not None


def _result_row(claim: BatchSelectorClaim, *, claim_epoch: int | None = None) -> dict:
    return {
        "item_id": claim.item_id,
        "claim_epoch": claim.claim_epoch if claim_epoch is None else claim_epoch,
        "response_body": {"id": claim.item_id},
        "usage": {"total_tokens": 1},
        "provider_cost": 0.0,
        "billed_cost": 0.0,
        "outbox_payload": {"batch_id": claim.batch_id, "item_id": claim.item_id},
        "outbox_max_attempts": 5,
    }


async def test_a_grouped_result_save_completes_every_item_or_none(claimed_items):
    repository, claims = claimed_items
    rows = [_result_row(claim) for claim in claims[:3]]
    stale = _result_row(claims[3], claim_epoch=99)
    result = await repository.complete_items_with_outbox_bulk(
        items=[*rows, stale], worker_id="worker-a", count_rejections=False
    )
    assert result == "not_owned"
    items = await repository.list_items_by_ids([claim.item_id for claim in claims])
    assert {item.status for item in items} == {"in_progress"}
    assert await repository.list_completion_outbox_by_item_ids([c.item_id for c in claims]) == []

    assert (
        await repository.complete_items_with_outbox_bulk(
            items=rows, worker_id="worker-a", count_rejections=False
        )
        == "completed"
    )
    saved = await repository.list_items_by_ids([claim.item_id for claim in claims[:3]])
    assert {item.status for item in saved} == {"completed"}
    outbox = await repository.list_completion_outbox_by_item_ids([c.item_id for c in claims[:3]])
    assert len(outbox) == 3
