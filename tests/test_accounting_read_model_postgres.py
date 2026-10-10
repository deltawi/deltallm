"""Native spend, audit, rollups, and the checkpoint share one fenced commit."""

import asyncio
from decimal import Decimal
import json
from uuid import uuid4

import pytest

from src.billing.accounting.permits.accounting_local_leases import LocalPermitFinalization
from src.billing.accounting.accounting_protocol import AccountingOutcome
from src.db.accounting.accounting_calls import AccountingProtocolUnavailable
from src.db.accounting.journal.accounting_journal import AccountingJournalRepository
from src.db.accounting.journal.accounting_journal_worker import AccountingJournalWorkerRepository
from src.db.accounting.reporting.accounting_read_model import AccountingReadModelRepository
from tests.accounting_read_model_fixtures import reporting_finalization, reporting_handle
from tests.test_accounting_journal_postgres import at_ordinal
from tests.test_accounting_local_leases_postgres import deadline, funded
from tests.test_accounting_permits_postgres import CountingClient
from tests.test_preissued_permit_bank import fresh
from tests.test_accounting_protocol_postgres import _window, accounting_db as _accounting_db

pytestmark = pytest.mark.postgres
accounting_db = _accounting_db


def repository(db):
    return AccountingReadModelRepository(db, statement_budget_seconds=2)


async def source(
    db,
    generation,
    *,
    count=4,
    outcome=AccountingOutcome.COMPLETED,
    padding=0,
    usage=None,
    billing=None,
    owner_account_id=None,
):
    window, item, grant = await funded(db, generation, owner_account_id=owner_account_id)
    values = []
    for ordinal in range(count):
        original = at_ordinal(fresh(item), grant, ordinal)
        terminal = reporting_finalization(reporting_handle(original.receipt), outcome)
        if usage is not None or billing is not None:
            payload = dict(terminal.spend_payload)
            if usage is not None:
                payload["usage"] = usage
            if billing is not None:
                payload["metadata"] = {**payload["metadata"], "billing": billing}
            terminal = terminal.model_copy(update={"spend_payload": payload})
        if padding:
            payload = dict(terminal.spend_payload)
            payload["metadata"] = {**payload["metadata"], "padding": "p" * padding}
            terminal = terminal.model_copy(update={"spend_payload": payload})
        values.append(LocalPermitFinalization(receipt=original.receipt, finalization=terminal))
    acceptance = AccountingJournalRepository(db, statement_budget_seconds=2)
    if padding:
        for value in values:
            await acceptance.append_batch([value], expires_at=deadline())
    else:
        await acceptance.append_batch(values, expires_at=deadline())
    journal = AccountingJournalWorkerRepository(db, statement_budget_seconds=2)
    remaining = count
    for _ in range(count):
        page = await journal.claim(generation=generation, worker_id="source", expires_at=deadline())
        remaining -= await journal.materialize(page, expires_at=deadline())
        if remaining == 0:
            break
    assert remaining == 0
    return window, values


async def next_page(repo, generation, *, worker="report", limit=256):
    return await repo.claim(
        generation=generation,
        worker_id=worker,
        limit=limit,
        lease_seconds=30,
        expires_at=deadline(),
    )


async def effects(db, generation):
    rows = await db.query_raw(
        "SELECT (SELECT count(*)::integer FROM deltallm_accounting_usage_facts_v2 WHERE protocol_generation=$1) AS facts,"
        "(SELECT count(*)::integer FROM deltallm_auditevent WHERE request_id IN "
        "(SELECT operation_id FROM deltallm_billing_operations WHERE accounting_generation=$1)) AS audits,"
        "(SELECT coalesce(sum(request_count),0)::integer FROM deltallm_accounting_usage_rollups_v2 "
        "WHERE bucket_kind='day' AND api_key IN (SELECT api_key FROM deltallm_billing_operations WHERE accounting_generation=$1)) AS rolled",
        generation,
    )
    return rows[0]


