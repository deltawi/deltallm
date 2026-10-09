"""Bound grant window work by request scopes, not retained window history."""

import asyncio
from decimal import Decimal
import json
import os
from uuid import UUID, uuid4

import pytest
from prisma.errors import RawQueryError

from src.billing.accounting_protocol import PreissuedPermitAllocation, ReserveDecision
from src.billing.preissued_permits import PreissuedPermitBank
from tests.accounting_adapters.permit_repository import AccountingPermitRepository
from src.db.accounting_local_leases import AccountingLocalLeaseRepository
from src.db.accounting_protocol import AccountingProtocolUnavailable
from tests.performance.accounting_allocator_plans import capture_accounting_plans
from tests.performance.accounting_allocator_plans import safe_plan
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


def nodes(node):
    yield node
    for child in node.get("Plans", []):
        yield from nodes(child)


async def plan(db, sql, *parameters):
    rows = await db.query_raw("EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) " + sql, *parameters)
    value = rows[0]["QUERY PLAN"]
    if isinstance(value, str):
        value = json.loads(value)
    return value[0]["Plan"]


def assert_bounded_window_plan(value, *, maximum_rows):
    planned_windows = [
        node
        for node in nodes(value)
        if node.get("Relation Name") == "deltallm_accounting_budget_windows"
    ]
    for node in planned_windows:
        if node["Actual Loops"] == 0:
            # An unused UNION branch can have a bitmap plan. It must have no
            # actual rows, filtering, or buffer work, not just a small result.
            assert all(
                node.get(field, 0) == 0
                for field in (
                    "Actual Rows",
                    "Rows Removed by Filter",
                    "Rows Removed by Index Recheck",
                    "Shared Hit Blocks",
                    "Shared Read Blocks",
                    "Temp Read Blocks",
                    "Temp Written Blocks",
                )
            ), safe_plan(value)
    window_nodes = [node for node in planned_windows if node["Actual Loops"]]
    assert window_nodes
    assert all(
        node["Node Type"] not in {"Sort", "Incremental Sort"}
        for node in nodes(value)
        if node["Actual Loops"]
    ), safe_plan(value)
    assert all(
        node["Node Type"] not in {"Seq Scan", "Bitmap Heap Scan"} for node in window_nodes
    ), safe_plan(value)
    assert all(node["Actual Rows"] <= maximum_rows for node in window_nodes), window_nodes
    assert all(node.get("Rows Removed by Filter", 0) <= maximum_rows for node in window_nodes)
    return window_nodes


def assert_bounded_allocator_plan(entry):
    observed = set()
    for node in nodes(entry.node):
        if not node["Actual Loops"]:
            continue
        relation = node.get("Relation Name")
        if relation in {
            "deltallm_accounting_budget_windows",
            "deltallm_accounting_grants",
            "deltallm_accounting_grant_windows",
            "deltallm_billing_operations",
        }:
            observed.add(relation)
            assert node["Node Type"] != "Seq Scan", entry.safe_report()
            assert node["Actual Rows"] <= 45, entry.safe_report()
            assert node.get("Rows Removed by Filter", 0) <= 45, entry.safe_report()
        if node["Node Type"] == "Bitmap Index Scan":
            # A bounded bitmap is valid for a key set, but must not build history.
            assert node["Actual Rows"] <= 45, entry.safe_report()
    return observed


async def seed_history(db, generation, item, *, overlap=False):
    await db.execute_raw(
        "INSERT INTO deltallm_accounting_budget_windows "
        "(window_id,protocol_name,generation,scope_type,scope_id,period_key,policy_generation,"
        "limit_exact,window_starts_at,window_ends_at,renewal_spec) "
        "SELECT 'allocator-plan-'||$1::text||'-'||value,'primary',$1::bigint,'organization',"
        "CASE WHEN value<=25000 THEN $2 ELSE 'unrelated-'||value END,'probe-'||value,1,10,"
        "NOW()-INTERVAL '2 hours',CASE WHEN $3::boolean AND value<=25000 "
        "THEN NOW()+INTERVAL '1 hour' WHEN value>25000 THEN NOW()+INTERVAL '1 hour' "
        "ELSE NOW()-INTERVAL '1 hour' END,"
        "CASE WHEN value%2=0 THEN '1h' ELSE NULL END FROM generate_series(1,50000) value",
        generation,
        item.attribution.organization_id,
        overlap,
    )
    await db.execute_raw("ANALYZE deltallm_accounting_budget_windows")


