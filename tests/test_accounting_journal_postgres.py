"""Real terminal acceptance remains funded through lost replies and grant expiry."""

import asyncio
from decimal import Decimal
import json
from uuid import uuid4

import pytest
from prisma.errors import RawQueryError

from src.billing.accounting.journal.accounting_journal import journal_batch
from src.billing.accounting.permits.accounting_local_leases import (
    LocalPermitReturn,
    LocalPermitFinalization,
)
from src.billing.accounting.accounting_protocol import PreissuedPermitAllocation
from src.db.accounting.journal.accounting_journal import AccountingJournalRepository
from src.db.accounting.accounting_calls import AccountingProtocolUnavailable
from tests.test_accounting_local_leases_postgres import deadline, funded, owner, terminal
from tests.test_accounting_permits_postgres import CountingClient
from tests.test_preissued_permit_bank import fresh
from tests.test_accounting_protocol_postgres import (
    _window,
    _settle_grants,
    accounting_db as _accounting_db,
)

pytestmark = pytest.mark.postgres
accounting_db = _accounting_db


async def counts(db, generation):
    rows = await db.query_raw(
        "SELECT (SELECT count(*)::integer FROM deltallm_accounting_terminal_journal WHERE generation=$1) AS journal,"
        "(SELECT count(*)::integer FROM deltallm_accounting_terminal_payloads p JOIN deltallm_accounting_terminal_journal j ON j.sequence=p.journal_sequence WHERE j.generation=$1) AS payloads,"
        "(SELECT count(*)::integer FROM deltallm_billing_operations WHERE accounting_generation=$1) AS operations,"
        "(SELECT sum(pending_entries)::integer FROM deltallm_accounting_terminal_capacity WHERE generation=$1) AS charged",
        generation,
    )
    return rows[0]


@pytest.mark.parametrize("lost", [False, True])
async def test_one_append_recovers_lost_ack_and_keeps_reserved_capacity(accounting_db, lost):
    clients, generation = accounting_db
    db = clients[0]
    window, item, grant = await funded(db, generation)
    value = terminal(item, grant)
    counted = CountingClient(db, lose_ack=lost)
    repo = AccountingJournalRepository(counted, statement_budget_seconds=2)
    accepted = (await repo.append_batch([value], expires_at=deadline()))[0]
    assert accepted.replayed is lost and counted.calls == 1 + lost
    assert await counts(db, generation) == {
        "journal": 1,
        "payloads": 1,
        "operations": 0,
        "charged": 1,
    }
    assert await _window(db, window) == (Decimal(0), Decimal(4), Decimal(0))
    assert await owner(db).return_batch(
        [LocalPermitReturn(grant=grant, first_unused_ordinal=1)], expires_at=deadline()
    ) == [3]
    await db.execute_raw(
        "UPDATE deltallm_accounting_grants SET state='draining',dispatch_expires_at=NOW()-INTERVAL '2 seconds',expires_at=NOW()-INTERVAL '1 second' WHERE grant_id=$1",
        grant.grant_id,
    )
    assert await _settle_grants(db, generation) == 0
    replay = (await repo.append_batch([value], expires_at=deadline()))[0]
    assert replay.replayed and replay.journal_sequence == accepted.journal_sequence
    assert await _window(db, window) == (Decimal(0), Decimal(4), Decimal(0))


async def test_changed_documents_reject_and_preserve_original_journal(accounting_db):
    clients, generation = accounting_db
    db = clients[0]
    _, item, grant = await funded(db, generation)
    value = terminal(item, grant)
    repo = AccountingJournalRepository(db, statement_budget_seconds=2)
    await repo.append_batch([value], expires_at=deadline())
    changed = value.model_copy(
        update={
            "finalization": value.finalization.model_copy(
                update={"audit_envelope": {"changed": True}}
            )
        }
    )
    with pytest.raises(AccountingProtocolUnavailable):
        await repo.append_batch([changed], expires_at=deadline())
    assert await counts(db, generation) == {
        "journal": 1,
        "payloads": 1,
        "operations": 0,
        "charged": 1,
    }


async def test_duplicate_concurrent_append_returns_one_durable_identity(accounting_db):
    clients, generation = accounting_db
    _, item, grant = await funded(clients[0], generation)
    value = terminal(item, grant)
    results = await asyncio.gather(
        *(
            AccountingJournalRepository(client, statement_budget_seconds=2).append_batch(
                [value], expires_at=deadline()
            )
            for client in clients
        )
    )
    assert len({result[0].journal_sequence for result in results}) == 1
    assert sorted(result[0].replayed for result in results) == [False, True]
    assert (await counts(clients[0], generation))["journal"] == 1


@pytest.mark.parametrize(
    "missing",
    [
        "operation_id",
        "grant_id",
        "grantee_id",
        "fence_token",
        "permit_ordinal",
        "allowance_exact",
        "outcome",
        "expires_at",
        "reservation_sha256",
        "finalization_sha256",
        "subject",
    ],
)
async def test_missing_sql_identity_rejects_without_partial_append(accounting_db, missing):
    clients, generation = accounting_db
    db = clients[0]
    _, item, grant = await funded(db, generation)
    batch = journal_batch([terminal(item, grant)])
    compact = json.loads(batch.compact)
    compact[0].pop(missing)
    with pytest.raises(RawQueryError):
        await db.query_raw(
            "SELECT * FROM deltallm_accounting_append_terminal_journal($1,$2::jsonb,$3::text[],$4::text[])",
            generation,
            json.dumps(compact),
            list(batch.reservations),
            list(batch.finalizations),
        )
    assert (await counts(db, generation))["journal"] == 0


