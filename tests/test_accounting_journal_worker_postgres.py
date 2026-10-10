"""Native canonical processing is fenced, exact, and atomic with capacity release."""

import asyncio
from decimal import Decimal
import os

import asyncpg
import pytest
from prisma.errors import RawQueryError

from src.billing.accounting.journal.accounting_journal_claims import JournalFailure
from src.billing.accounting.permits.accounting_local_leases import LocalPermitReturn
from src.billing.accounting.accounting_protocol import AccountingOutcome
from src.db.accounting.journal.accounting_journal import AccountingJournalRepository
from src.db.accounting.journal.accounting_journal_worker import AccountingJournalWorkerRepository
from src.db.accounting.accounting_calls import AccountingProtocolUnavailable
from tests.test_accounting_journal_postgres import at_ordinal, counts
from tests.test_accounting_local_leases_postgres import deadline, funded, owner
from tests.test_accounting_permits_postgres import CountingClient
from tests.test_preissued_permit_bank import fresh
from tests.test_accounting_protocol_postgres import (
    _create_window,
    _reservation,
    _window,
    _settle_grants,
    _finalization,
    accounting_db as _accounting_db,
)

pytestmark = pytest.mark.postgres
accounting_db = _accounting_db


async def pending(db, generation, *, count=4, outcome=AccountingOutcome.COMPLETED):
    window, item, grant = await funded(db, generation)
    values = [at_ordinal(fresh(item), grant, ordinal) for ordinal in range(count)]
    values = [
        value.model_copy(update={"finalization": _finalization(value.receipt.reservation, outcome)})
        for value in values
    ]
    accepted = await AccountingJournalRepository(db, statement_budget_seconds=2).append_batch(
        values, expires_at=deadline()
    )
    return window, grant, values, accepted


def worker(db):
    return AccountingJournalWorkerRepository(db, statement_budget_seconds=2)


@pytest.mark.parametrize("phase", [None, "claim", "materialize"])
async def test_native_bulk_commit_lost_replies_and_exact_closed_replay(accounting_db, phase):
    clients, generation = accounting_db
    db = clients[0]
    window, grant, values, accepted = await pending(db, generation)
    counted = CountingClient(db, lose_ack=phase == "claim")
    repo = worker(counted)
    claim = await repo.claim(generation=generation, worker_id="worker", expires_at=deadline())
    assert set(claim.sequences) == {ack.journal_sequence for ack in accepted}
    counted.lose_ack = phase == "materialize"
    assert await repo.materialize(claim, expires_at=deadline()) == 4
    assert counted.calls == 2 + (phase is not None)
    assert await counts(db, generation) == {
        "journal": 4,
        "payloads": 0,
        "operations": 4,
        "charged": 0,
    }
    assert await _settle_grants(db, generation) == 1
    assert await _window(db, window) == (Decimal("2.4"), Decimal(0), Decimal(0))
    assert await repo.materialize(claim, expires_at=deadline()) == 4
    replay = await AccountingJournalRepository(db, statement_budget_seconds=2).append_batch(
        values, expires_at=deadline()
    )
    assert [ack.journal_sequence for ack in replay] == [ack.journal_sequence for ack in accepted]
    assert all(ack.replayed for ack in replay)
    rows = await db.query_raw(
        "SELECT count(*)::integer AS count FROM deltallm_accounting_events WHERE generation=$1",
        generation,
    )
    assert rows[0]["count"] == 4
    assert await _window(db, window) == (Decimal("2.4"), Decimal(0), Decimal(0))


@pytest.mark.parametrize("outcome", [AccountingOutcome.NOT_DISPATCHED, AccountingOutcome.UNCERTAIN])
async def test_release_and_uncertain_money_match_existing_accounting(accounting_db, outcome):
    clients, generation = accounting_db
    db = clients[0]
    window, _, _, _ = await pending(db, generation, outcome=outcome)
    repo = worker(db)
    claim = await repo.claim(generation=generation, worker_id="worker", expires_at=deadline())
    assert await repo.materialize(claim, expires_at=deadline()) == 4
    assert await _settle_grants(db, generation) == 1
    assert await _window(db, window) == (
        Decimal(0),
        Decimal(0),
        Decimal(4 if outcome is AccountingOutcome.UNCERTAIN else 0),
    )