async def test_overlapping_window_lookup_stops_at_nine_without_sorting_history(accounting_db):
    clients, generation = accounting_db
    db = clients[0]
    item = _reservation(generation, str(uuid4()), explicit_window=False)
    await seed_history(db, generation, item, overlap=True)
    value = await plan(
        db,
        "SELECT * FROM deltallm_accounting_permit_window_ids($1,$2::jsonb)",
        generation,
        item.model_dump_json(),
    )
    bounded = assert_bounded_window_plan(value, maximum_rows=9)
    assert any(
        node.get("Index Name") == "deltallm_accounting_window_subject_time_idx" for node in bounded
    )
    assert value["Actual Rows"] == 9


async def test_renewal_lookup_uses_scope_index_and_skips_history(accounting_db):
    clients, generation = accounting_db
    db = clients[0]
    item = _reservation(generation, str(uuid4()), explicit_window=False)
    await seed_history(db, generation, item)
    value = await plan(
        db,
        "SELECT * FROM deltallm_accounting_pending_renewal_scopes($1,$2::jsonb)",
        generation,
        item.model_dump_json(),
    )
    bounded = assert_bounded_window_plan(value, maximum_rows=1)
    assert any(
        node.get("Index Name") == "deltallm_accounting_window_subject_renewal_idx"
        for node in bounded
    )
    assert value["Actual Rows"] == 1
    window_id = str(uuid4())
    await _create_window(db, generation, window_id)
    await db.execute_raw(
        "UPDATE deltallm_accounting_budget_windows SET scope_id=$2 WHERE window_id=$1",
        window_id,
        item.attribution.organization_id,
    )
    assert (
        await db.query_raw(
            "SELECT * FROM deltallm_accounting_pending_renewal_scopes($1,$2::jsonb)",
            generation,
            item.model_dump_json(),
        )
        == []
    )


def admission(db, mode):
    if mode == "assigned":
        return _repository(db, target_operations=4)
    if mode == "local":
        return AccountingLocalLeaseRepository(
            db, owner_id="bounded-local-allocator", statement_budget_seconds=2
        )
    return PreissuedPermitBank(
        AccountingPermitRepository(db, owner_id="bounded-allocator", statement_budget_seconds=2),
        target_operations=4,
        max_operations=256,
        max_subjects=16,
    )


@pytest.mark.parametrize("mode", ["assigned", "permits"])
async def test_all_five_scopes_keep_one_hard_budget_contract(accounting_db, mode):
    clients, generation = accounting_db
    db = clients[0]
    window_id = str(uuid4())
    await _create_window(db, generation, window_id)
    item = _reservation(generation, window_id, explicit_window=False)
    attribution = item.attribution
    scopes = [
        ("api_key", attribution.api_key),
        ("user", attribution.user_id),
        ("team", attribution.team_id),
        ("team_model", f"{attribution.team_id}:{attribution.model}"),
    ]
    window_ids = [window_id]
    for scope, identity in scopes:
        identity_id = str(uuid4())
        window_ids.append(identity_id)
        await db.execute_raw(
            "INSERT INTO deltallm_accounting_budget_windows "
            "(window_id,protocol_name,generation,scope_type,scope_id,period_key,"
            "policy_generation,limit_exact,window_starts_at,window_ends_at) "
            "VALUES ($1,'primary',$2,$3,$4,'test',1,10,NOW()-INTERVAL '1 minute',"
            "NOW()+INTERVAL '1 hour')",
            identity_id,
            generation,
            scope,
            identity,
        )
    owner = admission(db, mode)
    try:
        permits = await owner.reserve_batch(
            [item], expires_at=asyncio.get_running_loop().time() + 6
        )
        assert permits[0].decision is ReserveDecision.DISPATCH
        for identity_id in window_ids:
            assert await _window(db, identity_id) == (Decimal(0), Decimal(4), Decimal(0))
        await _repository(db).finalize_batch(
            [_finalization(item)], expires_at=asyncio.get_running_loop().time() + 6
        )
        await db.execute_raw(
            "UPDATE deltallm_accounting_grants SET expires_at=NOW()-INTERVAL '1 second' "
            "WHERE generation=$1",
            generation,
        )
        assert await _settle_grants(db, generation) == 1
        for identity_id in window_ids:
            assert await _window(db, identity_id) == (Decimal("0.6"), Decimal(0), Decimal(0))
        assert await _outstanding(db, generation) == 0
    finally:
        if isinstance(owner, PreissuedPermitBank):
            await owner.close()


