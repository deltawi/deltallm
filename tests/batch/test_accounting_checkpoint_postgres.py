import asyncio
from dataclasses import replace
import json
from time import perf_counter

import pytest

from src.batch.accounting_checkpoint import BatchAccountingUnavailable, BatchAccountingWrite
from src.batch.repositories.accounting_repository import (
    BatchAccountingRepository,
    WRITE_CHECKPOINTS_SQL,
)
from src.batch.worker_constants import COMPLETION_OUTBOX_MAX_ATTEMPTS
from tests.batch.accounting_fixtures import checkpoint_for, uncertain
from src.batch.accounting_native import terminal_checkpoint
from tests.batch.test_selector_checkpoint_postgres import (
    batch_db as _batch_db,
    claimed_selector as _claimed_selector,
    deadline,
)

pytestmark = pytest.mark.postgres
batch_db = _batch_db
claimed_selector = _claimed_selector


async def test_only_one_worker_can_install_initial_dispatch_fence(batch_db, claimed_selector):
    _, claim, _ = claimed_selector
    checkpoint = checkpoint_for(claim)
    stores = [BatchAccountingRepository(batch_db) for _ in range(2)]
    results = await asyncio.gather(
        *(
            store.write_many([BatchAccountingWrite(claim, None, checkpoint)], expires_at=deadline())
            for store in stores
        ),
        return_exceptions=True,
    )
    assert sum(result is None for result in results) == 1
    assert sum(isinstance(result, BatchAccountingUnavailable) for result in results) == 1


async def test_checkpoint_survives_reclaim_and_terminal_delivery_is_durable(
    batch_db, claimed_selector
):
    repository, claim, _ = claimed_selector
    pending, store = checkpoint_for(claim), BatchAccountingRepository(batch_db)
    await store.write_many([BatchAccountingWrite(claim, None, pending)], expires_at=deadline())
    await batch_db.execute_raw(
        "UPDATE deltallm_batch_item SET lease_expires_at=NOW() WHERE item_id=$1",
        claim.item_id,
    )
    (item,) = await repository.claim_items(batch_id=claim.batch_id, worker_id="new-worker")
    assert item.accounting_checkpoint == pending.model_dump(mode="json")
    current = replace(claim, worker_id="new-worker", claim_epoch=item.claim_epoch)
    with pytest.raises(BatchAccountingUnavailable):
        await store.write_many(
            [BatchAccountingWrite(claim, pending, uncertain(pending))], expires_at=deadline()
        )
    terminal = uncertain(pending, claim_epoch=current.claim_epoch)
    write = BatchAccountingWrite(current, pending, terminal)
    await store.write_many([write], expires_at=deadline())
    await store.write_many([write], expires_at=deadline())
    (record,) = await repository.list_completion_outbox_by_item_ids([claim.item_id])
    assert record.completion_id == str(pending.operation_id)
    assert record.payload_json["native_accounting"] == terminal.model_dump(mode="json")
    (saved,) = await repository.load_claim_items([claim.item_id])
    assert saved.accounting_checkpoint == terminal.model_dump(mode="json")


@pytest.mark.parametrize(
    "field,value",
    [
        ("api_key", "other-tenant"),
        ("batch_id", "other-job"),
        ("item_id", "other-item"),
        ("worker_id", "other-worker"),
        ("claim_epoch", 2),
    ],
)
async def test_batch_native_fence_rejects_other_subject(batch_db, claimed_selector, field, value):
    _, claim, _ = claimed_selector
    with pytest.raises(BatchAccountingUnavailable):
        await BatchAccountingRepository(batch_db).write_many(
            [
                BatchAccountingWrite(replace(claim, **{field: value}), None, checkpoint_for(claim)),
            ],
            expires_at=deadline(),
        )
    (row,) = await batch_db.query_raw(
        "SELECT accounting_checkpoint FROM deltallm_batch_item WHERE item_id=$1",
        claim.item_id,
    )
    assert row["accounting_checkpoint"] is None