@pytest.mark.parametrize("lost", [None, "claim", "commit"])
async def test_complete_atomic_native_projection_and_lost_reply_replay(accounting_db, lost):
    clients, generation = accounting_db
    db = clients[0]
    window, values = await source(db, generation)
    counted = CountingClient(db)
    repo = repository(counted)
    await repo.initialize(generation=generation, expires_at=deadline())
    counted.lose_ack = lost == "claim"
    page = await next_page(repo, generation)
    assert len(page.sequences) == 4 and page.source_bytes <= 1_048_576
    counted.lose_ack = lost == "commit"
    if lost == "commit":
        with pytest.raises(AccountingProtocolUnavailable):
            await repo.materialize(page, expires_at=deadline())
    else:
        assert await repo.materialize(page, expires_at=deadline()) == 4
    assert await repo.materialize(page, expires_at=deadline()) == 0
    assert await next_page(repo, generation) is None
    assert await effects(db, generation) == {"facts": 4, "audits": 4, "rolled": 4}
    rows = await db.query_raw(
        "SELECT sum(spend_exact)::text AS spend,sum(provider_cost_exact)::text AS provider,"
        "sum(total_tokens)::integer AS tokens FROM deltallm_accounting_usage_facts_v2 WHERE protocol_generation=$1",
        generation,
    )
    assert Decimal(rows[0]["spend"]) == Decimal("2.4")
    assert Decimal(rows[0]["provider"]) == Decimal("1.6") and rows[0]["tokens"] == 20
    ids = await db.query_raw(
        "SELECT event_id::text AS event_id,content_stored FROM deltallm_auditevent WHERE request_id=ANY($1::text[])",
        [str(value.receipt.reservation.operation_id) for value in values],
    )
    assert {row["event_id"] for row in ids} == {
        value.finalization.audit_envelope["event_id"] for value in values
    }
    assert all(row["content_stored"] is False for row in ids)
    assert await _window(db, window) == (Decimal(0), Decimal(4), Decimal(0))
    legacy = await db.query_raw(
        "SELECT count(*)::integer AS count FROM deltallm_spendlog_events WHERE api_key=$1",
        values[0].receipt.reservation.attribution.api_key,
    )
    assert legacy == [{"count": 0}]


async def test_two_initializers_and_workers_keep_complete_cells_and_disjoint_partitions(
    accounting_db,
):
    clients, generation = accounting_db
    await source(clients[0], generation)
    first, second = [repository(client) for client in clients]
    await asyncio.gather(
        *(repo.initialize(generation=generation, expires_at=deadline()) for repo in (first, second))
    )
    pages = await asyncio.gather(
        next_page(first, generation, worker="first"), next_page(second, generation, worker="second")
    )
    owned = [page for page in pages if page is not None]
    assert len(owned) == 1
    assert await first.materialize(owned[0], expires_at=deadline()) == 4
    assert await effects(clients[0], generation) == {"facts": 4, "audits": 4, "rolled": 4}


@pytest.mark.parametrize(
    "change", ["expired", "owner", "token", "generation", "partition", "checkpoint"]
)
async def test_stale_full_fence_cannot_write_any_sink(accounting_db, change):
    clients, generation = accounting_db
    db = clients[0]
    await source(db, generation)
    repo = repository(db)
    await repo.initialize(generation=generation, expires_at=deadline())
    page = await next_page(repo, generation)
    if change == "expired":
        await db.execute_raw(
            "UPDATE deltallm_accounting_projection_checkpoints SET lease_expires_at=NOW()-INTERVAL '1 second' WHERE projection_name='accounting-read-model-v2' AND generation=$1 AND lease_token IS NOT NULL",
            generation,
        )
        changed = page
    else:
        updates = {
            "owner": {"worker_id": "other"},
            "token": {"lease_token": uuid4()},
            "generation": {"generation": generation + 1},
            "partition": {"accounting_partition": (page.accounting_partition + 1) % 4},
            "checkpoint": {"after_sequence": 1},
        }
        changed = page.model_copy(update=updates[change])
    assert await repo.materialize(changed, expires_at=deadline()) == 0
    assert await effects(db, generation) == {"facts": 0, "audits": 0, "rolled": 0}


