"""Real PostgreSQL accounting-v2 atomicity, replay, and concurrency contracts."""

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import os
from uuid import UUID, uuid4

from prisma import Prisma
import pytest

from src.billing.accounting_protocol import (
    AccountingAttribution,
    AccountingFinalization,
    AccountingOutcome,
    AccountingReservation,
    AccountingScope,
    BudgetWindowRef,
    ReserveDecision,
    request_fingerprint,
)
from src.config import DatabaseConnectionSettings
from src.db.accounting_pool import AccountingPostgresManager
from src.db.accounting_protocol import AccountingProtocolRepository
from src.db.accounting_protocol import AccountingProtocolUnavailable
from src.db.accounting_projection import AccountingProjectionRepository

pytestmark = pytest.mark.postgres


@pytest.fixture
async def accounting_db():
    url = os.getenv("DATABASE_URL")
    if not url:
        if os.getenv("CI"):
            pytest.fail("CI must provision PostgreSQL")
        pytest.skip("DATABASE_URL is required")
    generation = int(uuid4().int % 1_000_000_000) + 1
    bootstrap_window = str(uuid4())
    clients = [Prisma(datasource={"url": url}) for _ in range(2)]
    for client in clients:
        await client.connect()
    db = clients[0]
    try:
        await db.execute_raw(
            "INSERT INTO deltallm_accounting_protocols "
            "(protocol_name,generation,writer_version,state,partition_count,"
            "max_outstanding_per_partition) VALUES ('primary',$1,2,'prepared',4,1000)",
            generation,
        )
        await db.execute_raw(
            "INSERT INTO deltallm_accounting_partitions "
            "(protocol_name,generation,partition_id,max_outstanding) "
            "SELECT 'primary',$1,value,1000 FROM generate_series(0,3) value",
            generation,
        )
        await _create_window(db, generation, bootstrap_window, limit="1000000")
        await db.execute_raw("SELECT deltallm_activate_accounting_protocol($1)", generation)
        yield clients, generation
    finally:
        await db.execute_raw(
            "DELETE FROM deltallm_accounting_terminal_journal WHERE generation=$1", generation
        )
        await db.execute_raw(
            "DELETE FROM deltallm_accounting_terminal_capacity WHERE generation=$1", generation
        )
        await db.execute_raw(
            "DELETE FROM deltallm_accounting_events WHERE generation=$1", generation
        )
        await db.execute_raw(
            "DELETE FROM deltallm_accounting_reservations WHERE operation_id IN "
            "(SELECT operation_id FROM deltallm_billing_operations "
            "WHERE accounting_generation=$1)",
            generation,
        )
        await db.execute_raw(
            "DELETE FROM deltallm_billing_operations WHERE accounting_generation=$1", generation
        )
        await db.execute_raw(
            "DELETE FROM deltallm_accounting_grant_windows WHERE grant_id IN "
            "(SELECT grant_id FROM deltallm_accounting_grants WHERE generation=$1)",
            generation,
        )
        await db.execute_raw(
            "DELETE FROM deltallm_accounting_grants WHERE generation=$1", generation
        )
        await db.execute_raw(
            "DELETE FROM deltallm_accounting_projection_checkpoints WHERE generation=$1",
            generation,
        )
        await db.execute_raw(
            "DELETE FROM deltallm_accounting_budget_windows WHERE generation=$1", generation
        )
        await db.execute_raw(
            "DELETE FROM deltallm_accounting_partitions WHERE generation=$1", generation
        )
        await db.execute_raw(
            "DELETE FROM deltallm_accounting_protocols WHERE generation=$1", generation
        )
        for client in clients:
            await client.disconnect()


async def _create_window(db: Prisma, generation: int, window_id: str, *, limit: str = "10") -> None:
    await db.execute_raw(
        "INSERT INTO deltallm_accounting_budget_windows "
        "(window_id,protocol_name,generation,scope_type,scope_id,period_key,"
        "policy_generation,limit_exact,window_starts_at,window_ends_at) "
        "VALUES ($1,'primary',$2,'organization',$3,$4,1,$5::numeric,NOW()-INTERVAL '1 minute',"
        "NOW()+INTERVAL '1 hour')",
        window_id,
        generation,
        "org-" + window_id,
        "test-" + window_id,
        limit,
    )