@pytest.mark.parametrize("change", ["cancelled", "expired", "lease_lost"])
async def test_new_dispatch_denied_but_owned_terminal_can_drain(batch_db, claimed_selector, change):
    _, claim, _ = claimed_selector
    pending, store = checkpoint_for(claim), BatchAccountingRepository(batch_db)
    await store.write_many([BatchAccountingWrite(claim, None, pending)], expires_at=deadline())
    if change == "lease_lost":
        await batch_db.execute_raw(
            "UPDATE deltallm_batch_item SET lease_expires_at=NOW() WHERE item_id=$1",
            claim.item_id,
        )
    elif change == "cancelled":
        await batch_db.execute_raw(
            "UPDATE deltallm_batch_job SET cancel_requested_at=NOW() WHERE batch_id=$1",
            claim.batch_id,
        )
    else:
        await batch_db.execute_raw(
            "UPDATE deltallm_batch_job SET expires_at=NOW() WHERE batch_id=$1",
            claim.batch_id,
        )
    with pytest.raises(BatchAccountingUnavailable):
        await store.write_many(
            [BatchAccountingWrite(claim, pending, pending)], expires_at=deadline()
        )
    if change == "lease_lost":
        with pytest.raises(BatchAccountingUnavailable):
            await store.write_many(
                [BatchAccountingWrite(claim, pending, uncertain(pending))], expires_at=deadline()
            )
    else:
        await store.write_many(
            [BatchAccountingWrite(claim, pending, uncertain(pending))], expires_at=deadline()
        )


async def test_native_outbox_ack_rejects_expiry_and_old_attempt_for_same_worker(
    batch_db, claimed_selector
):
    repository, claim, _ = claimed_selector
    pending, store = checkpoint_for(claim), BatchAccountingRepository(batch_db)
    await store.write_many([BatchAccountingWrite(claim, None, pending)], expires_at=deadline())
    await store.write_many(
        [BatchAccountingWrite(claim, pending, uncertain(pending))], expires_at=deadline()
    )
    (first,) = await repository.claim_completion_outbox_due(worker_id="delivery", lease_seconds=30)
    await batch_db.execute_raw(
        "UPDATE deltallm_batch_completion_outbox SET lease_expires_at=NOW() WHERE completion_id=$1",
        first.completion_id,
    )
    assert not await store.mark_sent(
        first.completion_id,
        worker_id="delivery",
        attempt_count=first.attempt_count,
        expires_at=deadline(),
    )
    (second,) = await repository.claim_completion_outbox_due(worker_id="delivery", lease_seconds=30)
    assert second.attempt_count > first.attempt_count
    assert not await store.mark_sent(
        first.completion_id,
        worker_id="delivery",
        attempt_count=first.attempt_count,
        expires_at=deadline(),
    )
    assert await store.mark_sent(
        second.completion_id,
        worker_id="delivery",
        attempt_count=second.attempt_count,
        expires_at=deadline(),
    )


async def test_native_checkpoint_lock_wait_is_bounded(batch_db, claimed_selector):
    _, claim, _ = claimed_selector
    store, pending = BatchAccountingRepository(batch_db), checkpoint_for(claim)
    async with batch_db.tx() as tx:
        await tx.query_raw(
            "SELECT item_id FROM deltallm_batch_item WHERE item_id=$1 FOR UPDATE", claim.item_id
        )
        started = perf_counter()
        with pytest.raises(BatchAccountingUnavailable):
            await store.write_many(
                [BatchAccountingWrite(claim, None, pending)], expires_at=deadline()
            )
        assert perf_counter() - started < 1
    await store.write_many([BatchAccountingWrite(claim, None, pending)], expires_at=deadline())


async def test_lost_initial_write_reply_can_release_only_the_same_unsent_proof(
    batch_db, claimed_selector
):
    _, claim, _ = claimed_selector
    store, pending = BatchAccountingRepository(batch_db), checkpoint_for(claim)
    await store.write_many([BatchAccountingWrite(claim, None, pending)], expires_at=deadline())
    terminal = terminal_checkpoint(pending, claim_epoch=claim.claim_epoch, dispatched=False)
    await store.write_many([BatchAccountingWrite(claim, None, terminal)], expires_at=deadline())
    await store.write_many([BatchAccountingWrite(claim, None, terminal)], expires_at=deadline())
    (row,) = await batch_db.query_raw(
        "SELECT accounting_checkpoint FROM deltallm_batch_item WHERE item_id=$1",
        claim.item_id,
    )
    assert row["accounting_checkpoint"] == terminal.model_dump(mode="json")


