"""Short native functions keep their execution policy local and money exact."""

from decimal import Decimal
import os
from uuid import uuid4

import asyncpg
import pytest

from src.billing.accounting.permits.accounting_local_leases import LocalPermitReturn
from src.billing.accounting.accounting_protocol import ReserveDecision
from tests.performance.accounting_allocator_plans import capture_accounting_plans
from tests.test_accounting_local_leases_postgres import allocation, deadline, owner
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

FUNCTION_POLICIES = (
    (
        "deltallm_accounting_claim_read_model(text,bigint,text,uuid,integer,integer)",
        {"plan_cache_mode=force_custom_plan"},
    ),
    (
        "deltallm_accounting_read_model_progress(text,bigint)",
        {"plan_cache_mode=force_custom_plan"},
    ),
    (
        "deltallm_accounting_recover_read_model_claim(text,bigint,text,uuid,integer)",
        {"plan_cache_mode=force_custom_plan"},
    ),
    (
        "deltallm_accounting_admit_grant_batch(bigint,text,integer,integer,jsonb)",
        {"jit=off"},
    ),
    (
        "deltallm_accounting_ensure_grants_batch(bigint,text,integer,integer,jsonb)",
        {"jit=off"},
    ),
    (
        "deltallm_accounting_reserve_grant_batch(bigint,text,integer,integer,jsonb)",
        {"jit=off"},
    ),
    (
        "deltallm_accounting_finalize_grant_batch(bigint,jsonb)",
        {"jit=off"},
    ),
    (
        "deltallm_accounting_claim_terminal_journal(bigint,text,uuid,integer,integer)",
        {"plan_cache_mode=force_custom_plan"},
    ),
    (
        "deltallm_accounting_append_terminal_journal(bigint,jsonb,text[],text[])",
        {"plan_cache_mode=force_custom_plan"},
    ),
    (
        "deltallm_accounting_allocate_local_permit_grants_batch(bigint,text,integer,jsonb)",
        {"jit=off"},
    ),
    (
        "deltallm_accounting_backlog_snapshot(bigint)",
        {"jit=off", "enable_seqscan=off", "enable_bitmapscan=off"},
    ),
    (
        "deltallm_accounting_project_read_models(bigint,text,uuid,integer,bigint,bigint[])",
        {"jit=off", "enable_seqscan=off", "enable_bitmapscan=off"},
    ),
)


@pytest.mark.parametrize("signature,expected", FUNCTION_POLICIES)
async def test_only_the_owned_function_settings_change(accounting_db, signature, expected):
    clients, _ = accounting_db
    rows = await clients[0].query_raw(
        "SELECT proconfig FROM pg_proc WHERE oid=$1::regprocedure", signature
    )
    assert set(rows[0]["proconfig"] or ()) == expected
    unrelated = await clients[0].query_raw(
        "SELECT proconfig FROM pg_proc WHERE oid="
        "'deltallm_accounting_pending_legacy_work()'::regprocedure"
    )
    assert "jit=off" not in (unrelated[0]["proconfig"] or ())


@pytest.mark.parametrize("planner", ["auto", "generic", "custom"])
async def test_grant_calls_skip_nested_compilation_and_restore_caller(accounting_db, planner):
    clients, generation = accounting_db
    db = clients[0]
    window = str(uuid4())
    await _create_window(db, generation, window)
    item = _reservation(generation, window)
    async with capture_accounting_plans(os.environ["DATABASE_URL"], planner=planner) as captured:
        connection = captured._connection
        await connection.execute("SET jit=on; SET jit_above_cost=0")
        await captured.query_raw("SELECT sum(value) FROM generate_series(1,10) value")
        assert any(plan.jit_functions > 0 for plan in captured.plans)
        repository = _repository(captured)
        for call, payload in (
            (repository.reserve_batch, item),
            (repository.finalize_batch, _finalization(item)),
        ):
            captured.plans.clear()
            (result,) = await call([payload], expires_at=deadline())
            if payload is item:
                assert result.decision is ReserveDecision.DISPATCH
            else:
                assert not result.replayed
            assert len(captured.plans) > 1
            assert all(plan.jit_functions == 0 for plan in captured.plans[:-1])
            assert await connection.fetchval("SHOW jit") == "on"
            assert await connection.fetchval("SHOW jit_above_cost") == "0"
        with pytest.raises(asyncpg.PostgresError):
            async with connection.transaction():
                await connection.fetch(
                    "SELECT * FROM deltallm_accounting_admit_grant_batch($1,$2,$3,$4,$5::jsonb)",
                    generation,
                    "execution-test",
                    1,
                    30,
                    "[]",
                )
        assert await connection.fetchval("SHOW jit") == "on"
        assert await connection.fetchval("SHOW jit_above_cost") == "0"
    assert captured.errors == []
    assert await _settle_grants(db, generation) == 1
    assert await _window(db, window) == (Decimal("0.6"), Decimal(0), Decimal(0))
    assert await _outstanding(db, generation) == 0


async def test_funding_skips_nested_compilation_and_restores_caller_after_success_or_error(
    accounting_db,
):
    clients, generation = accounting_db
    db = clients[0]
    window = str(uuid4())
    await _create_window(db, generation, window)
    async with capture_accounting_plans(os.environ["DATABASE_URL"]) as captured:
        connection = captured._connection
        await connection.execute("SET jit=on; SET jit_above_cost=0")
        await captured.query_raw("SELECT sum(value) FROM generate_series(1,10) value")
        assert any(plan.jit_functions > 0 for plan in captured.plans)
        captured.plans.clear()
        repository = owner(captured)
        grant = (
            await repository.allocate_batch(
                [allocation(_reservation(generation, window))], expires_at=deadline()
            )
        )[0]
        assert len(captured.plans) > 1
        assert all(plan.jit_functions == 0 for plan in captured.plans[:-1])
        assert await connection.fetchval("SHOW jit") == "on"
        assert await connection.fetchval("SHOW jit_above_cost") == "0"
        with pytest.raises(asyncpg.PostgresError):
            async with connection.transaction():
                await connection.fetch(
                    "SELECT * FROM deltallm_accounting_allocate_local_permit_grants_batch"
                    "($1,$2,$3,$4::jsonb)",
                    generation,
                    "execution-test",
                    30,
                    "[]",
                )
        assert await connection.fetchval("SHOW jit") == "on"
        await connection.execute("RESET jit_above_cost")
        assert await repository.return_batch(
            [LocalPermitReturn(grant=grant, first_unused_ordinal=0)], expires_at=deadline()
        ) == [4]
    assert captured.errors == []
    assert await _settle_grants(db, generation) == 1
    assert await _window(db, window) == (Decimal(0), Decimal(0), Decimal(0))
    assert await _outstanding(db, generation) == 0