def _reservation(
    generation: int,
    window_id: str,
    *,
    allowance: str = "1",
    explicit_window: bool = True,
):
    operation_id = uuid4()
    organization_id = "org-" + window_id
    fingerprint = request_fingerprint(operation_kind="chat", payload={"operation_id": operation_id})
    return AccountingReservation(
        protocol_generation=generation,
        operation_id=operation_id,
        owner_token=uuid4(),
        request_fingerprint=fingerprint,
        attribution=AccountingAttribution(
            api_key="key-" + window_id,
            user_id="user-" + window_id,
            team_id="team-" + window_id,
            organization_id=organization_id,
            model="gpt-test",
            deployment_id="deployment-test",
            provider="test",
            call_type="chat",
        ),
        allowance=Decimal(allowance),
        windows=(
            BudgetWindowRef(
                window_id=UUID(window_id),
                scope_type=AccountingScope.ORGANIZATION,
                scope_id=organization_id,
                policy_generation=1,
            ),
        )
        if explicit_window
        else (),
        pricing_snapshot={"version": "test-v1"},
        audit_envelope={"action": "provider.dispatch", "redacted": True},
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )


def _finalization(reservation, outcome=AccountingOutcome.COMPLETED):
    completed = outcome is AccountingOutcome.COMPLETED
    return AccountingFinalization(
        protocol_generation=reservation.protocol_generation,
        operation_id=reservation.operation_id,
        owner_token=reservation.owner_token,
        request_fingerprint=reservation.request_fingerprint,
        component_id="provider-attempt-1",
        event_id=uuid4(),
        outcome=outcome,
        exact_charge=Decimal("0.6") if completed else None,
        spend_payload={"request_id": str(reservation.operation_id)} if completed else None,
        audit_envelope={"action": "provider.terminal", "redacted": True},
        occurred_at=datetime.now(UTC),
        uncertainty_reason="provider_ack_lost" if outcome is AccountingOutcome.UNCERTAIN else None,
    )


async def _window(db, window_id):
    (row,) = await db.query_raw(
        "SELECT committed_exact::text AS committed,reserved_exact::text AS reserved,"
        "provisional_exact::text AS provisional FROM deltallm_accounting_budget_windows "
        "WHERE window_id=$1",
        window_id,
    )
    return tuple(Decimal(row[name]) for name in ("committed", "reserved", "provisional"))


async def _outstanding(db: Prisma, generation: int) -> int:
    rows = await db.query_raw(
        "SELECT COALESCE(sum(outstanding_count),0)::integer AS count "
        "FROM deltallm_accounting_partitions WHERE generation=$1",
        generation,
    )
    return int(rows[0]["count"])


async def test_projection_does_not_claim_empty_partitions(accounting_db):
    clients, generation = accounting_db
    repository = AccountingProjectionRepository(clients[0])
    await repository.initialize(
        projection_name="legacy-spend-audit-v1",
        generation=generation,
    )

    claims = await repository.claim_many(
        projection_name="legacy-spend-audit-v1",
        generation=generation,
        worker_id="empty-partition-test",
        lease_seconds=30,
        limit=4,
    )

    assert claims == []


def _repository(
    db: Prisma,
    *,
    target_operations: int = 1,
    grantee_id: str = "postgres-contract-test",
) -> AccountingProtocolRepository:
    return AccountingProtocolRepository(
        db,
        statement_budget_seconds=0.25,
        grant_target_operations=target_operations,
        grantee_id=grantee_id,
    )


async def _settle_grants(db: Prisma, generation: int) -> int:
    rows = await db.query_raw(
        "SELECT deltallm_accounting_reconcile_grants($1,256) AS count",
        generation,
    )
    return int(rows[0]["count"])


