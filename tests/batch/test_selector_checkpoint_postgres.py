import asyncio
from dataclasses import replace
import json
from time import perf_counter

import pytest

from src.batch.models import BatchItemCreate
from src.batch.repository import BatchRepository
from src.batch.repositories.selector_repository import BatchSelectorRepository, WRITE_CHECKPOINT_SQL
from src.batch.selector_checkpoint import (
    BatchSelectorCheckpoint,
    BatchSelectorClaim,
    BatchSelectorUnavailable,
)
from src.batch.selector_identity import batch_selector_operation_id
from src.router.selection.contracts import (
    SelectorCause,
    SelectorDecision,
    SelectorPolicyIdentity,
    UnattemptedSelectorUsage,
)
from tests import test_batch_db_integration as batch_fixtures

pytestmark = pytest.mark.postgres
batch_db = batch_fixtures.batch_db


def deadline():
    return asyncio.get_running_loop().time() + 2


@pytest.fixture
async def claimed_selector(batch_db):
    repository = BatchRepository(batch_db)
    file_id = await batch_fixtures._seed_batch_file(repository)
    job = await repository.create_job(
        endpoint="/v1/chat/completions",
        input_file_id=file_id,
        model="selected",
        metadata=None,
        created_by_api_key="key-a",
        created_by_user_id=None,
        created_by_team_id=None,
        status="in_progress",
        total_items=1,
    )
    await repository.create_items(
        job.batch_id,
        [
            BatchItemCreate(
                line_number=1, custom_id="one", request_body={"model": "selected", "messages": []}
            )
        ],
    )
    (item,) = await repository.claim_items(batch_id=job.batch_id, worker_id="worker-a")
    claim = BatchSelectorClaim(job.batch_id, item.item_id, "key-a", "worker-a", item.claim_epoch)
    checkpoint = BatchSelectorCheckpoint(
        operation_id=batch_selector_operation_id(job.batch_id, item.item_id),
        input_fingerprint="0" * 64,
        model_group="selected",
        policy_identity=SelectorPolicyIdentity(fingerprint="route-policy-v1:" + "1" * 64),
    )
    return repository, claim, checkpoint


def decided(pending):
    return BatchSelectorCheckpoint(
        **pending.model_dump(exclude={"decision"}),
        decision=SelectorDecision(
            lane="quality",
            minimum_rank=1,
            cause=SelectorCause.INPUT_UNAVAILABLE,
            latency_ms=0.5,
            policy_identity=pending.policy_identity,
            usage=UnattemptedSelectorUsage(),
        ),
    )


async def test_duplicate_workers_cannot_begin_same_selector_twice(batch_db, claimed_selector):
    _, claim, pending = claimed_selector
    repositories = [BatchSelectorRepository(batch_db), BatchSelectorRepository(batch_db)]
    results = await asyncio.gather(
        *(
            repository.write(claim, expected=None, checkpoint=pending, expires_at=deadline())
            for repository in repositories
        ),
        return_exceptions=True,
    )
    assert sum(result is None for result in results) == 1
    assert sum(isinstance(result, BatchSelectorUnavailable) for result in results) == 1


async def test_completion_outbox_row_identity_survives_old_consumer_and_duplicate_completion(
    batch_db, claimed_selector
):
    repository, claim, pending = claimed_selector
    store = BatchSelectorRepository(batch_db)
    await store.write(claim, expected=None, checkpoint=pending, expires_at=deadline())
    await store.write(claim, expected=pending, checkpoint=decided(pending), expires_at=deadline())
    row = {
        "item_id": claim.item_id,
        "claim_epoch": claim.claim_epoch,
        "response_body": {"usage": {"prompt_tokens": 1, "completion_tokens": 1}},
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        "provider_cost": 0.01,
        "billed_cost": 0.01,
        "outbox_payload": {
            "batch_id": claim.batch_id,
            "item_id": claim.item_id,
            "billing_event_id": str(pending.operation_id),
        },
    }
    assert (
        await repository.complete_items_with_outbox_bulk(items=[row], worker_id=claim.worker_id)
        == "completed"
    )
    assert (
        await repository.complete_items_with_outbox_bulk(items=[row], worker_id=claim.worker_id)
        == "already_completed"
    )
    (record,) = await repository.list_completion_outbox_by_item_ids([claim.item_id])
    assert record.completion_id == str(pending.operation_id)


async def test_decision_survives_requeue_reclaim_and_work_slice_load(batch_db, claimed_selector):
    repository, claim, pending = claimed_selector
    store = BatchSelectorRepository(batch_db)
    await store.write(claim, expected=None, checkpoint=pending, expires_at=deadline())
    complete = decided(pending)
    await store.write(claim, expected=pending, checkpoint=complete, expires_at=deadline())
    await store.write(claim, expected=pending, checkpoint=complete, expires_at=deadline())
    items = await repository.load_claim_items([claim.item_id])
    assert items[0].selector_checkpoint == complete.model_dump(mode="json")
    await batch_db.execute_raw(
        "UPDATE deltallm_batch_item SET lease_expires_at=NOW()-INTERVAL '1 second' WHERE item_id=$1",
        claim.item_id,
    )
    (reclaimed,) = await repository.claim_items(batch_id=claim.batch_id, worker_id="worker-b")
    assert reclaimed.claim_epoch > claim.claim_epoch
    assert reclaimed.selector_checkpoint == complete.model_dump(mode="json")
    with pytest.raises(BatchSelectorUnavailable):
        await store.write(claim, expected=pending, checkpoint=complete, expires_at=deadline())