@pytest.mark.parametrize(
    "value",
    [
        "[]",
        "{}",
        '{"version":1}',
        '{"version":null}',
        '{"version":2}',
        '{"version":"1"}',
    ],
)
async def test_database_rejects_invalid_native_envelope(batch_db, claimed_selector, value):
    _, claim, _ = claimed_selector
    with pytest.raises(Exception, match="batch_accounting_checkpoint"):
        await batch_db.execute_raw(
            "UPDATE deltallm_batch_item SET accounting_checkpoint=$2::jsonb WHERE item_id=$1",
            claim.item_id,
            value,
        )


async def test_native_checkpoint_mutation_plan_does_not_scan_item_history(
    batch_db, claimed_selector
):
    _, claim, _ = claimed_selector
    pending = checkpoint_for(claim)
    await batch_db.execute_raw(
        """INSERT INTO deltallm_batch_item
        (item_id,batch_id,line_number,custom_id,status,request_body,lease_expires_at)
        SELECT 'native-checkpoint-plan-'||n,$1,n+1,'plan-'||n,'pending','{}'::jsonb,
               NOW()+INTERVAL '60 seconds' FROM generate_series(1,10000) n""",
        claim.batch_id,
    )
    await batch_db.execute_raw("ANALYZE deltallm_batch_item")
    value = json.dumps(
        [
            {
                "item_id": claim.item_id,
                "batch_id": claim.batch_id,
                "worker_id": claim.worker_id,
                "claim_epoch": claim.claim_epoch,
                "api_key": claim.api_key,
                "checkpoint": pending.model_dump(mode="json"),
                "expected": None,
            }
        ]
    )
    rows = await batch_db.query_raw(
        "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + WRITE_CHECKPOINTS_SQL,
        value,
        COMPLETION_OUTBOX_MAX_ATTEMPTS,
    )
    work, scans = [rows[0]["QUERY PLAN"][0]["Plan"]], []
    while work:
        node = work.pop()
        if node.get("Relation Name") == "deltallm_batch_item" and node["Node Type"].endswith(
            "Scan"
        ):
            scans.append(node)
        work.extend(node.get("Plans", []))
    assert scans
    for node in scans:
        assert node["Node Type"] == "Index Scan", node
        assert node["Actual Rows"] * node["Actual Loops"] <= 1, node


@pytest.mark.parametrize("transition", ["sent", "retry", "failed", "renew"])
async def test_every_native_delivery_transition_rejects_stale_attempt(
    batch_db, claimed_selector, transition
):
    from datetime import UTC, datetime

    repository, claim, _ = claimed_selector
    store, pending = BatchAccountingRepository(batch_db), checkpoint_for(claim)
    await store.write_many([BatchAccountingWrite(claim, None, pending)], expires_at=deadline())
    await store.write_many(
        [BatchAccountingWrite(claim, pending, uncertain(pending))], expires_at=deadline()
    )
    await batch_db.execute_raw(
        "UPDATE deltallm_batch_completion_outbox "
        "SET next_attempt_at=NOW()-INTERVAL '1 second' WHERE item_id=$1",
        claim.item_id,
    )
    (first,) = await repository.claim_completion_outbox_due(worker_id="delivery", lease_seconds=30)
    await batch_db.execute_raw(
        "UPDATE deltallm_batch_completion_outbox SET lease_expires_at=NOW()-INTERVAL '1 second' WHERE completion_id=$1",
        first.completion_id,
    )
    (second,) = await repository.claim_completion_outbox_due(worker_id="delivery", lease_seconds=30)
    assert second.attempt_count > first.attempt_count
    functions = {
        "sent": (repository.mark_completion_outbox_sent, {}),
        "retry": (
            repository.mark_completion_outbox_retry,
            {"error": "temporary", "next_attempt_at": datetime.now(UTC)},
        ),
        "failed": (repository.mark_completion_outbox_failed, {"error": "terminal"}),
        "renew": (repository.renew_completion_outbox_lease, {"lease_seconds": 30}),
    }
    function, parameters = functions[transition]
    assert not await function(
        completion_id=first.completion_id,
        worker_id="delivery",
        attempt_count=first.attempt_count,
        **parameters,
    )
    assert await function(
        completion_id=second.completion_id,
        worker_id="delivery",
        attempt_count=second.attempt_count,
        **parameters,
    )