@pytest.mark.parametrize(
    "change",
    [
        "spend_attribution",
        "audit_attribution",
        "charge",
        "usage",
        "audit_content",
        "audit_id_collision",
        "fact_identity",
    ],
)
async def test_one_bad_record_rolls_back_facts_audit_rollups_and_checkpoint(accounting_db, change):
    clients, generation = accounting_db
    db = clients[0]
    _, values = await source(db, generation)
    repo = repository(db)
    await repo.initialize(generation=generation, expires_at=deadline())
    page = await next_page(repo, generation)
    preexisting = {"facts": 0, "audits": 0, "rolled": 0}
    if change == "audit_id_collision":
        await db.execute_raw(
            "INSERT INTO deltallm_auditevent(event_id,action,status,actor_type) VALUES ($1::uuid,'wrong','success','system')",
            values[-1].finalization.audit_envelope["event_id"],
        )
    elif change == "fact_identity":
        assert await repo.materialize(page, expires_at=deadline()) == 4
        await db.execute_raw(
            "UPDATE deltallm_accounting_projection_checkpoints SET last_sequence=0,lease_owner=NULL,lease_token=NULL,lease_expires_at=NULL WHERE projection_name='accounting-read-model-v2' AND generation=$1",
            generation,
        )
        await db.execute_raw(
            "UPDATE deltallm_accounting_usage_facts_v2 SET model='wrong' WHERE accounting_sequence=$1",
            page.sequences[-1],
        )
        page = await next_page(repo, generation)
        preexisting = {"facts": 4, "audits": 4, "rolled": 4}
    else:
        column, path, value = {
            "spend_attribution": ("payload_json", "{spend,organization_id}", "other-tenant"),
            "audit_attribution": (
                "audit_envelope_json",
                "{redacted_payload,event,organization_id}",
                "other-tenant",
            ),
            "charge": ("payload_json", "{spend,cost_exact}", "0.7"),
            "usage": ("payload_json", "{spend,usage,prompt_tokens}", -1),
            "audit_content": ("audit_envelope_json", "{redacted_payload,payloads}", ["private"]),
        }[change]
        # Both identifiers are a fixed test allowlist, never request input.
        await db.execute_raw(
            f"UPDATE deltallm_accounting_events SET {column}=jsonb_set({column},$2::text[],$3::jsonb) WHERE sequence=$1",
            page.sequences[-1],
            path.strip("{}").split(","),
            json.dumps(value),
        )
    with pytest.raises(AccountingProtocolUnavailable):
        await repo.materialize(page, expires_at=deadline())
    assert await effects(db, generation) == preexisting
    checkpoint = await db.query_raw(
        "SELECT last_sequence,lease_token FROM deltallm_accounting_projection_checkpoints WHERE projection_name='accounting-read-model-v2' AND generation=$1 AND accounting_partition=$2",
        generation,
        page.accounting_partition,
    )
    assert checkpoint == [{"last_sequence": 0, "lease_token": str(page.lease_token)}]


@pytest.mark.parametrize("outcome", [AccountingOutcome.NOT_DISPATCHED, AccountingOutcome.UNCERTAIN])
async def test_release_and_uncertain_events_have_audit_but_no_success_fact(accounting_db, outcome):
    clients, generation = accounting_db
    db = clients[0]
    await source(db, generation, outcome=outcome)
    repo = repository(db)
    await repo.initialize(generation=generation, expires_at=deadline())
    assert await repo.materialize(await next_page(repo, generation), expires_at=deadline()) == 4
    assert await effects(db, generation) == {"facts": 0, "audits": 4, "rolled": 0}


async def test_payload_byte_cap_returns_only_small_keys_and_advances_without_skips(accounting_db):
    clients, generation = accounting_db
    db = clients[0]
    await source(db, generation, padding=260_500)
    repo = repository(db)
    await repo.initialize(generation=generation, expires_at=deadline())
    first = await next_page(repo, generation)
    assert 1 <= len(first.sequences) < 4 and first.source_bytes <= 1_048_576
    assert (
        len(first.model_dump_json().encode()) < 16_384 and "padding" not in first.model_dump_json()
    )
    assert await repo.materialize(first, expires_at=deadline()) == len(first.sequences)
    second = await next_page(repo, generation)
    assert min(second.sequences) > max(first.sequences)
    assert len(first.sequences) + len(second.sequences) == 4
    assert await repo.materialize(second, expires_at=deadline()) == len(second.sequences)
    assert await effects(db, generation) == {"facts": 4, "audits": 4, "rolled": 4}


async def test_prefix_must_not_skip_the_first_eligible_event(accounting_db):
    clients, generation = accounting_db
    db = clients[0]
    await source(db, generation)
    repo = repository(db)
    await repo.initialize(generation=generation, expires_at=deadline())
    page = await next_page(repo, generation)
    skipped = page.model_copy(update={"sequences": page.sequences[1:]})
    with pytest.raises(AccountingProtocolUnavailable):
        await repo.materialize(skipped, expires_at=deadline())
    assert await effects(db, generation) == {"facts": 0, "audits": 0, "rolled": 0}