async def test_reserve_and_finalize_use_two_durable_acks_with_idempotent_replay(accounting_db):
    clients, generation = accounting_db
    db = clients[0]
    window_id = str(uuid4())
    await _create_window(db, generation, window_id)
    item = _reservation(generation, window_id)
    repository = _repository(db)
    deadline = asyncio.get_running_loop().time() + 2

    (permit,) = await repository.reserve_batch([item], expires_at=deadline)
    assert permit.decision is ReserveDecision.DISPATCH
    assert permit.dispatch_token == item.owner_token
    assert await _window(db, window_id) == (Decimal(0), Decimal(1), Decimal(0))

    (replay,) = await repository.reserve_batch(
        [item], expires_at=asyncio.get_running_loop().time() + 2
    )
    assert replay.decision is ReserveDecision.REPLAY
    assert replay.dispatch_token is None
    assert await _window(db, window_id) == (Decimal(0), Decimal(1), Decimal(0))

    terminal = _finalization(item).model_copy(
        update={
            "spend_payload": {
                "cost": Decimal("0.6"),
                "event_id": uuid4(),
                "occurred_at": datetime.now(UTC),
            },
            "audit_envelope": {
                "action": "provider.terminal",
                "occurred_at": datetime.now(UTC),
            },
        }
    )
    (receipt,) = await repository.finalize_batch(
        [terminal], expires_at=asyncio.get_running_loop().time() + 2
    )
    assert receipt.outcome is AccountingOutcome.COMPLETED
    assert not receipt.replayed
    assert await _settle_grants(db, generation) == 1
    assert await _window(db, window_id) == (Decimal("0.6"), Decimal(0), Decimal(0))

    (terminal_replay,) = await repository.finalize_batch(
        [terminal], expires_at=asyncio.get_running_loop().time() + 2
    )
    assert terminal_replay.replayed
    assert terminal_replay.event_sequence == receipt.event_sequence
    assert await _window(db, window_id) == (Decimal("0.6"), Decimal(0), Decimal(0))

    conflicting = terminal.model_copy(update={"exact_charge": Decimal("0.7")})
    with pytest.raises(AccountingProtocolUnavailable):
        await repository.finalize_batch(
            [conflicting], expires_at=asyncio.get_running_loop().time() + 2
        )
    assert await _window(db, window_id) == (Decimal("0.6"), Decimal(0), Decimal(0))


async def test_direct_pool_executes_accounting_protocol_with_native_deadlines(accounting_db):
    clients, generation = accounting_db
    db = clients[0]
    url = os.environ["DATABASE_URL"]
    window_id = str(uuid4())
    await _create_window(db, generation, window_id)
    item = _reservation(generation, window_id)
    manager = AccountingPostgresManager()
    await manager.connect(
        DatabaseConnectionSettings(url=url, pool_size=2, pool_timeout=1),
        pool_size=1,
        acquisition_seconds=0.2,
        statement_seconds=0.25,
        lock_seconds=0.2,
    )
    try:
        assert manager.client is not None
        repository = AccountingProtocolRepository(
            manager.client,
            statement_budget_seconds=0.25,
            grant_target_operations=1,
            grantee_id="direct-pool-contract-test",
        )
        (permit,) = await repository.reserve_batch(
            [item],
            expires_at=asyncio.get_running_loop().time() + 1,
        )
        terminal = _finalization(item).model_copy(
            update={
                "spend_payload": {
                    "cost": Decimal("0.6"),
                    "event_id": uuid4(),
                    "occurred_at": datetime.now(UTC),
                }
            }
        )
        (receipt,) = await repository.finalize_batch(
            [terminal],
            expires_at=asyncio.get_running_loop().time() + 1,
        )
    finally:
        await manager.disconnect()

    assert permit.decision is ReserveDecision.DISPATCH
    assert receipt.outcome is AccountingOutcome.COMPLETED
    assert await _settle_grants(db, generation) == 1
    assert await _outstanding(db, generation) == 0
    assert await _window(db, window_id) == (Decimal("0.6"), Decimal(0), Decimal(0))


async def test_committed_reservation_with_lost_ack_recovers_before_dispatch(accounting_db):
    clients, generation = accounting_db
    db = clients[0]
    window_id = str(uuid4())
    await _create_window(db, generation, window_id)
    item = _reservation(generation, window_id)

    class LostAckDatabase:
        def __init__(self):
            self.lost = False

        async def query_raw(self, query, *parameters):
            rows = await db.query_raw(query, *parameters)
            if "deltallm_accounting_admit_grant_batch" in query and not self.lost:
                self.lost = True
                raise TimeoutError()
            return rows

    repository = _repository(LostAckDatabase())
    (permit,) = await repository.reserve_batch(
        [item], expires_at=asyncio.get_running_loop().time() + 2
    )

    assert permit.decision is ReserveDecision.DISPATCH
    assert permit.dispatch_token == item.owner_token
    assert permit.accounting_partition is not None
    assert await _outstanding(db, generation) == 1
    rows = await db.query_raw(
        "SELECT count(*)::integer AS count FROM deltallm_accounting_events "
        "WHERE operation_id=$1 AND event_type='reserved'",
        str(item.operation_id),
    )
    assert rows == [{"count": 1}]


