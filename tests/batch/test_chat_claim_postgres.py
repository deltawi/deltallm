import asyncio
from time import perf_counter
from unittest.mock import AsyncMock

import pytest

from src.batch.chat_capacity import ChatDeploymentCapacity
from src.batch.chat_lease_lifecycle import ChatItemLeaseWatch, ClaimedChatAttemptCapacity
from src.batch.repositories.item_repository import RENEW_ITEM_LEASE_SQL
from src.batch.worker_types import BatchItemLeaseLostError
from src.router.execution import RequestDeadline
from tests.batch import test_selector_checkpoint_postgres as fixtures
from tests.batch.test_chat_capacity import deployment
from tests.batch.test_chat_claim_guard import stop_task

pytestmark = pytest.mark.postgres
batch_db = fixtures.batch_db
claimed_selector = fixtures.claimed_selector


async def renew(repository, claim):
    return await repository.renew_item_lease(
        item_id=claim.item_id,
        worker_id=claim.worker_id,
        claim_epoch=claim.claim_epoch,
        lease_seconds=30,
        expires_at=asyncio.get_running_loop().time() + 1,
    )


@pytest.mark.parametrize("new_worker", ["worker-b", "worker-a"])
async def test_reclaimed_item_cannot_pass_old_fence_or_dispatch(
    batch_db, claimed_selector, new_worker
):
    repository, claim, _ = claimed_selector
    watch = ChatItemLeaseWatch(
        asyncio.create_task(asyncio.Event().wait()), asyncio.Event(), stop_task
    )
    capacity = ChatDeploymentCapacity(1)
    guard = ClaimedChatAttemptCapacity(capacity, watch, lambda _: renew(repository, claim))
    provider = AsyncMock()
    waiting = asyncio.Event()

    async def attempt():
        waiting.set()
        async with guard.slot(deployment(), RequestDeadline.after(2)):
            await provider()

    task = None
    try:
        async with capacity.slot(deployment(), RequestDeadline.after(2)):
            task = asyncio.create_task(attempt())
            await waiting.wait()
            await batch_db.execute_raw(
                "UPDATE deltallm_batch_item SET lease_expires_at=NOW()-INTERVAL '1 second' WHERE item_id=$1",
                claim.item_id,
            )
            (reclaimed,) = await repository.claim_items(
                batch_id=claim.batch_id, worker_id=new_worker
            )
            assert reclaimed.claim_epoch > claim.claim_epoch
            provider.assert_not_awaited()
        with pytest.raises(BatchItemLeaseLostError):
            await task
    finally:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await watch.stop()
    provider.assert_not_awaited()
    assert capacity._total == 0
    assert not await repository.mark_item_completed(
        item_id=claim.item_id,
        worker_id=claim.worker_id,
        claim_epoch=claim.claim_epoch,
        response_body={},
        usage=None,
        provider_cost=0,
        billed_cost=0,
    )
    assert not await repository.items.mark_item_failed(
        item_id=claim.item_id,
        worker_id=claim.worker_id,
        claim_epoch=claim.claim_epoch,
        error_body={"message": "stale"},
        last_error="stale",
        retryable=False,
    )
    (row,) = await repository.load_claim_items([claim.item_id])
    assert row.locked_by == new_worker and row.claim_epoch == reclaimed.claim_epoch
    assert row.status == "in_progress"


async def test_renewed_waiting_item_is_not_reclaimable(batch_db, claimed_selector):
    repository, claim, _ = claimed_selector
    assert await renew(repository, claim)
    assert not await repository.claim_items(batch_id=claim.batch_id, worker_id="worker-b")
    (row,) = await repository.load_claim_items([claim.item_id])
    assert row.claim_epoch == claim.claim_epoch and row.locked_by == claim.worker_id


async def test_dispatch_renewal_lock_timeout_leaves_no_open_transaction(batch_db, claimed_selector):
    repository, claim, _ = claimed_selector
    async with batch_db.tx() as other:
        await other.query_raw(
            "SELECT item_id FROM deltallm_batch_item WHERE item_id=$1 FOR UPDATE", claim.item_id
        )
        started = perf_counter()
        assert not await renew(repository, claim)
        assert perf_counter() - started < 1
    assert await renew(repository, claim)


async def test_dispatch_renewal_deadline_never_mutates_expired_call(batch_db, claimed_selector):
    repository, claim, _ = claimed_selector
    (before,) = await repository.load_claim_items([claim.item_id])
    assert not await repository.renew_item_lease(
        item_id=claim.item_id,
        worker_id=claim.worker_id,
        claim_epoch=claim.claim_epoch,
        lease_seconds=300,
        expires_at=asyncio.get_running_loop().time() - 1,
    )
    (after,) = await repository.load_claim_items([claim.item_id])
    assert before.lease_expires_at == after.lease_expires_at


async def test_dispatch_renewal_query_is_primary_key_bounded(batch_db, claimed_selector):
    repository, claim, _ = claimed_selector
    await batch_db.execute_raw(
        """INSERT INTO deltallm_batch_item
        (item_id,batch_id,line_number,custom_id,status,request_body,locked_by)
        SELECT 'split-plan-'||n,$1,n+1,'split-plan-'||n,'in_progress','{}'::jsonb,$2
        FROM generate_series(1,10000) n""",
        claim.batch_id,
        claim.worker_id,
    )
    await batch_db.execute_raw("ANALYZE deltallm_batch_item")
    rows = await batch_db.query_raw(
        "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + RENEW_ITEM_LEASE_SQL,
        claim.item_id,
        claim.worker_id,
        30,
        claim.claim_epoch,
    )
    plan = rows[0]["QUERY PLAN"][0]["Plan"]["Plans"][0]
    assert plan["Node Type"] == "Index Scan"
    assert plan["Index Name"] == "deltallm_batch_item_pkey"
    assert plan["Actual Rows"] == plan["Actual Loops"] == 1
    assert await renew(repository, claim)