async def test_return_cannot_include_an_accepted_ordinal(accounting_db):
    clients, generation = accounting_db
    db = clients[0]
    _, item, grant = await funded(db, generation)
    await AccountingJournalRepository(db, statement_budget_seconds=2).append_batch(
        [terminal(item, grant)], expires_at=deadline()
    )
    with pytest.raises(AccountingProtocolUnavailable):
        await owner(db).return_batch(
            [LocalPermitReturn(grant=grant, first_unused_ordinal=0)], expires_at=deadline()
        )
    assert (await counts(db, generation))["charged"] == 1


def at_ordinal(item, grant, ordinal):
    value = terminal(item, grant)
    return LocalPermitFinalization(
        receipt=value.receipt.model_copy(update={"permit_ordinal": ordinal}),
        finalization=value.finalization,
    )


async def test_entry_capacity_rejects_a_new_item_without_losing_exact_replay(accounting_db):
    clients, generation = accounting_db
    db = clients[0]
    _, item, grant = await funded(db, generation)
    value = terminal(item, grant)
    repo = AccountingJournalRepository(db, statement_budget_seconds=2)
    accepted = (await repo.append_batch([value], expires_at=deadline()))[0]
    await db.execute_raw(
        "UPDATE deltallm_accounting_terminal_capacity SET max_entries=1 WHERE generation=$1",
        generation,
    )
    with pytest.raises(AccountingProtocolUnavailable):
        await repo.append_batch([value, at_ordinal(fresh(item), grant, 1)], expires_at=deadline())
    replay = (await repo.append_batch([value], expires_at=deadline()))[0]
    assert replay.replayed and replay.journal_sequence == accepted.journal_sequence
    assert await counts(db, generation) == {
        "journal": 1,
        "payloads": 1,
        "operations": 0,
        "charged": 1,
    }


async def test_byte_capacity_rejects_a_whole_new_batch_and_keeps_documents(accounting_db):
    clients, generation = accounting_db
    db = clients[0]
    _, item, initial = await funded(db, generation)
    # Return the initial never-issued grant; a single eight-slot grant puts all
    # payloads in the same capacity partition, without a random partition choice.
    await owner(db).return_batch(
        [LocalPermitReturn(grant=initial, first_unused_ordinal=0)], expires_at=deadline()
    )
    assert await _settle_grants(db, generation) == 1
    grant = (
        await owner(db).allocate_batch(
            [PreissuedPermitAllocation(reservation=item, fence_token=uuid4(), target_operations=8)],
            expires_at=deadline(),
        )
    )[0]
    await db.execute_raw(
        "INSERT INTO deltallm_accounting_terminal_capacity(generation,accounting_partition,max_entries,max_bytes) VALUES ($1,$2,1000,1048576)",
        generation,
        grant.accounting_partition,
    )
    values = [at_ordinal(fresh(item), grant, ordinal) for ordinal in range(6)]
    for value in values:
        value.finalization.spend_payload["large"] = "x" * 250000
    repo = AccountingJournalRepository(db, statement_budget_seconds=2)
    await repo.append_batch(values[:4], expires_at=deadline())
    before = await db.query_raw(
        "SELECT pending_entries,pending_bytes FROM deltallm_accounting_terminal_capacity WHERE generation=$1",
        generation,
    )
    assert before[0]["pending_entries"] == 4 and before[0]["pending_bytes"] <= 1048576
    with pytest.raises(AccountingProtocolUnavailable):
        await repo.append_batch(values[4:], expires_at=deadline())
    assert (
        await db.query_raw(
            "SELECT pending_entries,pending_bytes FROM deltallm_accounting_terminal_capacity WHERE generation=$1",
            generation,
        )
        == before
    )
    assert await counts(db, generation) == {
        "journal": 4,
        "payloads": 4,
        "operations": 0,
        "charged": 4,
    }


async def test_return_race_has_one_authoritative_winner(accounting_db):
    clients, generation = accounting_db
    _, item, grant = await funded(clients[0], generation)
    result = await asyncio.gather(
        AccountingJournalRepository(clients[0], statement_budget_seconds=2).append_batch(
            [terminal(item, grant)], expires_at=deadline()
        ),
        owner(clients[1]).return_batch(
            [LocalPermitReturn(grant=grant, first_unused_ordinal=0)], expires_at=deadline()
        ),
        return_exceptions=True,
    )
    assert sum(isinstance(value, AccountingProtocolUnavailable) for value in result) == 1
    state = await clients[0].query_raw(
        "SELECT returned_operations FROM deltallm_accounting_grants WHERE grant_id=$1",
        grant.grant_id,
    )
    accepted = (await counts(clients[0], generation))["journal"]
    assert (accepted, state[0]["returned_operations"]) in {(1, 0), (0, 4)}


async def test_expiry_transition_does_not_update_more_than_the_worker_limit(accounting_db):
    clients, generation = accounting_db
    db = clients[0]
    grants = [(await funded(db, generation))[2] for _ in range(3)]
    await db.execute_raw(
        "UPDATE deltallm_accounting_grants SET dispatch_expires_at=NOW()-INTERVAL '2 seconds',expires_at=NOW()-INTERVAL '1 second' WHERE grant_id=ANY($1::text[])",
        [grant.grant_id for grant in grants],
    )
    for expected in (1, 2, 3):
        result = await db.query_raw(
            "SELECT deltallm_accounting_reconcile_grants($1,1) AS closed", generation
        )
        assert result[0]["closed"] == 1
        rows = await db.query_raw(
            "SELECT state,count(*)::integer AS count FROM deltallm_accounting_grants WHERE generation=$1 GROUP BY state",
            generation,
        )
        states = {row["state"]: row["count"] for row in rows}
        assert states.get("closed", 0) == expected
        assert states.get("active", 0) == 3 - expected
        assert states.get("draining", 0) == 0