async def test_committed_finalization_with_lost_ack_recovers_terminal_receipt(accounting_db):
    clients, generation = accounting_db
    db = clients[0]
    window_id = str(uuid4())
    await _create_window(db, generation, window_id)
    item = _reservation(generation, window_id)
    repository = _repository(db)
    (permit,) = await repository.reserve_batch(
        [item], expires_at=asyncio.get_running_loop().time() + 2
    )
    assert permit.decision is ReserveDecision.DISPATCH
    terminal = _finalization(item).model_copy(
        update={
            "spend_payload": {
                "cost": Decimal("0.6"),
                "event_id": uuid4(),
                "occurred_at": datetime.now(UTC),
            },
            "audit_envelope": {
                "action": "provider.terminal",
                "occurred_at": datetime.now(UTC),
            },
        }
    )

    class LostFinalizationAckDatabase:
        def __init__(self):
            self.lost = False

        async def query_raw(self, query, *parameters):
            rows = await db.query_raw(query, *parameters)
            if "deltallm_accounting_finalize_grant_batch" in query and not self.lost:
                self.lost = True
                raise TimeoutError()
            return rows

    recovery_repository = _repository(LostFinalizationAckDatabase())
    (receipt,) = await recovery_repository.finalize_batch(
        [terminal], expires_at=asyncio.get_running_loop().time() + 2
    )

    assert receipt.replayed
    assert receipt.outcome is AccountingOutcome.COMPLETED
    assert await _settle_grants(db, generation) == 1
    assert await _outstanding(db, generation) == 0
    rows = await db.query_raw(
        "SELECT count(*)::integer AS count FROM deltallm_accounting_events "
        "WHERE operation_id=$1 AND event_type='finalized'",
        str(item.operation_id),
    )
    assert rows == [{"count": 1}]


async def test_concurrent_reservations_cannot_overspend_one_hot_window(accounting_db):
    clients, generation = accounting_db
    window_id = str(uuid4())
    await _create_window(clients[0], generation, window_id, limit="1.5")
    items = [_reservation(generation, window_id) for _ in range(12)]
    repositories = [_repository(client) for client in clients]

    async def reserve(index):
        return (
            await repositories[index % 2].reserve_batch(
                [items[index]], expires_at=asyncio.get_running_loop().time() + 5
            )
        )[0]

    permits = await asyncio.gather(*(reserve(index) for index in range(len(items))))
    assert sum(item.decision is ReserveDecision.DISPATCH for item in permits) == 1
    assert sum(item.decision is ReserveDecision.BUDGET_EXHAUSTED for item in permits) == 11
    assert await _window(clients[0], window_id) == (Decimal(0), Decimal(1), Decimal(0))


@pytest.mark.parametrize(
    "outcome,expected",
    [
        (AccountingOutcome.NOT_DISPATCHED, (Decimal(0), Decimal(0), Decimal(0))),
        (AccountingOutcome.UNCERTAIN, (Decimal(0), Decimal(0), Decimal(1))),
    ],
)
async def test_terminal_failure_semantics_release_or_provisionally_debit(
    accounting_db, outcome, expected
):
    clients, generation = accounting_db
    db = clients[0]
    window_id = str(uuid4())
    await _create_window(db, generation, window_id)
    item = _reservation(generation, window_id)
    repository = _repository(db)
    await repository.reserve_batch([item], expires_at=asyncio.get_running_loop().time() + 2)
    await repository.finalize_batch(
        [_finalization(item, outcome)], expires_at=asyncio.get_running_loop().time() + 2
    )
    assert await _settle_grants(db, generation) == 1
    assert await _window(db, window_id) == expected


async def test_empty_window_list_resolves_all_active_attribution_windows(accounting_db):
    clients, generation = accounting_db
    db = clients[0]
    window_id = str(uuid4())
    await _create_window(db, generation, window_id)
    item = _reservation(generation, window_id, explicit_window=False)
    (permit,) = await _repository(db).reserve_batch(
        [item], expires_at=asyncio.get_running_loop().time() + 2
    )
    assert permit.decision is ReserveDecision.DISPATCH
    assert await _window(db, window_id) == (Decimal(0), Decimal(1), Decimal(0))


async def test_completed_failover_keeps_unresolved_attempt_allowance_provisional(accounting_db):
    clients, generation = accounting_db
    db = clients[0]
    window_id = str(uuid4())
    await _create_window(db, generation, window_id)
    item = _reservation(generation, window_id)
    repository = _repository(db)
    await repository.reserve_batch([item], expires_at=asyncio.get_running_loop().time() + 2)
    terminal = _finalization(item).model_copy(update={"unresolved_attempts": 1})
    await repository.finalize_batch([terminal], expires_at=asyncio.get_running_loop().time() + 2)
    assert await _settle_grants(db, generation) == 1
    assert await _window(db, window_id) == (
        Decimal("0.6"),
        Decimal(0),
        Decimal("0.4"),
    )


