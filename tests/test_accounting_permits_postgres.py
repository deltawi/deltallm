"""Real database bounds, replay, and concurrency for permit batches."""

import asyncio
from decimal import Decimal
import json
from uuid import UUID, uuid4

import pytest

from src.billing.accounting.accounting_protocol import (
    AccountingOutcome,
    AccountingScope,
    BudgetWindowRef,
    PreissuedPermitAllocation,
    PreissuedPermitClaim,
    PreissuedPermitGrant,
    ReserveDecision,
)
from src.db.accounting_permits import AccountingPermitRepository
from src.db.accounting_calls import AccountingProtocolUnavailable
from tests.test_accounting_protocol_postgres import (
    _create_window,
    _finalization,
    _outstanding,
    _repository,
    _reservation,
    _settle_grants,
    _window,
    accounting_db as _accounting_db,
)

pytestmark = pytest.mark.postgres
accounting_db = _accounting_db


class CountingClient:
    def __init__(self, db, *, lose_ack=False):
        self.db = db
        self.calls = 0
        self.lose_ack = lose_ack

    async def query_raw(self, query, *parameters):
        self.calls += 1
        rows = await self.db.query_raw(query, *parameters)
        if self.lose_ack:
            self.lose_ack = False
            raise TimeoutError()
        return rows


def owner(client, *, owner_id="permit-batch-test"):
    return AccountingPermitRepository(client, owner_id=owner_id, statement_budget_seconds=2)


def deadline():
    return asyncio.get_running_loop().time() + 6


def allocation(item, *, target=2):
    return PreissuedPermitAllocation(
        reservation=item, fence_token=uuid4(), target_operations=target
    )


def claims_for(grant, reservations):
    assert isinstance(grant, PreissuedPermitGrant)
    return [
        PreissuedPermitClaim(grant=grant, permit_ordinal=index, reservation=item)
        for index, item in enumerate(reservations)
    ]


async def test_multi_subject_refill_and_multi_grant_claim_use_one_call_each(accounting_db):
    clients, generation = accounting_db
    db = clients[0]
    items = []
    for _ in range(8):
        window_id = str(uuid4())
        await _create_window(db, generation, window_id)
        items.append(_reservation(generation, window_id))
    counted = CountingClient(db)
    repository = owner(counted)
    grants = await repository.allocate_batch(
        [allocation(item) for item in items], expires_at=deadline()
    )
    assert counted.calls == 1
    claims = [claims_for(grant, [item])[0] for grant, item in zip(grants, items, strict=True)]
    permits = await repository.claim_batch(list(reversed(claims)), expires_at=deadline())
    assert counted.calls == 2
    assert [permit.operation_id for permit in permits] == [
        item.operation_id for item in reversed(items)
    ]
    assert all(permit.decision is ReserveDecision.DISPATCH for permit in permits)


async def test_exhausted_and_closed_grants_replay_without_provider_dispatch(accounting_db):
    clients, generation = accounting_db
    db = clients[0]
    groups = []
    for _ in range(2):
        window_id = str(uuid4())
        await _create_window(db, generation, window_id)
        groups.append((window_id, [_reservation(generation, window_id) for _ in range(2)]))
    repository = owner(db)
    grants = await repository.allocate_batch(
        [allocation(items[0]) for _, items in groups], expires_at=deadline()
    )
    claims = [
        claim
        for grant, (_, items) in zip(grants, groups, strict=True)
        for claim in claims_for(grant, items)
    ]
    first = await repository.claim_batch(claims, expires_at=deadline())
    assert all(permit.decision is ReserveDecision.DISPATCH for permit in first)
    for _ in range(2):
        repeated = await repository.claim_batch(claims, expires_at=deadline())
        assert all(
            permit.decision is ReserveDecision.REPLAY and permit.dispatch_token is None
            for permit in repeated
        )
    await _repository(db).finalize_batch(
        [_finalization(item.reservation) for item in claims], expires_at=deadline()
    )
    assert await _settle_grants(db, generation) == 2
    repeated = await repository.claim_batch(claims, expires_at=deadline())
    assert all(permit.decision is ReserveDecision.REPLAY for permit in repeated)
    assert await _outstanding(db, generation) == 0
    for window_id, _ in groups:
        assert await _window(db, window_id) == (Decimal("1.2"), Decimal(0), Decimal(0))


