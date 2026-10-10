"""Bounded permit memory and batch calls before runtime activation."""

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.billing.accounting_protocol import (
    DispatchPermit,
    PreissuedPermitGrant,
    ReserveDecision,
    request_fingerprint,
)
from src.billing.durable_microbatch import DurableBatchClosed, DurableMicrobatcher
from src.billing.preissued_permits import PreissuedPermitBank
from src.metrics.prometheus import get_prometheus_registry
from tests.test_accounting_protocol import reservation


class FakePermitRepository:
    def __init__(self, *, grant_limit=None, decisions=()):
        self.refills = []
        self.claims = []
        self.grant_limit = grant_limit
        self.decisions = iter(decisions)

    async def allocate_batch(self, allocations, *, expires_at):
        self.refills.append(list(allocations))
        decision = next(self.decisions, None)
        if decision:
            return [decision for _ in allocations]
        return [
            PreissuedPermitGrant(
                protocol_generation=item.reservation.protocol_generation,
                grant_id=str(uuid4()),
                grantee_id=f"test-owner:{item.fence_token}",
                fence_token=item.fence_token,
                accounting_partition=0,
                allowance=item.reservation.allowance,
                operation_limit=min(
                    self.grant_limit or item.target_operations, item.target_operations
                ),
                expires_at=datetime.now(UTC) + timedelta(seconds=30),
            )
            for item in allocations
        ]

    async def claim_batch(self, claims, *, expires_at):
        self.claims.append(list(claims))
        return [
            DispatchPermit(
                protocol_generation=item.reservation.protocol_generation,
                operation_id=item.reservation.operation_id,
                decision=ReserveDecision.DISPATCH,
                dispatch_token=item.reservation.owner_token,
                accounting_partition=item.grant.accounting_partition,
            )
            for item in claims
        ]


def bank(repository, **overrides):
    return PreissuedPermitBank(
        repository,
        **{"target_operations": 4, "max_operations": 256, "max_subjects": 1024, **overrides},
    )


def fresh(item):
    operation_id = uuid4()
    return item.model_copy(
        update={
            "operation_id": operation_id,
            "owner_token": uuid4(),
            "request_fingerprint": request_fingerprint(
                operation_kind="chat", payload={"operation_id": operation_id}
            ),
        }
    )


def deadline():
    return asyncio.get_running_loop().time() + 1


@pytest.mark.parametrize("count", [1, 8, 32, 256])
async def test_cold_and_warm_calls_do_not_grow_with_subject_count(count):
    repository = FakePermitRepository()
    owner = bank(repository)
    first = [reservation() for _ in range(count)]
    second = [fresh(item) for item in first]
    before = (
        get_prometheus_registry().get_sample_value(
            "deltallm_accounting_permit_actions_total", {"action": "claim", "outcome": "success"}
        )
        or 0
    )
    assert all(
        item.decision is ReserveDecision.DISPATCH
        for item in await owner.reserve_batch(first, expires_at=deadline())
    )
    assert len(repository.refills) == len(repository.claims) == 1
    assert owner.active_subjects == count
    assert owner.available_permits == 3 * count
    assert all(
        item.decision is ReserveDecision.DISPATCH
        for item in await owner.reserve_batch(second, expires_at=deadline())
    )
    assert len(repository.refills) == 1
    assert len(repository.claims) == 2
    assert owner.available_permits == 2 * count
    assert [item.permit_ordinal for item in repository.claims[1]] == [1] * count
    assert (
        get_prometheus_registry().get_sample_value(
            "deltallm_accounting_permit_actions_total", {"action": "claim", "outcome": "success"}
        )
        or 0
    ) - before == 2 * count
    await owner.close()
    assert owner.active_subjects == owner.available_permits == 0


async def test_one_hot_subject_has_unique_local_ordinals_across_batches():
    repository = FakePermitRepository()
    owner = bank(repository)
    first = reservation()
    items = [first, *[fresh(first) for _ in range(7)]]
    await owner.reserve_batch(items, expires_at=deadline())
    assert len(repository.refills) == len(repository.claims) == 1
    assert repository.refills[0][0].target_operations == 32
    assert [item.permit_ordinal for item in repository.claims[0]] == list(range(8))
    await owner.reserve_batch([fresh(first)], expires_at=deadline())
    assert repository.claims[1][0].permit_ordinal == 8
    assert owner.available_permits == 23