async def test_expired_dispatch_becomes_provisional_without_provider_retry(accounting_db):
    clients, generation = accounting_db
    db = clients[0]
    window_id = str(uuid4())
    await _create_window(db, generation, window_id)
    item = _reservation(generation, window_id)
    await _repository(db).reserve_batch([item], expires_at=asyncio.get_running_loop().time() + 2)
    await db.execute_raw(
        "UPDATE deltallm_billing_operations SET "
        "created_at=NOW()-INTERVAL '10 minutes',expires_at=NOW()-INTERVAL '1 second' "
        "WHERE operation_id=$1",
        str(item.operation_id),
    )
    recovered = await AccountingProjectionRepository(db).reconcile_expired(
        generation=generation,
        limit=10,
    )
    assert recovered == 1
    assert await _window(db, window_id) == (Decimal(0), Decimal(0), Decimal(1))
    rows = await db.query_raw(
        "SELECT outcome,payload_json->>'uncertainty_reason' AS reason "
        "FROM deltallm_accounting_events WHERE operation_id=$1 AND event_type='finalized'",
        str(item.operation_id),
    )
    assert rows == [{"outcome": "uncertain", "reason": "reservation_expired"}]
    resolved = await db.query_raw(
        "SELECT deltallm_accounting_resolve_provisional("
        "$1,$2,0::numeric,'null'::jsonb,'provider confirmed no charge') AS sequence",
        generation,
        str(item.operation_id),
    )
    assert int(resolved[0]["sequence"]) > 0
    replayed = await db.query_raw(
        "SELECT deltallm_accounting_resolve_provisional("
        "$1,$2,0::numeric,'null'::jsonb,'provider confirmed no charge') AS sequence",
        generation,
        str(item.operation_id),
    )
    assert replayed == resolved
    assert await _window(db, window_id) == (Decimal(0), Decimal(0), Decimal(0))


async def test_worker_rolls_expired_budget_window_before_new_admission(accounting_db):
    clients, generation = accounting_db
    db = clients[0]
    window_id = str(uuid4())
    await _create_window(db, generation, window_id)
    await db.execute_raw(
        "UPDATE deltallm_accounting_budget_windows SET "
        "window_starts_at=NOW()-INTERVAL '2 hours',"
        "window_ends_at=NOW()-INTERVAL '1 hour',renewal_spec='1h' WHERE window_id=$1",
        window_id,
    )
    repository = AccountingProjectionRepository(db)
    assert await repository.roll_budget_windows(generation=generation, limit=10) == 1
    rows = await db.query_raw(
        "SELECT window_id FROM deltallm_accounting_budget_windows "
        "WHERE generation=$1 AND scope_type='organization' AND scope_id=$2 "
        "AND window_starts_at<=NOW() AND window_ends_at>NOW()",
        generation,
        "org-" + window_id,
    )
    assert len(rows) == 1
    item = _reservation(generation, window_id, explicit_window=False)
    (permit,) = await _repository(db).reserve_batch(
        [item], expires_at=asyncio.get_running_loop().time() + 2
    )
    assert permit.decision is ReserveDecision.DISPATCH


async def test_grant_amortizes_window_mutation_across_operations(accounting_db):
    clients, generation = accounting_db
    db = clients[0]
    window_id = str(uuid4())
    await _create_window(db, generation, window_id, limit="100")
    items = [_reservation(generation, window_id) for _ in range(4)]
    repository = _repository(db, target_operations=4)

    permits = await repository.reserve_batch(
        items,
        expires_at=asyncio.get_running_loop().time() + 2,
    )

    assert all(permit.decision is ReserveDecision.DISPATCH for permit in permits)
    assert await _window(db, window_id) == (Decimal(0), Decimal(4), Decimal(0))
    assert await _outstanding(db, generation) == 4
    grants = await db.query_raw(
        "SELECT allocated_exact::text AS allocated,consumed_operations,state "
        "FROM deltallm_accounting_grants WHERE generation=$1 AND grantee_id=$2",
        generation,
        "postgres-contract-test",
    )
    assert grants == [
        {
            "allocated": "4.000000000000000000",
            "consumed_operations": 4,
            "state": "draining",
        }
    ]

    await repository.finalize_batch(
        [_finalization(item) for item in items],
        expires_at=asyncio.get_running_loop().time() + 2,
    )
    assert await _window(db, window_id) == (Decimal(0), Decimal(4), Decimal(0))
    before_close = await db.query_raw(
        "SELECT settled_operations,settled_exact::text AS settled "
        "FROM deltallm_accounting_grants WHERE generation=$1 AND grantee_id=$2",
        generation,
        "postgres-contract-test",
    )
    assert len(before_close) == 1
    assert before_close[0]["settled_operations"] == 0
    assert Decimal(before_close[0]["settled"]) == 0
    assert await _settle_grants(db, generation) == 1
    assert await _window(db, window_id) == (Decimal("2.4"), Decimal(0), Decimal(0))
    assert await _outstanding(db, generation) == 0


