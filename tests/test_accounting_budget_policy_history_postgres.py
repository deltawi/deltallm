"""Policy edits keep durable charges without waiting for report projection."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
import json

import pytest
from prisma.errors import RawQueryError

from src.billing.accounting.permits.accounting_local_leases import (
    LocalPermitFinalization,
    LocalPermitReturn,
)
from src.billing.accounting.accounting_protocol import (
    AccountingAttempt,
    AccountingOperationHandle,
    AccountingOutcome,
    AccountingScope,
    ReserveDecision,
)
from src.billing.spend.spend import SpendTrackingService
from src.billing.budgets.budget import budget_read_period
from src.db.accounting.accounting_budget_reads import AccountingBudgetReadRepository
from tests.accounting_read_model_fixtures import reporting_finalization, reporting_handle
from tests.test_accounting_budget_regressions_postgres import issuer, materialize, organization
from tests.test_accounting_local_leases_postgres import deadline, owner
from tests.test_accounting_local_lease_foundation_postgres import expire
from tests.test_accounting_protocol_postgres import _settle_grants, accounting_db as _accounting_db
from tests.test_preissued_permit_bank import fresh

pytestmark = pytest.mark.postgres
accounting_db = _accounting_db


async def complete(
    db,
    generation,
    item,
    *,
    outcome=AccountingOutcome.COMPLETED,
    start=None,
    unresolved=0,
):
    permit = (await issuer(db, generation).reserve_batch([item], expires_at=deadline())).permits[0]
    terminal = reporting_finalization(reporting_handle(permit.proof), outcome)
    if unresolved:
        terminal = terminal.model_copy(update={"unresolved_attempts": unresolved})
    if start is not None:
        terminal = terminal.model_copy(
            update={"spend_payload": terminal.spend_payload | {"start_time": start}}
        )
    await materialize(
        db, generation, [LocalPermitFinalization(receipt=permit.proof, finalization=terminal)]
    )
    await owner(db).return_batch(
        [LocalPermitReturn(grant=permit.proof.grant, first_unused_ordinal=1)], expires_at=deadline()
    )
    assert await _settle_grants(db, generation) == 1
    return terminal


async def owners(db, generation, initial):
    item = await organization(db, generation, limit=initial)
    a = item.attribution
    await db.execute_raw(
        "INSERT INTO deltallm_teamtable(team_id,organization_id,max_budget,model_max_budget,updated_at) "
        "VALUES ($1,$2,$3,$4::jsonb,NOW())",
        a.team_id,
        a.organization_id,
        initial,
        '{"gpt-test":10}' if initial is not None else "{}",
    )
    await db.execute_raw(
        "INSERT INTO deltallm_usertable(user_id,team_id,max_budget,updated_at) VALUES ($1,$2,$3,NOW())",
        a.user_id,
        a.team_id,
        initial,
    )
    await db.execute_raw(
        "INSERT INTO deltallm_verificationtoken(id,token,user_id,team_id,max_budget,updated_at) "
        "VALUES (gen_random_uuid(),$1,$2,$3,$4,NOW())",
        a.api_key,
        a.user_id,
        a.team_id,
        initial,
    )
    return item


_POLICIES = {
    "api_key": ("deltallm_verificationtoken", "token", "api_key"),
    "user": ("deltallm_usertable", "user_id", "user_id"),
    "team": ("deltallm_teamtable", "team_id", "team_id"),
    "organization": ("deltallm_organizationtable", "organization_id", "organization_id"),
    "team_model": ("deltallm_teamtable", "team_id", "team_id"),
}


async def change(db, item, scope, limit):
    table, column, attribute = _POLICIES[scope]
    identity = getattr(item.attribution, attribute)
    if scope == "team_model":
        value = "{}" if limit is None else '{"gpt-test":' + str(limit) + "}"
        await db.execute_raw(
            f"UPDATE {table} SET model_max_budget=$1::jsonb WHERE {column}=$2", value, identity
        )
    else:
        await db.execute_raw(f"UPDATE {table} SET max_budget=$1 WHERE {column}=$2", limit, identity)


@pytest.mark.parametrize("scope", list(_POLICIES))
@pytest.mark.parametrize("initial", [None, 10])
async def test_new_and_restored_caps_keep_native_charges_before_projection(
    accounting_db, scope, initial
):
    clients, generation = accounting_db
    db = clients[0]
    item = await owners(db, generation, initial)
    a = item.attribution
    try:
        await complete(db, generation, item)
        assert await db.query_raw(
            "SELECT count(*)::int AS count FROM deltallm_accounting_usage_facts_v2 WHERE protocol_generation=$1",
            generation,
        ) == [{"count": 0}]
        await change(db, item, scope, None)
        with pytest.raises(RawQueryError, match="accounting_budget_policy_below_debits"):
            await change(db, item, scope, 0.5)
        for _ in range(2):
            await change(db, item, scope, 0.7)
            scope_id = getattr(a, _POLICIES[scope][2])
            if scope == "team_model":
                scope_id += ":" + a.model
            row = await db.query_raw(
                "SELECT committed_exact::text AS committed FROM deltallm_accounting_budget_windows "
                "WHERE generation=$1 AND scope_type=$2 AND scope_id=$3 "
                "AND window_starts_at<=NOW() AND window_ends_at>NOW()",
                generation,
                scope,
                scope_id,
            )
            assert len(row) == 1 and Decimal(row[0]["committed"]) == Decimal("0.6")
            request = fresh(item).model_copy(update={"allowance": Decimal("0.2")})
            denied = (
                await issuer(db, generation).reserve_batch([request], expires_at=deadline())
            ).permits[0]
            assert denied.decision is ReserveDecision.BUDGET_EXHAUSTED
            await change(db, item, scope, None)
    finally:
        await db.execute_raw("DELETE FROM deltallm_verificationtoken WHERE token=$1", a.api_key)
        await db.execute_raw("DELETE FROM deltallm_usertable WHERE user_id=$1", a.user_id)
        await db.execute_raw("DELETE FROM deltallm_teamtable WHERE team_id=$1", a.team_id)
        await db.execute_raw(
            "DELETE FROM deltallm_organizationtable WHERE organization_id=$1", a.organization_id
        )


@pytest.mark.parametrize("legacy_projection", [False, True])
async def test_seed_keeps_legacy_balance_without_counting_projected_charge_twice(
    accounting_db, legacy_projection
):
    clients, generation = accounting_db
    db = clients[0]
    item = await organization(db, generation)
    org = item.attribution.organization_id
    terminal = None
    try:
        await db.execute_raw(
            "UPDATE deltallm_organizationtable SET spend=0.2,spend_exact=0.2 WHERE organization_id=$1",
            org,
        )
        terminal = await complete(db, generation, item)
        if legacy_projection:
            await SpendTrackingService(db).log_batch_once(
                [(str(terminal.event_id), "spend", terminal.spend_payload)]
            )
        await db.execute_raw(
            "UPDATE deltallm_organizationtable SET max_budget=10 WHERE organization_id=$1", org
        )
        balance = (
            await AccountingBudgetReadRepository(db).balances(AccountingScope.ORGANIZATION, [org])
        )[org]
        assert balance.spend == Decimal("0.8")
    finally:
        if terminal is not None:
            await db.execute_raw(
                "DELETE FROM deltallm_spendlog_events WHERE id=$1", str(terminal.event_id)
            )
        await db.execute_raw("DELETE FROM deltallm_organizationtable WHERE organization_id=$1", org)


@pytest.mark.parametrize("outcome", [AccountingOutcome.UNCERTAIN, AccountingOutcome.COMPLETED])
async def test_uncertain_charge_must_be_resolved_before_new_budget(accounting_db, outcome):
    clients, generation = accounting_db
    db = clients[0]
    item = await organization(db, generation)
    org = item.attribution.organization_id
    try:
        await complete(
            db,
            generation,
            item,
            outcome=outcome,
            unresolved=1 if outcome is AccountingOutcome.COMPLETED else 0,
        )
        with pytest.raises(RawQueryError, match="accounting_budget_policy_requires_drain"):
            await db.execute_raw(
                "UPDATE deltallm_organizationtable SET max_budget=10 WHERE organization_id=$1", org
            )
        handle = AccountingOperationHandle(
            reservation=item,
            dispatch_token=item.owner_token,
            accounting_partition=0,
            attempts=(
                AccountingAttempt(
                    deployment_id=item.attribution.deployment_id,
                    provider=item.attribution.provider,
                    model=item.attribution.model,
                    pricing_snapshot=item.pricing_snapshot,
                ),
            ),
        )
        payload = reporting_finalization(handle).model_dump(mode="json")["spend_payload"]
        await db.query_raw(
            "SELECT deltallm_accounting_resolve_provisional($1,$2,0.3::numeric,$3::jsonb,'provider evidence')",
            generation,
            str(item.operation_id),
            json.dumps(payload | {"cost_exact": "0.3"}),
        )
        await db.execute_raw(
            "UPDATE deltallm_organizationtable SET max_budget=10 WHERE organization_id=$1", org
        )
        balance = (
            await AccountingBudgetReadRepository(db).balances(AccountingScope.ORGANIZATION, [org])
        )[org]
        assert balance.spend == (
            Decimal("0.9") if outcome is AccountingOutcome.COMPLETED else Decimal("0.3")
        )
    finally:
        await db.execute_raw("DELETE FROM deltallm_organizationtable WHERE organization_id=$1", org)


@pytest.mark.parametrize("outcome", [AccountingOutcome.COMPLETED, AccountingOutcome.UNCERTAIN])
async def test_another_tenants_charges_and_holds_do_not_change_a_new_cap(accounting_db, outcome):
    clients, generation = accounting_db
    db = clients[0]
    first = await organization(db, generation)
    second = await organization(db, generation)
    try:
        await complete(db, generation, first, outcome=outcome)
        await db.execute_raw(
            "UPDATE deltallm_organizationtable SET max_budget=0.5 WHERE organization_id=$1",
            second.attribution.organization_id,
        )
        balance = (
            await AccountingBudgetReadRepository(db).balances(
                AccountingScope.ORGANIZATION,
                [second.attribution.organization_id],
            )
        )[second.attribution.organization_id]
        assert balance.spend == 0
    finally:
        for item in (first, second):
            await db.execute_raw(
                "DELETE FROM deltallm_organizationtable WHERE organization_id=$1",
                item.attribution.organization_id,
            )


@pytest.mark.parametrize("duration", ["1h", "1d", "1mo"])
async def test_new_recurring_cap_excludes_charges_before_current_period(accounting_db, duration):
    clients, generation = accounting_db
    db = clients[0]
    item = await organization(db, generation)
    org = item.attribution.organization_id
    try:
        await complete(db, generation, item, start=datetime.now(UTC) - timedelta(days=40))
        reset = datetime.now(UTC) + timedelta(minutes=30)
        await db.execute_raw(
            "UPDATE deltallm_organizationtable SET max_budget=0.5,budget_duration=$2,budget_reset_at=$3::timestamp "
            "WHERE organization_id=$1",
            org,
            duration,
            reset.replace(tzinfo=None),
        )
        balance = (
            await AccountingBudgetReadRepository(db).balances(AccountingScope.ORGANIZATION, [org])
        )[org]
        assert balance.spend == 0
    finally:
        await db.execute_raw("DELETE FROM deltallm_organizationtable WHERE organization_id=$1", org)


@pytest.mark.parametrize("inside", [False, True])
async def test_monthly_history_uses_the_existing_month_end_anchor(accounting_db, inside):
    clients, generation = accounting_db
    db = clients[0]
    item = await organization(db, generation)
    org = item.attribution.organization_id
    now = datetime.now(UTC)
    next_month = (now.replace(day=1) + timedelta(days=32)).replace(day=1)
    reset = (next_month - timedelta(days=1)).replace(
        hour=23, minute=59, second=59, microsecond=999000
    )
    metadata = {"_budget_reset": {"monthly_anchor_day": 31}}
    period = budget_read_period("1mo", reset, metadata, now=now)
    start = period.starts_at + timedelta(microseconds=1 if inside else -1)
    try:
        await complete(db, generation, item, start=start)
        await db.execute_raw(
            "UPDATE deltallm_organizationtable SET max_budget=$2::numeric,budget_duration='1mo',"
            "budget_reset_at=$3::timestamp,metadata=$4::jsonb WHERE organization_id=$1",
            org,
            Decimal("0.7"),
            reset.replace(tzinfo=None).isoformat(),
            json.dumps(metadata),
        )
        balance = (
            await AccountingBudgetReadRepository(db).balances(
                AccountingScope.ORGANIZATION,
                [org],
            )
        )[org]
        assert balance.spend == (Decimal("0.6") if inside else Decimal(0))
        assert balance.reset_at == reset
    finally:
        await db.execute_raw("DELETE FROM deltallm_organizationtable WHERE organization_id=$1", org)


@pytest.mark.parametrize("old_snapshot", [False, True])
async def test_unreported_closed_grant_cannot_be_hidden_by_a_new_budget(
    accounting_db, old_snapshot
):
    clients, generation = accounting_db
    db = clients[0]
    item = await organization(db, generation)
    org = item.attribution.organization_id
    try:
        permit = (
            await issuer(db, generation).reserve_batch([item], expires_at=deadline())
        ).permits[0]
        await expire(db, {"grant_id": permit.proof.grant.grant_id})
        assert await _settle_grants(db, generation) == 1
        if old_snapshot:
            await db.execute_raw(
                "UPDATE deltallm_accounting_grants SET policy_attribution=NULL WHERE grant_id=$1",
                permit.proof.grant.grant_id,
            )
        with pytest.raises(RawQueryError, match="accounting_budget_policy_requires_drain"):
            await db.execute_raw(
                "UPDATE deltallm_organizationtable SET max_budget=10 WHERE organization_id=$1",
                org,
            )
    finally:
        await db.execute_raw("DELETE FROM deltallm_organizationtable WHERE organization_id=$1", org)