@pytest.mark.parametrize("mode", ["assigned", "permits"])
async def test_expired_renewal_cannot_look_like_an_unlimited_budget(accounting_db, mode):
    clients, generation = accounting_db
    db = clients[0]
    item = _reservation(generation, str(uuid4()), explicit_window=False)
    await seed_history(db, generation, item)
    owner = admission(db, mode)
    try:
        with pytest.raises(AccountingProtocolUnavailable, match="accounting protocol unavailable"):
            await owner.reserve_batch([item], expires_at=asyncio.get_running_loop().time() + 6)
        assert await _outstanding(db, generation) == 0
        assert (
            await db.query_raw(
                "SELECT operation_id FROM deltallm_billing_operations WHERE accounting_generation=$1",
                generation,
            )
            == []
        )
    finally:
        if isinstance(owner, PreissuedPermitBank):
            await owner.close()


async def test_assigned_allocator_rejects_more_than_eight_implicit_windows(accounting_db):
    clients, generation = accounting_db
    db = clients[0]
    item = _reservation(generation, str(uuid4()), explicit_window=False)
    await seed_history(db, generation, item, overlap=True)
    with pytest.raises(RawQueryError, match="accounting_budget_window_capacity"):
        await db.query_raw(
            "SELECT * FROM deltallm_accounting_ensure_grants_batch($1,'bounded',4,30,$2::jsonb)",
            generation,
            json.dumps([item.model_dump(mode="json")]),
        )
    assert await _outstanding(db, generation) == 0


async def test_allocator_uses_bounded_scope_helpers_at_each_window_stage(accounting_db):
    clients, _ = accounting_db
    rows = await clients[0].query_raw(
        "SELECT pg_get_functiondef("
        "'deltallm_accounting_ensure_grants_batch(bigint,text,integer,integer,jsonb)'::regprocedure"
        ") AS body"
    )
    body = rows[0]["body"]
    # Explicit references keep their original primary-key policy validation.
    assert body.count("CASE w.scope_type") == 1
    assert "CASE expired.scope_type" not in body
    assert body.count("deltallm_accounting_permit_window_ids") == 3
    assert "deltallm_accounting_pending_renewal_scopes" in body


async def seed_closed_accounting_history(db, generation):
    await db.execute_raw(
        "INSERT INTO deltallm_accounting_grants "
        "(grant_id,protocol_name,generation,grantee_id,subject_key,accounting_partition,"
        "state,allocated_exact,operation_limit,expires_at,reconciled_at) "
        "SELECT 'allocator-grant-'||$1::text||'-'||value,'primary',$1::bigint,"
        "CASE WHEN value%2=0 THEN 'bounded-allocator' ELSE 'postgres-contract-test' END,"
        "repeat('a',64),0,'closed',0,1,NOW()-INTERVAL '1 hour',NOW() "
        "FROM generate_series(1,10000) value",
        generation,
    )
    await db.execute_raw(
        "INSERT INTO deltallm_billing_operations "
        "(operation_id,owner_token,api_key,model,snapshot,selector_event_id,"
        "selector_allowance,answer_allowance,selector_state,answer_state,closed_at,expires_at,"
        "accounting_protocol,accounting_generation,accounting_partition,request_fingerprint,"
        "accounting_state,provisional_debit_exact) "
        "SELECT 'allocator-op-'||$1::text||'-'||value,'fixture-owner','fixture-key',"
        "'fixture-model','{}'::jsonb,'allocator-event-'||$1::text||'-'||value,"
        "0,0,'unattempted','unattempted',NOW(),NOW()+INTERVAL '5 minutes',"
        "'primary',$1::bigint,0,repeat('a',64),'released',0 FROM generate_series(1,10000) value",
        generation,
    )
    await db.execute_raw("ANALYZE deltallm_accounting_grants")
    await db.execute_raw("ANALYZE deltallm_billing_operations")