async def test_grants_do_not_cross_allowance_contracts_for_the_same_attribution(accounting_db):
    clients, generation = accounting_db
    db = clients[0]
    window_id = str(uuid4())
    await _create_window(db, generation, window_id, limit="100")
    first = _reservation(generation, window_id, allowance="1")
    second = _reservation(generation, window_id, allowance="2")
    repository = _repository(db, target_operations=4)

    permits = await repository.reserve_batch(
        [first, second],
        expires_at=asyncio.get_running_loop().time() + 2,
    )

    assert all(permit.decision is ReserveDecision.DISPATCH for permit in permits)
    grants = await db.query_raw(
        "SELECT allocated_exact::text AS allocated,operation_limit "
        "FROM deltallm_accounting_grants WHERE generation=$1 AND grantee_id=$2 "
        "ORDER BY allocated_exact",
        generation,
        "postgres-contract-test",
    )
    assert grants == [
        {"allocated": "4.000000000000000000", "operation_limit": 4},
        {"allocated": "8.000000000000000000", "operation_limit": 4},
    ]
    assert await _window(db, window_id) == (Decimal(0), Decimal(12), Decimal(0))


async def test_expired_grant_releases_unused_reserved_capacity(accounting_db):
    clients, generation = accounting_db
    db = clients[0]
    window_id = str(uuid4())
    await _create_window(db, generation, window_id, limit="10")
    item = _reservation(generation, window_id)
    repository = _repository(db, target_operations=4)

    await repository.reserve_batch([item], expires_at=asyncio.get_running_loop().time() + 2)
    await repository.finalize_batch(
        [_finalization(item)],
        expires_at=asyncio.get_running_loop().time() + 2,
    )
    assert await _window(db, window_id) == (Decimal(0), Decimal(4), Decimal(0))
    assert await _outstanding(db, generation) == 4

    await db.execute_raw(
        "UPDATE deltallm_accounting_grants SET expires_at=NOW()-INTERVAL '1 second' "
        "WHERE generation=$1 AND grantee_id=$2",
        generation,
        "postgres-contract-test",
    )
    assert await _settle_grants(db, generation) == 1
    assert await _window(db, window_id) == (Decimal("0.6"), Decimal(0), Decimal(0))
    assert await _outstanding(db, generation) == 0


async def test_concurrent_grants_cannot_overspend_one_hot_window(accounting_db):
    clients, generation = accounting_db
    window_id = str(uuid4())
    await _create_window(clients[0], generation, window_id, limit="5")
    items = [_reservation(generation, window_id) for _ in range(12)]
    repositories = [
        _repository(clients[0], target_operations=4, grantee_id="grant-contender-a"),
        _repository(clients[1], target_operations=4, grantee_id="grant-contender-b"),
    ]

    batches = await asyncio.gather(
        *(
            repository.reserve_batch(
                items[index::2],
                expires_at=asyncio.get_running_loop().time() + 5,
            )
            for index, repository in enumerate(repositories)
        )
    )
    permits = [permit for batch in batches for permit in batch]
    assert sum(item.decision is ReserveDecision.DISPATCH for item in permits) == 5
    assert sum(item.decision is ReserveDecision.BUDGET_EXHAUSTED for item in permits) == 7
    assert await _window(clients[0], window_id) == (Decimal(0), Decimal(5), Decimal(0))
    assert await _outstanding(clients[0], generation) == 5