async def test_invalid_second_grant_rolls_back_the_whole_claim_batch(accounting_db):
    clients, generation = accounting_db
    db = clients[0]
    window_id = str(uuid4())
    await _create_window(db, generation, window_id)
    items = [_reservation(generation, window_id) for _ in range(2)]
    repository = owner(db)
    grants = await repository.allocate_batch(
        [allocation(item) for item in items], expires_at=deadline()
    )
    claims = [claims_for(grant, [item])[0] for grant, item in zip(grants, items, strict=True)]
    invalid = claims[1].model_copy(
        update={"grant": claims[1].grant.model_copy(update={"fence_token": uuid4()})}
    )
    with pytest.raises(AccountingProtocolUnavailable):
        await repository.claim_batch([claims[0], invalid], expires_at=deadline())
    assert (
        await db.query_raw(
            "SELECT operation_id FROM deltallm_billing_operations WHERE accounting_generation=$1",
            generation,
        )
        == []
    )
    assert await _window(db, window_id) == (Decimal(0), Decimal(4), Decimal(0))
    permits = await repository.claim_batch(claims, expires_at=deadline())
    assert all(permit.decision is ReserveDecision.DISPATCH for permit in permits)


async def test_two_replicas_replay_one_refill_and_claim_without_double_effect(accounting_db):
    clients, generation = accounting_db
    db = clients[0]
    window_id = str(uuid4())
    await _create_window(db, generation, window_id)
    items = [_reservation(generation, window_id) for _ in range(2)]
    refill = allocation(items[0])
    repositories = [owner(client) for client in clients]
    outcomes = await asyncio.gather(
        *(repository.allocate_batch([refill], expires_at=deadline()) for repository in repositories)
    )
    assert outcomes[0] == outcomes[1]
    assert await _window(db, window_id) == (Decimal(0), Decimal(2), Decimal(0))
    claims = claims_for(outcomes[0][0], items)
    permits = await asyncio.gather(
        *(repository.claim_batch(claims, expires_at=deadline()) for repository in repositories)
    )
    assert (
        sum(permit.decision is ReserveDecision.DISPATCH for batch in permits for permit in batch)
        == 2
    )
    assert (
        sum(permit.decision is ReserveDecision.REPLAY for batch in permits for permit in batch) == 2
    )
    assert await _window(db, window_id) == (Decimal(0), Decimal(2), Decimal(0))


async def test_two_owners_cannot_refill_past_one_hard_budget(accounting_db):
    clients, generation = accounting_db
    db = clients[0]
    window_id = str(uuid4())
    await _create_window(db, generation, window_id, limit="5")
    item = _reservation(generation, window_id)
    outcomes = await asyncio.gather(
        *(
            owner(client, owner_id=f"replica-{index}").allocate_batch(
                [allocation(item, target=4)], expires_at=deadline()
            )
            for index, client in enumerate(clients)
        )
    )
    assert sum(batch[0].operation_limit for batch in outcomes) == 5
    assert await _window(db, window_id) == (Decimal(0), Decimal(5), Decimal(0))


async def test_zero_allowance_grant_keeps_capacity_until_all_ordinals_are_used(accounting_db):
    clients, generation = accounting_db
    db = clients[0]
    window_id = str(uuid4())
    await _create_window(db, generation, window_id)
    items = [_reservation(generation, window_id, allowance="0") for _ in range(3)]
    repository = owner(db)
    grants = await repository.allocate_batch(
        [allocation(items[0], target=3)], expires_at=deadline()
    )
    claims = claims_for(grants[0], items)
    for index, claim in enumerate(claims):
        permits = await repository.claim_batch([claim], expires_at=deadline())
        assert permits[0].decision is ReserveDecision.DISPATCH
        assert await db.query_raw(
            "SELECT state,consumed_operations FROM deltallm_accounting_grants WHERE grant_id=$1",
            grants[0].grant_id,
        ) == [{"state": "draining" if index == 2 else "active", "consumed_operations": index + 1}]
        terminal = _finalization(claim.reservation).model_copy(update={"exact_charge": Decimal(0)})
        await _repository(db).finalize_batch([terminal], expires_at=deadline())
    assert await _settle_grants(db, generation) == 1
    assert await _outstanding(db, generation) == 0
    assert await _window(db, window_id) == (Decimal(0), Decimal(0), Decimal(0))


@pytest.mark.parametrize("phase", ["refill", "claim"])
async def test_real_commit_ack_loss_recovers_exact_identities(accounting_db, phase):
    clients, generation = accounting_db
    db = clients[0]
    window_id = str(uuid4())
    await _create_window(db, generation, window_id)
    item = _reservation(generation, window_id)
    counted = CountingClient(db, lose_ack=phase == "refill")
    repository = owner(counted)
    grants = await repository.allocate_batch([allocation(item)], expires_at=deadline())
    assert counted.calls == (2 if phase == "refill" else 1)
    counted.lose_ack = phase == "claim"
    permits = await repository.claim_batch(claims_for(grants[0], [item]), expires_at=deadline())
    assert counted.calls == 3
    assert permits[0].dispatch_token == item.owner_token
    assert await _window(db, window_id) == (Decimal(0), Decimal(2), Decimal(0))