@pytest.mark.parametrize(
    "field,value",
    [
        ("api_key", "other-tenant"),
        ("batch_id", "other-job"),
        ("item_id", "other-item"),
        ("worker_id", "worker-b"),
        ("claim_epoch", 0),
    ],
)
async def test_checkpoint_mutations_reject_other_tenant_or_stale_claim(
    batch_db, claimed_selector, field, value
):
    _, claim, pending = claimed_selector
    with pytest.raises(BatchSelectorUnavailable):
        await BatchSelectorRepository(batch_db).write(
            replace(claim, **{field: value}),
            expected=None,
            checkpoint=pending,
            expires_at=deadline(),
        )
    (row,) = await batch_db.query_raw(
        "SELECT selector_checkpoint FROM deltallm_batch_item WHERE item_id=$1", claim.item_id
    )
    assert row["selector_checkpoint"] is None


@pytest.mark.parametrize("change", ["cancelled", "expired", "lease_lost", "terminal"])
async def test_checkpoint_rejects_cancelled_expired_or_unowned_work(
    batch_db, claimed_selector, change
):
    _, claim, pending = claimed_selector
    if change == "lease_lost":
        await batch_db.execute_raw(
            "UPDATE deltallm_batch_item SET lease_expires_at=NOW() WHERE item_id=$1", claim.item_id
        )
    elif change == "terminal":
        await batch_db.execute_raw(
            "UPDATE deltallm_batch_item SET status='failed' WHERE item_id=$1", claim.item_id
        )
    elif change == "cancelled":
        await batch_db.execute_raw(
            "UPDATE deltallm_batch_job SET cancel_requested_at=NOW() WHERE batch_id=$1",
            claim.batch_id,
        )
    else:
        await batch_db.execute_raw(
            "UPDATE deltallm_batch_job SET expires_at=NOW() WHERE batch_id=$1", claim.batch_id
        )
    with pytest.raises(BatchSelectorUnavailable):
        await BatchSelectorRepository(batch_db).write(
            claim, expected=None, checkpoint=pending, expires_at=deadline()
        )


async def test_checkpoint_lock_wait_is_bounded_and_rolls_back(batch_db, claimed_selector):
    _, claim, pending = claimed_selector
    async with batch_db.tx() as tx:
        await tx.query_raw(
            "SELECT item_id FROM deltallm_batch_item WHERE item_id=$1 FOR UPDATE", claim.item_id
        )
        started = perf_counter()
        with pytest.raises(BatchSelectorUnavailable):
            await BatchSelectorRepository(batch_db).write(
                claim, expected=None, checkpoint=pending, expires_at=deadline()
            )
        assert perf_counter() - started < 1
    await BatchSelectorRepository(batch_db).write(
        claim, expected=None, checkpoint=pending, expires_at=deadline()
    )


@pytest.mark.parametrize("value", ["[]", '{"version":2}', '{"version":null}', '{"version":"1"}'])
async def test_database_rejects_malformed_checkpoint_envelope(batch_db, claimed_selector, value):
    _, claim, _ = claimed_selector
    with pytest.raises(Exception, match="deltallm_batch_selector_checkpoint_bound"):
        await batch_db.execute_raw(
            "UPDATE deltallm_batch_item SET selector_checkpoint=$2::jsonb WHERE item_id=$1",
            claim.item_id,
            value,
        )


async def test_checkpoint_query_is_primary_key_bounded_at_representative_cardinality(
    batch_db, claimed_selector
):
    _, claim, pending = claimed_selector
    await batch_db.execute_raw(
        """INSERT INTO deltallm_batch_item
        (item_id,batch_id,line_number,custom_id,status,request_body,lease_expires_at)
        SELECT 'checkpoint-plan-'||n,$1,n+1,'plan-'||n,'in_progress','{}'::jsonb,
               NOW()+INTERVAL '60 seconds'
        FROM generate_series(1,10000) n""",
        claim.batch_id,
    )
    await batch_db.execute_raw("ANALYZE deltallm_batch_item")
    rows = await batch_db.query_raw(
        "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + WRITE_CHECKPOINT_SQL,
        claim.item_id,
        claim.batch_id,
        claim.worker_id,
        claim.claim_epoch,
        claim.api_key,
        pending.model_dump_json(),
        None,
    )
    report = rows[0]["QUERY PLAN"][0]
    nodes, work = [], [report["Plan"]]
    while work:
        node = work.pop()
        nodes.append(node)
        work.extend(node.get("Plans", []))
    scans = [
        node
        for node in nodes
        if node.get("Relation Name") == "deltallm_batch_item" and node["Node Type"].endswith("Scan")
    ]
    assert len(scans) == 1
    assert scans[0]["Node Type"] == "Index Scan"
    assert scans[0]["Index Name"] == "deltallm_batch_item_pkey"
    assert scans[0]["Actual Rows"] == scans[0]["Actual Loops"] == 1
    print(
        json.dumps({"execution_ms": report["Execution Time"], "item_scan": scans[0]["Node Type"]})
    )
