"""Native reporting keeps exact costs, scopes, and retained legacy history."""

from datetime import datetime
from dataclasses import replace
from decimal import Decimal
import json

import pytest

from src.billing.spend.spend_preparation import prepare_spend_event
from src.billing.spend.spend_read import SPEND_READ_SOURCE
from src.services.spend_visibility import SpendVisibility, apply_spend_visibility
from src.db.organization_deletion_repository import OrganizationDeletionRepository
from tests.test_accounting_local_leases_postgres import deadline
from tests.test_accounting_protocol_postgres import accounting_db as _accounting_db
from tests.test_accounting_read_model_postgres import next_page, repository, source

pytestmark = pytest.mark.postgres
accounting_db = _accounting_db


async def projected(db, generation, **kwargs):
    _, values = await source(db, generation, **kwargs)
    repo = repository(db)
    await repo.initialize(generation=generation, expires_at=deadline())
    page = await next_page(repo, generation)
    assert await repo.materialize(page, expires_at=deadline()) == len(values)
    return values


@pytest.mark.parametrize(
    ("usage", "billing"),
    [
        ({}, {"billing_unit": "token"}),
        (
            {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
            {"billing_unit": "token"},
        ),
        (
            {},
            {
                "billing_unit": "second",
                "usage_snapshot": {
                    "prompt_tokens": 7,
                    "completion_tokens": 4,
                    "prompt_tokens_cached": 2,
                    "completion_tokens_cached": 1,
                    "input_audio_tokens": 3,
                    "output_audio_tokens": 2,
                    "input_characters": 12,
                    "output_characters": 5,
                    "duration_seconds": 1.25,
                    "image_count": 2,
                    "rerank_units": 3,
                },
                "pricing_fields_used": ["input_cost_per_token"],
            },
        ),
    ],
)
async def test_native_view_matches_the_shared_legacy_report_contract(
    accounting_db,
    usage,
    billing,
):
    clients, generation = accounting_db
    db = clients[0]
    values = await projected(db, generation, count=1, usage=usage, billing=billing)
    finalization = values[0].finalization
    payload = dict(finalization.spend_payload)
    legacy = prepare_spend_event(
        event_id=finalization.event_id, event_type="spend", payload=payload
    )
    result = await db.query_raw(
        "SELECT to_jsonb(e)||jsonb_build_object("
        "'spend_exact',e.spend_exact::text,'provider_cost_exact',e.provider_cost_exact::text)"
        " AS item FROM deltallm_spend_read_events_v2 e WHERE id=$1",
        str(finalization.event_id),
    )
    native = result[0]["item"]
    if isinstance(native, str):
        native = json.loads(native)
    assert native.keys() == legacy.row.keys()
    for field, expected in legacy.row.items():
        actual = native[field]
        if field in {"start_time", "end_time"}:
            assert datetime.fromisoformat(actual) == datetime.fromisoformat(expected)
        elif field in {"spend_exact", "provider_cost_exact"} and expected is not None:
            assert Decimal(actual) == Decimal(expected)
        else:
            assert actual == expected, field


async def test_tenant_and_owner_filters_do_not_expose_native_facts(accounting_db):
    clients, generation = accounting_db
    db = clients[0]
    values = await projected(db, generation, owner_account_id="report-owner")
    attribution = values[0].receipt.reservation.attribution
    for column, owned in (
        ("organization_id", attribution.organization_id),
        ("owner_account_id", attribution.owner_account_id),
        ("api_key", attribution.api_key),
    ):
        matching = await db.query_raw(
            f"SELECT count(*)::integer AS count FROM deltallm_spend_read_events_v2 "
            f"WHERE api_key=$1 AND {column} IS NOT DISTINCT FROM $2::text",
            attribution.api_key,
            owned,
        )
        denied = await db.query_raw(
            f"SELECT count(*)::integer AS count FROM deltallm_spend_read_events_v2 "
            f"WHERE api_key=$1 AND {column}=$2",
            attribution.api_key,
            "not-the-verified-scope",
        )
        assert matching == [{"count": 4}] and denied == [{"count": 0}]
    for owner, count in (("report-owner", 4), ("other-account", 0)):
        visibility = SpendVisibility(
            False,
            owner_account_id=owner,
            self_organization_ids=(attribution.organization_id,),
        )
        clauses, parameters = [], []
        apply_spend_visibility(
            clauses=clauses,
            params=parameters,
            visibility=visibility,
            source=replace(SPEND_READ_SOURCE, table="deltallm_spend_read_events_v2"),
        )
        rows = await db.query_raw(
            "SELECT count(*)::integer AS count FROM deltallm_spend_read_events_v2 WHERE "
            + " AND ".join(clauses),
            *parameters,
        )
        assert rows == [{"count": count}]


async def test_native_usage_and_audit_survive_organization_removal(accounting_db):
    clients, generation = accounting_db
    db = clients[0]
    values = await projected(db, generation, count=1, owner_account_id="retained-owner")
    organization = values[0].receipt.reservation.attribution.organization_id
    await db.execute_raw(
        "INSERT INTO deltallm_organizationtable "
        "(organization_id,organization_name,created_at,updated_at) "
        "VALUES ($1,'native retention test',NOW(),NOW())",
        organization,
    )
    try:
        plan = await OrganizationDeletionRepository(db).get_plan(organization)
        assert plan.counts.retained_spend_events == plan.counts.retained_audit_events == 1
        await db.execute_raw(
            "DELETE FROM deltallm_organizationtable WHERE organization_id=$1", organization
        )
        rows = await db.query_raw(
            "SELECT (SELECT count(*)::integer FROM deltallm_spend_read_events_v2 "
            "WHERE organization_id=$1) AS spend,"
            "(SELECT count(*)::integer FROM deltallm_auditevent "
            "WHERE organization_id=$1) AS audit",
            organization,
        )
        assert rows == [{"spend": 1, "audit": 1}]
    finally:
        await db.execute_raw(
            "DELETE FROM deltallm_organizationtable WHERE organization_id=$1", organization
        )


async def test_view_deduplicates_native_and_legacy_without_hiding_retained_history(
    accounting_db,
):
    clients, generation = accounting_db
    db = clients[0]
    values = await projected(db, generation, count=1)
    event_id = str(values[0].finalization.event_id)
    key = values[0].receipt.reservation.attribution.api_key
    try:
        for identity in (event_id, event_id + "-legacy-only"):
            await db.execute_raw(
                "INSERT INTO deltallm_spendlog_events "
                "(id,request_id,call_type,api_key,model,spend,spend_exact,"
                "start_time,end_time,organization_id,owner_account_id,metadata) "
                "SELECT $1,request_id,call_type,api_key,model,spend,spend_exact,"
                "start_time,end_time,organization_id,owner_account_id,metadata "
                "FROM deltallm_accounting_usage_facts_v2 WHERE event_id=$2",
                identity,
                event_id,
            )
        rows = await db.query_raw(
            "SELECT id,spend_exact::text AS spend FROM deltallm_spend_read_events_v2 "
            "WHERE api_key=$1 ORDER BY id",
            key,
        )
        assert {row["id"] for row in rows} == {event_id, event_id + "-legacy-only"}
        assert sum(Decimal(row["spend"]) for row in rows) == Decimal("1.2")
        # Writer rollback keeps the expanded read view. No accepted history disappears.
        await db.execute_raw(
            "UPDATE deltallm_accounting_protocols SET state='draining' "
            "WHERE protocol_name='primary' AND generation=$1",
            generation,
        )
        assert await db.query_raw(
            "SELECT count(*)::integer AS count FROM deltallm_spend_read_events_v2 WHERE api_key=$1",
            key,
        ) == [{"count": 2}]
    finally:
        await db.execute_raw("DELETE FROM deltallm_spendlog_events WHERE api_key=$1", key)


async def test_progress_distinguishes_missing_cells_from_an_empty_queue(accounting_db):
    clients, generation = accounting_db
    db = clients[0]
    repo = repository(db)
    await repo.initialize(generation=generation, expires_at=deadline())
    empty = await repo.progress(generation=generation, expires_at=deadline())
    assert empty.slots == empty.partition_count == 4 and empty.pending_partitions == 0
    await db.execute_raw(
        "DELETE FROM deltallm_accounting_projection_checkpoints "
        "WHERE projection_name='accounting-read-model-v2' AND generation=$1 "
        "AND accounting_partition=3",
        generation,
    )
    missing = await repo.progress(generation=generation, expires_at=deadline())
    assert missing.slots == 3 and missing.partition_count == 4