@pytest.mark.parametrize("mode", ["assigned", "permits", "local"])
@pytest.mark.parametrize("explicit", [False, True])
@pytest.mark.parametrize("planner", ["auto", "generic", "custom", "alternate_join"])
async def test_actual_nested_allocator_plans_do_not_scan_retained_history(
    accounting_db, mode, explicit, planner
):
    clients, generation = accounting_db
    db = clients[0]
    # Sort the target after retained IDs. A merge scan cannot pass by accident.
    window_id = str(UUID("ffffffff-ffff-4fff-8fff-" + uuid4().hex[-12:]))
    await _create_window(db, generation, window_id)
    item = _reservation(generation, window_id, explicit_window=explicit)
    await seed_history(db, generation, item)
    await seed_closed_accounting_history(db, generation)
    # Retain real FK references as well as their closed grants. A small reference
    # table can conceal a flattened join or a broad bitmap of dead index entries.
    await db.execute_raw(
        "INSERT INTO deltallm_accounting_grant_windows(grant_id,window_id,allocated_exact) "
        "SELECT grant_id,$2,0 FROM deltallm_accounting_grants "
        "WHERE generation=$1 AND grant_id LIKE 'allocator-grant-%'",
        generation,
        window_id,
    )
    await db.execute_raw("ANALYZE deltallm_accounting_grant_windows")
    async with capture_accounting_plans(os.environ["DATABASE_URL"], planner=planner) as captured:
        owner = admission(captured, mode)
        try:
            # Include warm calls past the driver's prepared-plan threshold.
            for _ in range(6):
                next_item = _reservation(generation, window_id, explicit_window=explicit)
                if mode == "local":
                    grants = await owner.allocate_batch(
                        [
                            PreissuedPermitAllocation(
                                reservation=next_item, fence_token=uuid4(), target_operations=1
                            )
                        ],
                        expires_at=asyncio.get_running_loop().time() + 6,
                    )
                    assert grants[0] is not None and grants[0].operation_limit == 1
                else:
                    permits = await owner.reserve_batch(
                        [next_item], expires_at=asyncio.get_running_loop().time() + 6
                    )
                    assert permits[0].decision is ReserveDecision.DISPATCH
        finally:
            if isinstance(owner, PreissuedPermitBank):
                await owner.close()
    assert captured.errors == []
    queries = [" ".join(entry.query.split()) for entry in captured.plans]
    required = (
        "FROM deltallm_accounting_protocols",
        "FROM deltallm_accounting_grants g",
        "FROM deltallm_accounting_partitions p",
        "INSERT INTO deltallm_accounting_grants",
        "INSERT INTO deltallm_accounting_grant_windows",
        "UPDATE deltallm_accounting_budget_windows w",
        "UPDATE deltallm_accounting_partitions",
    )
    assert all(any(marker in query for query in queries) for marker in required), queries
    observed_tables = set()
    for entry in captured.plans:
        observed_tables.update(assert_bounded_allocator_plan(entry))
    assert len(observed_tables) == 4
    reserved = Decimal(6) if mode == "local" else Decimal(8)
    assert await _window(db, window_id) == (Decimal(0), reserved, Decimal(0))
    print(
        "\nACCOUNTING_ALLOCATION_PLANS="
        + json.dumps(
            {
                "mode": mode,
                "explicit": explicit,
                "planner": planner,
                "plans": [entry.safe_report() for entry in captured.plans],
            },
            sort_keys=True,
        )
    )


@pytest.mark.parametrize("explicit", [False, True])
@pytest.mark.parametrize("funding", ["assigned", "permits", "local"])
async def test_bounded_window_membership_cannot_become_a_retained_history_join(
    accounting_db, explicit, funding
):
    clients, generation = accounting_db
    db = clients[0]
    window_id = str(UUID("ffffffff-ffff-4fff-8fff-" + uuid4().hex[-12:]))
    await _create_window(db, generation, window_id)
    item = _reservation(generation, window_id, explicit_window=explicit)
    await seed_history(db, generation, item)
    async with capture_accounting_plans(
        os.environ["DATABASE_URL"], planner="alternate_join"
    ) as captured:
        # Match the actual lock lookup; retain its SQL shape after migration.
        signatures = {
            "assigned": "deltallm_accounting_ensure_grants_batch(bigint,text,integer,integer,jsonb)",
            "permits": "deltallm_accounting_allocate_permit_grants_batch(bigint,text,integer,jsonb)",
            "local": "deltallm_accounting_allocate_local_permit_grants_batch(bigint,text,integer,jsonb)",
        }
        rows = await captured.query_raw(
            "SELECT pg_get_functiondef($1::regprocedure) AS body", signatures[funding]
        )
        body = rows[0]["body"]
        start = body.index("PERFORM 1 FROM deltallm_accounting_budget_windows w")
        end = body.index(";", start)
        query = body[start:end].replace("PERFORM 1", "SELECT w.window_id", 1)
        query = query.replace("p_generation", "$1")
        if funding == "assigned":
            query = query.replace(",item)", ",$2::jsonb)")
            payload = item.model_dump_json()
        else:
            query = query.replace("p_items", "$2::jsonb")
            payload = json.dumps([{"reservation": item.model_dump(mode="json")}])
        result = await captured.query_raw(query, generation, payload)
        assert [row["window_id"] for row in result] == [window_id]
    assert captured.errors == []
    observed = [entry for entry in captured.plans if entry.query == query]
    assert observed
    for entry in observed:
        print("\nWINDOW_KEYSET_PLAN=" + json.dumps(entry.safe_report(), sort_keys=True))
        assert "deltallm_accounting_budget_windows" in assert_bounded_allocator_plan(entry)