async def test_grant_capacity_exhaustion_does_not_reserve_budget(accounting_db):
    clients, generation = accounting_db
    db = clients[0]
    window_id = str(uuid4())
    await _create_window(db, generation, window_id, limit="10")
    await db.execute_raw(
        "UPDATE deltallm_accounting_partitions SET outstanding_count=max_outstanding "
        "WHERE generation=$1",
        generation,
    )

    item = _reservation(generation, window_id)
    (permit,) = await _repository(db, target_operations=4).reserve_batch(
        [item],
        expires_at=asyncio.get_running_loop().time() + 2,
    )

    assert permit.decision is ReserveDecision.CAPACITY_EXHAUSTED
    assert await _window(db, window_id) == (Decimal(0), Decimal(0), Decimal(0))
    rows = await db.query_raw(
        "SELECT count(*)::integer AS count FROM deltallm_accounting_grants WHERE generation=$1",
        generation,
    )
    assert rows == [{"count": 0}]


async def test_protocol_probe_checks_the_requested_generation_on_the_database(accounting_db):
    clients, generation = accounting_db
    repository = AccountingProtocolRepository(clients[0])
    assert await repository.protocol_ready(generation) is True
    assert await repository.protocol_ready(generation + 1) is False
    await clients[0].execute_raw(
        "UPDATE deltallm_accounting_protocols SET state='draining' WHERE generation=$1", generation
    )
    assert await repository.protocol_ready(generation) is False


@pytest.mark.parametrize("state", ["dispatched", "accepted", "pending"])
async def test_unsettled_realtime_work_prevents_accounting_activation(accounting_db, state):
    clients, generation = accounting_db
    db = clients[0]
    operation_id = str(uuid4())
    event_id = str(uuid4())
    await db.execute_raw(
        "INSERT INTO deltallm_realtime_billing_intents "
        "(operation_id,session_id,snapshot,state,event_id,receipt_facts,spend_payload,expires_at) "
        "VALUES ($1,$1,'{}'::jsonb,$2,$3,'{}'::jsonb,'{}'::jsonb,NOW()+INTERVAL '1 minute')",
        operation_id,
        state,
        event_id,
    )
    try:
        assert {
            row["lane"]
            for row in await db.query_raw(
                "SELECT lane FROM deltallm_accounting_pending_legacy_work()"
            )
        } == {"realtime"}
        with pytest.raises(Exception, match="accounting_legacy_work_pending"):
            await db.execute_raw("SELECT deltallm_activate_accounting_protocol($1)", generation)
        await db.execute_raw(
            "UPDATE deltallm_realtime_billing_intents SET state='settled' WHERE operation_id=$1",
            operation_id,
        )
        assert (
            await db.query_raw("SELECT lane FROM deltallm_accounting_pending_legacy_work()") == []
        )
    finally:
        await db.execute_raw(
            "DELETE FROM deltallm_realtime_billing_intents WHERE operation_id=$1", operation_id
        )


@pytest.mark.parametrize(
    "lane,state",
    [
        ("spend", "queued"),
        ("spend", "blocked"),
        ("selector", "reserved"),
        ("batch", "queued"),
        ("batch", "in_progress"),
        ("batch", "finalizing"),
        ("batch_receipt", "queued"),
        ("batch_receipt", "blocked"),
    ],
)
async def test_each_legacy_writer_blocks_activation_until_drained(accounting_db, lane, state):
    clients, generation = accounting_db
    db = clients[0]
    identity = str(uuid4())
    await _insert_legacy_cutover_work(db, lane, state, identity)
    try:
        await db.execute_raw(
            "UPDATE deltallm_accounting_protocols SET state='prepared',activated_at=NULL "
            "WHERE generation=$1",
            generation,
        )
        assert await db.query_raw("SELECT lane FROM deltallm_accounting_pending_legacy_work()") == [
            {"lane": "batch" if lane == "batch_receipt" else lane}
        ]
        with pytest.raises(Exception, match="accounting_legacy_work_pending"):
            await db.execute_raw("SELECT deltallm_activate_accounting_protocol($1)", generation)
    finally:
        await db.execute_raw(
            "DELETE FROM deltallm_spend_ingestion_outbox WHERE event_id=$1", identity
        )
        await db.execute_raw(
            "DELETE FROM deltallm_billing_operations WHERE operation_id=$1", identity
        )
        await db.execute_raw(
            "DELETE FROM deltallm_batch_completion_outbox WHERE completion_id=$1", identity
        )
        await db.execute_raw("DELETE FROM deltallm_batch_job WHERE batch_id=$1", identity)
        await db.execute_raw("DELETE FROM deltallm_batch_file WHERE file_id=$1", identity)
    assert await db.query_raw("SELECT lane FROM deltallm_accounting_pending_legacy_work()") == []
    await db.execute_raw("SELECT deltallm_activate_accounting_protocol($1)", generation)
    assert await AccountingProtocolRepository(db).protocol_ready(generation) is True