async def test_stale_worker_cannot_commit_or_fail_a_reclaimed_batch(accounting_db):
    clients, generation = accounting_db
    db = clients[0]
    window, _, _, _ = await pending(db, generation)
    first, second = worker(db), worker(clients[1])
    old = await first.claim(generation=generation, worker_id="old", expires_at=deadline())
    await db.execute_raw(
        "UPDATE deltallm_accounting_terminal_journal SET lease_expires_at=NOW()-INTERVAL '1 second' WHERE sequence=ANY($1::bigint[])",
        list(old.sequences),
    )
    new = await second.claim(generation=generation, worker_id="new", expires_at=deadline())
    assert new.sequences == old.sequences and new.lease_token != old.lease_token
    assert await first.materialize(old, expires_at=deadline()) == 0
    assert await first.fail(old, JournalFailure.PERSISTENCE, expires_at=deadline()) == 0
    assert (await counts(db, generation))["charged"] == 4
    assert await second.materialize(new, expires_at=deadline()) == 4
    assert await _settle_grants(db, generation) == 1
    assert await _window(db, window) == (Decimal("2.4"), Decimal(0), Decimal(0))


async def test_two_workers_claim_disjoint_entries_and_commit_one_result_each(accounting_db):
    clients, generation = accounting_db
    db = clients[0]
    await pending(db, generation)
    repos = [worker(client) for client in clients]
    claims = await asyncio.gather(
        *(
            repo.claim(
                generation=generation, worker_id=f"worker-{index}", limit=2, expires_at=deadline()
            )
            for index, repo in enumerate(repos)
        )
    )
    assert len(claims[0].sequences) == len(claims[1].sequences) == 2
    assert set(claims[0].sequences).isdisjoint(claims[1].sequences)
    assert await asyncio.gather(
        *(
            repo.materialize(claim, expires_at=deadline())
            for repo, claim in zip(repos, claims, strict=True)
        )
    ) == [2, 2]
    assert await counts(db, generation) == {
        "journal": 4,
        "payloads": 0,
        "operations": 4,
        "charged": 0,
    }


async def test_corrupt_batch_rolls_back_and_failed_records_keep_all_charges(accounting_db):
    clients, generation = accounting_db
    db = clients[0]
    await pending(db, generation)
    repo = worker(db)
    for attempt in range(1, 6):
        claim = await repo.claim(generation=generation, worker_id="worker", expires_at=deadline())
        if attempt == 1:
            await db.execute_raw(
                "UPDATE deltallm_accounting_terminal_payloads SET finalization_payload='{}' WHERE journal_sequence=$1",
                claim.sequences[-1],
            )
        with pytest.raises(AccountingProtocolUnavailable):
            await repo.materialize(claim, expires_at=deadline())
        assert await repo.fail(claim, JournalFailure.INVALID_PAYLOAD, expires_at=deadline()) == 4
        assert await counts(db, generation) == {
            "journal": 4,
            "payloads": 4,
            "operations": 0,
            "charged": 4,
        }
    assert (
        await repo.claim(generation=generation, worker_id="worker", expires_at=deadline())
    ).sequences == ()
    state = await db.query_raw(
        "SELECT pending_entries,failed_entries FROM deltallm_accounting_terminal_capacity WHERE generation=$1",
        generation,
    )
    assert state[0]["pending_entries"] == state[0]["failed_entries"] == 4
    assert await _settle_grants(db, generation) == 0