async def test_partial_budget_grant_dispatches_only_funded_items():
    repository = FakePermitRepository(
        grant_limit=2, decisions=[None, ReserveDecision.BUDGET_EXHAUSTED]
    )
    owner = bank(repository)
    first = reservation()
    results = await owner.reserve_batch(
        [first, *[fresh(first) for _ in range(4)]], expires_at=deadline()
    )
    assert [item.decision for item in results] == [ReserveDecision.DISPATCH] * 2 + [
        ReserveDecision.BUDGET_EXHAUSTED
    ] * 3
    assert len(repository.refills) == 2
    assert len(repository.claims) == 1
    assert owner.active_subjects == owner.available_permits == 0


async def test_repeated_partial_grants_have_a_fixed_work_bound():
    repository = FakePermitRepository(grant_limit=1)
    owner = bank(repository)
    first = reservation()
    results = await owner.reserve_batch(
        [first, *[fresh(first) for _ in range(9)]], expires_at=deadline()
    )
    assert [item.decision for item in results] == [ReserveDecision.DISPATCH] * 2 + [
        ReserveDecision.CAPACITY_EXHAUSTED
    ] * 8
    assert len(repository.refills) == 2
    assert len(repository.claims) == 1


async def test_memory_capacity_rejection_does_not_evict_an_active_grant():
    repository = FakePermitRepository()
    owner = bank(repository, max_subjects=1)
    first = reservation()
    await owner.reserve_batch([first], expires_at=deadline())
    original = repository.claims[0][0].grant
    denied = await owner.reserve_batch([reservation()], expires_at=deadline())
    assert denied[0].decision is ReserveDecision.CAPACITY_EXHAUSTED
    assert len(repository.refills) == 1
    await owner.reserve_batch([fresh(first)], expires_at=deadline())
    assert repository.claims[1][0].grant == original
    assert repository.claims[1][0].permit_ordinal == 1


async def test_expired_subject_releases_memory_without_creating_money_locally():
    repository = FakePermitRepository()
    owner = bank(repository, max_subjects=1)
    await owner.reserve_batch([reservation()], expires_at=deadline())
    cursor = next(iter(owner._cursors.values()))
    cursor.grant = cursor.grant.model_copy(
        update={"expires_at": datetime.now(UTC) - timedelta(seconds=1)}
    )
    next_item = reservation()
    permits = await owner.reserve_batch([next_item], expires_at=deadline())
    assert permits[0].decision is ReserveDecision.DISPATCH
    assert len(repository.refills) == 2
    assert owner.active_subjects == 1
    assert owner.available_permits == 3


async def test_pruning_has_a_fixed_scan_bound():
    repository = FakePermitRepository()
    # Keep 1000 live subjects so this test exercises scan count, not the separate
    # retained-byte capacity rejection.
    owner = bank(repository, max_retained_bytes=16 * 1024 * 1024)
    for _ in range(4):
        await owner.reserve_batch([reservation() for _ in range(250)], expires_at=deadline())
    original = owner._usable
    owner._usable = MagicMock(side_effect=original)
    owner._prune_expired()
    assert owner._usable.call_count == 256
    assert owner.active_subjects == 1000
    assert owner.available_permits == 3000


@pytest.mark.parametrize(
    "field,value",
    [
        ("protocol_generation", 8),
        ("allowance", Decimal("2")),
        ("allowance", Decimal("1.250")),
        ("organization_id", "another-org"),
        ("model", "other-model"),
    ],
)
async def test_grants_cannot_cross_financial_subjects(field, value):
    repository = FakePermitRepository()
    owner = bank(repository)
    first = reservation()
    await owner.reserve_batch([first], expires_at=deadline())
    second = fresh(first)
    if field in {"organization_id", "model"}:
        second = second.model_copy(
            update={"attribution": second.attribution.model_copy(update={field: value})}
        )
    else:
        second = second.model_copy(update={field: value})
    await owner.reserve_batch([second], expires_at=deadline())
    assert len(repository.refills) == 2
    assert repository.claims[0][0].grant != repository.claims[1][0].grant


@pytest.mark.parametrize("fault", ["duplicate", "size", "generation"])
async def test_invalid_batches_do_not_mutate_the_bank(fault):
    repository = FakePermitRepository()
    owner = bank(repository)
    first = reservation()
    items = [first, first]
    if fault == "size":
        items = [reservation() for _ in range(257)]
    elif fault == "generation":
        items = [first, fresh(first).model_copy(update={"protocol_generation": 8})]
    with pytest.raises(ValueError):
        await owner.reserve_batch(items, expires_at=deadline())
    assert repository.refills == repository.claims == []
    assert owner.active_subjects == owner.available_permits == 0