async def _insert_legacy_cutover_work(db, lane, state, identity):
    if lane == "spend":
        await db.execute_raw(
            "INSERT INTO deltallm_spend_ingestion_outbox "
            "(event_id,event_type,payload_json,status,updated_at) "
            "VALUES ($1,'spend','{}'::jsonb,$2,NOW())",
            identity,
            state,
        )
    elif lane == "selector":
        await db.execute_raw(
            "INSERT INTO deltallm_billing_operations "
            "(operation_id,owner_token,api_key,model,snapshot,selector_event_id,"
            "selector_allowance,answer_allowance,selector_state,expires_at) "
            "VALUES ($1,$1,'test-key','test-model','{}'::jsonb,$1,0,0,$2,"
            "NOW()+INTERVAL '1 minute')",
            identity,
            state,
        )
    elif lane == "batch":
        await db.execute_raw(
            "INSERT INTO deltallm_batch_file (file_id,purpose,filename,bytes,storage_key) "
            "VALUES ($1,'batch','test.jsonl',0,$1)",
            identity,
        )
        await db.execute_raw(
            "INSERT INTO deltallm_batch_job (batch_id,endpoint,status,input_file_id) "
            "VALUES ($1,'/v1/embeddings',$2::\"DeltaLLM_BatchJobStatus\",$1)",
            identity,
            state,
        )
    else:
        await db.execute_raw(
            "INSERT INTO deltallm_batch_completion_outbox "
            "(completion_id,batch_id,item_id,payload_json,status,updated_at) "
            "VALUES ($1,$1,$1,'{}'::jsonb,$2,NOW())",
            identity,
            state,
        )


async def test_active_budget_policy_changes_synchronize_authoritative_window(accounting_db):
    clients, generation = accounting_db
    db = clients[0]
    suffix = str(uuid4())
    organization_id = "org-" + suffix
    await db.execute_raw(
        "INSERT INTO deltallm_organizationtable "
        "(id,organization_id,max_budget,spend,spend_exact) "
        "VALUES ($1,$2,2.0,0.5,0.5::numeric)",
        "id-" + suffix,
        organization_id,
    )
    try:
        rows = await db.query_raw(
            "SELECT limit_exact::text AS budget,committed_exact::text AS committed,"
            "policy_generation FROM deltallm_accounting_budget_windows "
            "WHERE generation=$1 AND scope_type='organization' AND scope_id=$2 "
            "AND window_starts_at<=NOW() AND window_ends_at>NOW()",
            generation,
            organization_id,
        )
        assert rows == [
            {
                "budget": "2.000000000000000000",
                "committed": "0.500000000000000000",
                "policy_generation": 0,
            }
        ]

        await db.execute_raw(
            "UPDATE deltallm_organizationtable SET max_budget=3.0 WHERE organization_id=$1",
            organization_id,
        )
        rows = await db.query_raw(
            "SELECT limit_exact::text AS budget,policy_generation "
            "FROM deltallm_accounting_budget_windows "
            "WHERE generation=$1 AND scope_type='organization' AND scope_id=$2 "
            "AND window_starts_at<=NOW() AND window_ends_at>NOW()",
            generation,
            organization_id,
        )
        assert rows == [{"budget": "3.000000000000000000", "policy_generation": 1}]

        with pytest.raises(Exception):
            await db.execute_raw(
                "UPDATE deltallm_organizationtable SET max_budget=0.4 WHERE organization_id=$1",
                organization_id,
            )
        await db.execute_raw(
            "UPDATE deltallm_organizationtable SET max_budget=NULL WHERE organization_id=$1",
            organization_id,
        )
        rows = await db.query_raw(
            "SELECT count(*) AS active_count FROM deltallm_accounting_budget_windows "
            "WHERE generation=$1 AND scope_type='organization' AND scope_id=$2 "
            "AND window_starts_at<=NOW() AND window_ends_at>NOW()",
            generation,
            organization_id,
        )
        assert int(rows[0]["active_count"]) == 0
    finally:
        await db.execute_raw(
            "DELETE FROM deltallm_organizationtable WHERE organization_id=$1",
            organization_id,
        )