@pytest.mark.parametrize("outcome", list(AccountingOutcome))
async def test_terminal_replay_ack_loss_never_restores_dispatch(accounting_db, outcome):
    clients, generation = accounting_db
    db = clients[0]
    window_id = str(uuid4())
    await _create_window(db, generation, window_id)
    item = _reservation(generation, window_id)
    counted = CountingClient(db)
    repository = owner(counted)
    grants = await repository.allocate_batch([allocation(item)], expires_at=deadline())
    claims = claims_for(grants[0], [item])
    await repository.claim_batch(claims, expires_at=deadline())
    await _repository(db).finalize_batch([_finalization(item, outcome)], expires_at=deadline())
    counted.lose_ack = True
    repeated = await repository.claim_batch(claims, expires_at=deadline())
    assert counted.calls == 4
    assert repeated[0].decision is ReserveDecision.REPLAY
    assert repeated[0].dispatch_token is None


async def test_cross_subject_refills_lock_shared_windows_in_one_order(accounting_db):
    clients, generation = accounting_db
    db = clients[0]
    shared_id = str(uuid4())
    team_id = "permit-shared-" + shared_id
    await db.execute_raw(
        "INSERT INTO deltallm_accounting_budget_windows "
        "(window_id,protocol_name,generation,scope_type,scope_id,period_key,policy_generation,"
        "limit_exact,window_starts_at,window_ends_at) VALUES ($1,'primary',$2,'team',$3,'test',"
        "1,6,NOW()-INTERVAL '1 minute',NOW()+INTERVAL '1 hour')",
        shared_id,
        generation,
        team_id,
    )
    items = []
    for _ in range(2):
        window_id = str(uuid4())
        await _create_window(db, generation, window_id)
        item = _reservation(generation, window_id)
        items.append(
            item.model_copy(
                update={
                    "attribution": item.attribution.model_copy(update={"team_id": team_id}),
                    "windows": (
                        *item.windows,
                        BudgetWindowRef(
                            window_id=UUID(shared_id),
                            scope_type=AccountingScope.TEAM,
                            scope_id=team_id,
                            policy_generation=1,
                        ),
                    ),
                }
            )
        )
    results = await asyncio.gather(
        *(
            owner(client, owner_id=f"replica-{index}").allocate_batch(
                [allocation(item) for item in (items if index == 0 else reversed(items))],
                expires_at=deadline(),
            )
            for index, client in enumerate(clients)
        )
    )
    assert (
        sum(
            grant.operation_limit
            for batch in results
            for grant in batch
            if isinstance(grant, PreissuedPermitGrant)
        )
        == 6
    )
    assert await _window(db, shared_id) == (Decimal(0), Decimal(6), Decimal(0))


async def test_window_lock_lookup_is_indexed_at_representative_cardinality(accounting_db):
    clients, generation = accounting_db
    db = clients[0]
    window_id = str(uuid4())
    await _create_window(db, generation, window_id)
    item = _reservation(generation, window_id, explicit_window=False)
    await db.execute_raw(
        "INSERT INTO deltallm_accounting_budget_windows "
        "(window_id,protocol_name,generation,scope_type,scope_id,period_key,policy_generation,"
        "limit_exact,window_starts_at,window_ends_at) "
        "SELECT 'permit-plan-'||$1::text||'-'||value,'primary',$1::bigint,'organization',"
        "CASE WHEN value<=25000 THEN $2 ELSE 'unrelated-'||value END,'probe-'||value,1,10,"
        "NOW()-INTERVAL '2 hours',CASE WHEN value<=25000 THEN NOW()-INTERVAL '1 hour' "
        "ELSE NOW()+INTERVAL '1 hour' END FROM generate_series(1,50000) value",
        generation,
        item.attribution.organization_id,
    )
    await db.execute_raw("ANALYZE deltallm_accounting_budget_windows")
    rows = await db.query_raw(
        "EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) "
        "SELECT * FROM deltallm_accounting_permit_window_ids($1,$2::jsonb)",
        generation,
        item.model_dump_json(),
    )
    serialized = json.dumps(rows)
    assert "deltallm_accounting_window_subject_time_idx" in serialized
    assert '"Node Type": "Seq Scan"' not in serialized
    assert await db.query_raw(
        "SELECT * FROM deltallm_accounting_permit_window_ids($1,$2::jsonb)",
        generation,
        item.model_dump_json(),
    ) == [{"window_id": window_id}]