async def test_accepted_result_can_materialize_after_grant_expiry(accounting_db):
    clients, generation = accounting_db
    db = clients[0]
    window, grant, _, _ = await pending(db, generation, count=1)
    await owner(db).return_batch(
        [LocalPermitReturn(grant=grant, first_unused_ordinal=1)], expires_at=deadline()
    )
    await db.execute_raw(
        "UPDATE deltallm_accounting_grants SET state='draining',dispatch_expires_at=NOW()-INTERVAL '2 seconds',expires_at=NOW()-INTERVAL '1 second' WHERE grant_id=$1",
        grant.grant_id,
    )
    assert await _settle_grants(db, generation) == 0
    repo = worker(db)
    claim = await repo.claim(generation=generation, worker_id="worker", expires_at=deadline())
    assert await repo.materialize(claim, expires_at=deadline()) == 1
    assert await _settle_grants(db, generation) == 1
    assert await _window(db, window) == (Decimal("0.6"), Decimal(0), Decimal(0))


async def test_claim_byte_limit_splits_large_payloads_without_releasing_capacity(accounting_db):
    clients, generation = accounting_db
    db = clients[0]
    _, item, grant = await funded(db, generation)
    values = [at_ordinal(fresh(item), grant, ordinal) for ordinal in range(4)]
    for value in values:
        large = value.model_copy(
            update={
                "finalization": value.finalization.model_copy(
                    update={
                        "audit_envelope": {"data": "x" * 50_000},
                        "spend_payload": {"data": "x" * 250_000},
                    }
                )
            }
        )
        await AccountingJournalRepository(db, statement_budget_seconds=2).append_batch(
            [large], expires_at=deadline()
        )
    repo = worker(db)
    first = await repo.claim(generation=generation, worker_id="first", expires_at=deadline())
    assert len(first.sequences) == 3
    rows = await db.query_raw(
        "SELECT sum(payload_bytes)::bigint AS bytes FROM deltallm_accounting_terminal_journal WHERE sequence=ANY($1::bigint[])",
        list(first.sequences),
    )
    assert rows[0]["bytes"] <= 1_048_576
    assert (await counts(db, generation))["charged"] == 4
    assert await repo.materialize(first, expires_at=deadline()) == 3
    second = await repo.claim(generation=generation, worker_id="second", expires_at=deadline())
    assert len(second.sequences) == 1
    assert await repo.materialize(second, expires_at=deadline()) == 1
    assert (await counts(db, generation))["charged"] == 0


async def test_repeated_worker_crashes_keep_failed_records_funded(accounting_db):
    clients, generation = accounting_db
    db = clients[0]
    await pending(db, generation)
    repo = worker(db)
    for attempt in range(5):
        claim = await repo.claim(
            generation=generation, worker_id=f"crash-{attempt}", expires_at=deadline()
        )
        assert len(claim.sequences) == 4
        await db.execute_raw(
            "UPDATE deltallm_accounting_terminal_journal SET lease_expires_at=NOW()-INTERVAL '1 second' WHERE sequence=ANY($1::bigint[])",
            list(claim.sequences),
        )
    assert not (
        await repo.claim(generation=generation, worker_id="replacement", expires_at=deadline())
    ).sequences
    rows = await db.query_raw(
        "SELECT pending_entries,failed_entries,pending_bytes FROM deltallm_accounting_terminal_capacity WHERE generation=$1",
        generation,
    )
    assert rows[0]["pending_entries"] == rows[0]["failed_entries"] == 4
    assert rows[0]["pending_bytes"] > 0
    assert (await counts(db, generation))["operations"] == 0
    assert await _settle_grants(db, generation) == 0


async def test_cancellation_after_commit_recovers_without_a_second_financial_write(accounting_db):
    clients, generation = accounting_db
    db = clients[0]
    await pending(db, generation)
    claim = await worker(db).claim(generation=generation, worker_id="worker", expires_at=deadline())

    class CancelAfterCommit:
        async def query_raw(self, query, *parameters):
            await db.query_raw(query, *parameters)
            raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await worker(CancelAfterCommit()).materialize(claim, expires_at=deadline())
    assert (await counts(db, generation))["operations"] == 4
    assert await worker(db).materialize(claim, expires_at=deadline()) == 4
    assert await counts(db, generation) == {
        "journal": 4,
        "payloads": 0,
        "operations": 4,
        "charged": 0,
    }