@pytest.mark.parametrize("error", [TimeoutError, asyncio.CancelledError])
async def test_failed_claim_never_reuses_an_uncertain_ordinal(error):
    repository = FakePermitRepository()
    owner = bank(repository)
    first = reservation()
    repository.claim_batch = AsyncMock(side_effect=error())
    with pytest.raises(error):
        await owner.reserve_batch([first], expires_at=deadline())
    first_fence = repository.refills[0][0].fence_token
    assert owner.active_subjects == owner.available_permits == 0
    repository.claim_batch = FakePermitRepository.claim_batch.__get__(repository)
    await owner.reserve_batch([fresh(first)], expires_at=deadline())
    assert repository.refills[1][0].fence_token != first_fence
    assert repository.claims[0][0].permit_ordinal == 0


async def test_refill_failure_cannot_fall_back_to_another_admission_owner():
    repository = FakePermitRepository()
    repository.allocate_batch = AsyncMock(side_effect=TimeoutError())
    owner = bank(repository)
    with pytest.raises(TimeoutError):
        await owner.reserve_batch([reservation()], expires_at=deadline())
    repository.allocate_batch.assert_awaited_once()
    assert repository.claims == []
    assert owner.active_subjects == owner.available_permits == 0


async def test_close_during_refill_prevents_a_later_claim():
    repository = FakePermitRepository()
    entered, release = asyncio.Event(), asyncio.Event()
    allocate = repository.allocate_batch

    async def blocked(allocations, *, expires_at):
        entered.set()
        await release.wait()
        return await allocate(allocations, expires_at=expires_at)

    repository.allocate_batch = blocked
    owner = bank(repository)
    reserve = asyncio.create_task(owner.reserve_batch([reservation()], expires_at=deadline()))
    await entered.wait()
    close = asyncio.create_task(owner.close())
    await asyncio.sleep(0)
    release.set()
    with pytest.raises(DurableBatchClosed):
        await reserve
    await close
    assert repository.claims == []
    assert owner.active_subjects == owner.available_permits == 0
    with pytest.raises(DurableBatchClosed):
        await owner.reserve_batch([reservation()], expires_at=deadline())


async def test_existing_microbatch_owner_can_use_the_typed_bank_handler():
    repository = FakePermitRepository()
    owner = bank(repository)

    async def handler(items):
        return await owner.reserve_batch(items, expires_at=deadline())

    batcher = DurableMicrobatcher(handler, max_batch_size=8, max_pending=16, dwell_seconds=0.01)
    batcher.start()
    try:
        results = await asyncio.gather(*[batcher.submit(reservation()) for _ in range(8)])
        assert all(item.decision is ReserveDecision.DISPATCH for item in results)
        assert len(repository.refills) == len(repository.claims) == 1
    finally:
        await batcher.close()
        await owner.close()


async def test_metrics_use_only_fixed_actions_and_validated_lanes():
    repository = FakePermitRepository()
    owner = bank(repository, lane=63)
    await owner.reserve_batch([reservation()], expires_at=deadline())
    samples = [
        sample
        for metric in get_prometheus_registry().collect()
        for sample in metric.samples
        if sample.name.startswith("deltallm_accounting_permit_")
    ]
    assert samples
    assert all(set(sample.labels) <= {"action", "outcome", "lane"} for sample in samples)
    assert (
        get_prometheus_registry().get_sample_value(
            "deltallm_accounting_permit_bank_subjects", {"lane": "63"}
        )
        == 1
    )
    await owner.close()
    assert (
        get_prometheus_registry().get_sample_value(
            "deltallm_accounting_permit_bank_available", {"lane": "63"}
        )
        == 0
    )
    with pytest.raises(ValueError, match="lane"):
        bank(repository, lane=64)


async def test_permit_representations_do_not_expose_owner_fences_or_keys():
    repository = FakePermitRepository()
    owner = bank(repository)
    item = reservation()
    await owner.reserve_batch([item], expires_at=deadline())
    cursor = next(iter(owner._cursors.values()))
    assert str(cursor.grant.fence_token) not in repr(cursor)
    assert cursor.grant.grantee_id not in repr(cursor)
    assert item.attribution.api_key not in repr(next(iter(owner._cursors)))
    await owner.close()