@pytest.mark.parametrize("keys", [[], [None], [0], [-1], [1, 1], [1] * 257])
@pytest.mark.parametrize("phase", ["materialize", "fail"])
async def test_native_malformed_keys_reject_before_any_worker_transition(
    accounting_db, keys, phase
):
    from uuid import uuid4

    clients, generation = accounting_db
    db = clients[0]
    await pending(db, generation)
    query = (
        "SELECT deltallm_accounting_materialize_terminal_journal($1,$2,$3::uuid,$4::bigint[])"
        if phase == "materialize"
        else "SELECT deltallm_accounting_fail_terminal_journal($1,$2,$3::uuid,$4::bigint[],'invalid_payload')"
    )
    with pytest.raises(RawQueryError):
        await db.query_raw(query, generation, "worker", str(uuid4()), keys)
    assert await counts(db, generation) == {
        "journal": 4,
        "payloads": 4,
        "operations": 0,
        "charged": 4,
    }


async def test_lease_expiry_while_waiting_for_grant_lock_rolls_back_every_effect(accounting_db):
    clients, generation = accounting_db
    db = clients[0]
    _, grant, _, _ = await pending(db, generation)
    repo = worker(db)
    claim = await repo.claim(generation=generation, worker_id="worker", expires_at=deadline())
    await db.execute_raw(
        "UPDATE deltallm_accounting_terminal_journal SET lease_expires_at=clock_timestamp()+INTERVAL '1 second' WHERE sequence=ANY($1::bigint[])",
        list(claim.sequences),
    )
    connection = await asyncpg.connect(os.environ["DATABASE_URL"], timeout=5, command_timeout=5)
    task = None
    try:
        async with connection.transaction():
            await connection.fetchrow(
                "SELECT grant_id FROM deltallm_accounting_grants WHERE grant_id=$1 FOR UPDATE",
                grant.grant_id,
            )
            task = asyncio.create_task(repo.materialize(claim, expires_at=deadline()))
            blocker = await connection.fetchval("SELECT pg_backend_pid()")
            async with asyncio.timeout(0.5):
                while not await connection.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE $1::integer=ANY(pg_blocking_pids(pid)))",
                    blocker,
                ):
                    await asyncio.sleep(0.005)
            async with asyncio.timeout(1):
                while not await connection.fetchval(
                    "SELECT clock_timestamp()>=min(lease_expires_at) FROM deltallm_accounting_terminal_journal WHERE sequence=ANY($1::bigint[])",
                    list(claim.sequences),
                ):
                    await asyncio.sleep(0.005)
        with pytest.raises(AccountingProtocolUnavailable):
            await task
        assert await counts(db, generation) == {
            "journal": 4,
            "payloads": 4,
            "operations": 0,
            "charged": 4,
        }
    finally:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await connection.close(timeout=5)


@pytest.mark.parametrize("charge", [Decimal(0), Decimal("0.123456789123456789")])
async def test_zero_and_fractional_charge_preserve_unresolved_provisional_state(
    accounting_db, charge
):
    from uuid import uuid4

    from tests.test_accounting_local_leases_postgres import allocation

    clients, generation = accounting_db
    db = clients[0]
    window = str(uuid4())
    await _create_window(db, generation, window)
    item = _reservation(generation, window, allowance="0.3")
    grant = (await owner(db).allocate_batch([allocation(item)], expires_at=deadline()))[0]
    value = at_ordinal(item, grant, 0)
    value = value.model_copy(
        update={
            "finalization": value.finalization.model_copy(
                update={"exact_charge": charge, "unresolved_attempts": 1}
            )
        }
    )
    await AccountingJournalRepository(db, statement_budget_seconds=2).append_batch(
        [value], expires_at=deadline()
    )
    await owner(db).return_batch(
        [LocalPermitReturn(grant=grant, first_unused_ordinal=1)], expires_at=deadline()
    )
    repo = worker(db)
    claim = await repo.claim(generation=generation, worker_id="worker", expires_at=deadline())
    assert await repo.materialize(claim, expires_at=deadline()) == 1
    assert await _settle_grants(db, generation) == 1
    assert await _window(db, window) == (charge, Decimal(0), Decimal("0.3") - charge)
    rows = await db.query_raw(
        "SELECT accounting_state FROM deltallm_billing_operations WHERE operation_id=$1",
        str(item.operation_id),
    )
    assert rows[0]["accounting_state"] == "provisional"


async def test_ordinary_claim_does_not_lock_unchanged_capacity(accounting_db):
    clients, generation = accounting_db
    db = clients[0]
    await pending(db, generation)
    connection = await asyncpg.connect(os.environ["DATABASE_URL"], timeout=5, command_timeout=5)
    try:
        async with connection.transaction():
            await connection.fetch(
                "SELECT accounting_partition FROM deltallm_accounting_terminal_capacity "
                "WHERE protocol_name='primary' AND generation=$1 "
                "ORDER BY accounting_partition FOR UPDATE",
                generation,
            )
            repo = AccountingJournalWorkerRepository(db, statement_budget_seconds=0.25)
            claim = await repo.claim(
                generation=generation, worker_id="capacity-independent", expires_at=deadline()
            )
            assert len(claim.sequences) == 4
            capacity = await connection.fetchrow(
                "SELECT pending_entries,failed_entries FROM deltallm_accounting_terminal_capacity "
                "WHERE protocol_name='primary' AND generation=$1",
                generation,
            )
            assert capacity["pending_entries"] == 4
            assert capacity["failed_entries"] == 0
        assert await repo.materialize(claim, expires_at=deadline()) == 4
        assert (await counts(db, generation))["charged"] == 0
    finally:
        await connection.close(timeout=5)


@pytest.mark.parametrize("exhausted", [1, 4])
async def test_exhausted_claim_still_locks_capacity_before_atomic_failure(accounting_db, exhausted):
    clients, generation = accounting_db
    db = clients[0]
    _, _, _, accepted = await pending(db, generation)
    failed_sequences = [ack.journal_sequence for ack in accepted[:exhausted]]
    await db.execute_raw(
        "UPDATE deltallm_accounting_terminal_journal SET attempts=5 WHERE sequence=ANY($1::bigint[])",
        failed_sequences,
    )
    connection = await asyncpg.connect(os.environ["DATABASE_URL"], timeout=5, command_timeout=5)
    task = None
    try:
        async with connection.transaction():
            await connection.fetch(
                "SELECT accounting_partition FROM deltallm_accounting_terminal_capacity "
                "WHERE protocol_name='primary' AND generation=$1 "
                "ORDER BY accounting_partition FOR UPDATE",
                generation,
            )
            task = asyncio.create_task(
                worker(db).claim(
                    generation=generation, worker_id="failure-counter", expires_at=deadline()
                )
            )
            blocker = await connection.fetchval("SELECT pg_backend_pid()")
            async with asyncio.timeout(1):
                while not await connection.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM pg_stat_activity "
                    "WHERE $1::integer=ANY(pg_blocking_pids(pid)))",
                    blocker,
                ):
                    await asyncio.sleep(0.005)
            assert not task.done()
            assert (
                await connection.fetchval(
                    "SELECT failed_entries FROM deltallm_accounting_terminal_capacity "
                    "WHERE protocol_name='primary' AND generation=$1",
                    generation,
                )
                == 0
            )
        claim = await task
        assert len(claim.sequences) == 4 - exhausted
        rows = await db.query_raw(
            "SELECT pending_entries,failed_entries FROM deltallm_accounting_terminal_capacity "
            "WHERE protocol_name='primary' AND generation=$1",
            generation,
        )
        assert rows[0]["pending_entries"] == 4
        assert rows[0]["failed_entries"] == exhausted
        if claim.sequences:
            assert await worker(db).materialize(claim, expires_at=deadline()) == 4 - exhausted
        assert (await counts(db, generation))["charged"] == exhausted
        assert await _settle_grants(db, generation) == 0
    finally:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await connection.close(timeout=5)
