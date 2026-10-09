"""Budget edits, admin balances, and alerts retain native economic authority."""

import asyncio
from decimal import Decimal
from uuid import uuid4

import pytest
from prisma.errors import RawQueryError

from src.billing.accounting.permits.accounting_local_cursors import LocalCursorStore
from src.billing.accounting.permits.accounting_local_issuer import LocalPermitIssuer
from src.billing.accounting.permits.accounting_local_leases import (
    LocalPermitFinalization,
    LocalPermitReturn,
)
from src.billing.accounting.permits.accounting_local_receipts import LocalReceiptStore
from src.billing.accounting.accounting_protocol import (
    AccountingOutcome,
    AccountingScope,
    ReserveDecision,
)
from src.db.accounting_budget_reads import AccountingBudgetReadRepository
from src.db.accounting_journal import AccountingJournalRepository
from src.db.accounting_journal_worker import AccountingJournalWorkerRepository
from src.db.budget_notifications import BudgetNotificationRepository
from src.db.accounting_read_model import AccountingReadModelRepository
from src.db.accounting_protocol import AccountingProtocolRepository
from tests.accounting_read_model_fixtures import reporting_finalization, reporting_handle
from tests.test_accounting_local_leases_postgres import deadline, owner
from tests.test_accounting_protocol_postgres import (
    _finalization,
    _reservation,
    _settle_grants,
    accounting_db as _accounting_db,
)
from tests.test_preissued_permit_bank import fresh

pytestmark = pytest.mark.postgres
accounting_db = _accounting_db


def issuer(db, generation):
    return LocalPermitIssuer(
        owner(db),
        LocalCursorStore(generation=generation, max_entries=128, max_retained_bytes=1024 * 1024),
        LocalReceiptStore(max_entries=128, max_retained_bytes=1024 * 1024),
        target_operations=4,
    )


async def organization(db, generation, *, limit=None, soft=None):
    item = _reservation(generation, str(uuid4()), explicit_window=False)
    await db.execute_raw(
        "INSERT INTO deltallm_organizationtable(organization_id,max_budget,soft_budget,updated_at) "
        "VALUES ($1,$2,$3,NOW())",
        item.attribution.organization_id,
        limit,
        soft,
    )
    return item


async def materialize(db, generation, values):
    await AccountingJournalRepository(db, statement_budget_seconds=2).append_batch(
        values, expires_at=deadline()
    )
    worker = AccountingJournalWorkerRepository(db, statement_budget_seconds=2)
    claim = await worker.claim(
        generation=generation, worker_id="budget-regression", expires_at=deadline()
    )
    assert await worker.materialize(claim, expires_at=deadline()) == len(values)


async def project_reports(db, generation):
    reports = AccountingReadModelRepository(db, statement_budget_seconds=2)
    await reports.initialize(generation=generation, expires_at=deadline())
    page = await reports.claim(
        generation=generation,
        worker_id="budget-reports",
        limit=256,
        lease_seconds=30,
        expires_at=deadline(),
    )
    assert await reports.materialize(page, expires_at=deadline()) == 1


async def test_new_hard_budget_cannot_commit_over_warm_permits_and_works_after_drain(accounting_db):
    clients, generation = accounting_db
    db = clients[0]
    item = await organization(db, generation)
    org = item.attribution.organization_id
    try:
        admission = issuer(db, generation)
        first = (await admission.reserve_batch([item], expires_at=deadline())).permits[0]
        unrelated = await organization(db, generation, limit=10)
        await db.execute_raw(
            "DELETE FROM deltallm_organizationtable WHERE organization_id=$1",
            unrelated.attribution.organization_id,
        )
        with pytest.raises(
            RawQueryError, match="accounting_budget_policy_requires_drain"
        ) as conflict:
            await db.execute_raw(
                "UPDATE deltallm_organizationtable SET max_budget=0 WHERE organization_id=$1", org
            )
        assert conflict.value.meta["code"] == "55000"
        assert await db.query_raw(
            "SELECT max_budget FROM deltallm_organizationtable WHERE organization_id=$1", org
        ) == [{"max_budget": None}]
        rest = (
            await admission.reserve_batch([fresh(item) for _ in range(3)], expires_at=deadline())
        ).permits
        values = [
            LocalPermitFinalization(
                receipt=p.proof,
                finalization=_finalization(p.proof.reservation, AccountingOutcome.NOT_DISPATCHED),
            )
            for p in (first, *rest)
        ]
        await materialize(db, generation, values)
        assert await _settle_grants(db, generation) == 1
        await db.execute_raw(
            "UPDATE deltallm_organizationtable SET max_budget=0 WHERE organization_id=$1", org
        )
        rejected = (await admission.reserve_batch([fresh(item)], expires_at=deadline())).permits[0]
        assert rejected.decision is ReserveDecision.BUDGET_EXHAUSTED
    finally:
        await db.execute_raw("DELETE FROM deltallm_organizationtable WHERE organization_id=$1", org)


async def test_policy_fence_waits_for_an_inflight_refill_then_sees_its_debits(accounting_db):
    clients, generation = accounting_db
    db, editing = clients
    item = await organization(db, generation)
    org = item.attribution.organization_id
    try:
        async with db.tx() as tx:
            await tx.query_raw(
                "SELECT generation FROM deltallm_accounting_protocols WHERE generation=$1 FOR SHARE",
                generation,
            )
            change = asyncio.create_task(
                editing.execute_raw(
                    "UPDATE deltallm_organizationtable SET max_budget=0 WHERE organization_id=$1",
                    org,
                )
            )
            # A refill in the transaction that owns the shared lock can finish.
            first = (
                await issuer(tx, generation).reserve_batch([item], expires_at=deadline())
            ).permits[0]
        with pytest.raises(RawQueryError, match="accounting_budget_policy_requires_drain"):
            await change
        await owner(db).return_batch(
            [LocalPermitReturn(grant=first.proof.grant, first_unused_ordinal=1)],
            expires_at=deadline(),
        )
        await materialize(
            db,
            generation,
            [
                LocalPermitFinalization(
                    receipt=first.proof,
                    finalization=_finalization(item, AccountingOutcome.NOT_DISPATCHED),
                )
            ],
        )
        assert await _settle_grants(db, generation) == 1
    finally:
        await db.execute_raw("DELETE FROM deltallm_organizationtable WHERE organization_id=$1", org)


@pytest.mark.parametrize("limit", [None, 10])
async def test_native_balance_and_soft_alert_use_settled_authority_not_legacy_zero(
    accounting_db, limit
):
    clients, generation = accounting_db
    db = clients[0]
    item = await organization(db, generation, limit=limit, soft=0.5)
    org = item.attribution.organization_id
    try:
        permit = (
            await issuer(db, generation).reserve_batch([item], expires_at=deadline())
        ).permits[0]
        await materialize(
            db,
            generation,
            [
                LocalPermitFinalization(
                    receipt=permit.proof,
                    finalization=reporting_finalization(reporting_handle(permit.proof)),
                )
            ],
        )
        await owner(db).return_batch(
            [LocalPermitReturn(grant=permit.proof.grant, first_unused_ordinal=1)],
            expires_at=deadline(),
        )
        assert await _settle_grants(db, generation) == 1
        await project_reports(db, generation)
        assert await db.query_raw(
            "SELECT spend FROM deltallm_organizationtable WHERE organization_id=$1", org
        ) == [{"spend": 0.0}]
        balance = await AccountingBudgetReadRepository(db).balances(
            AccountingScope.ORGANIZATION, [org]
        )
        assert balance[org].spend == Decimal("0.6")
        notifications = BudgetNotificationRepository(db)
        assert await notifications.enqueue_accounting_thresholds(after="", ttl_seconds=60) == org
        await notifications.enqueue_accounting_thresholds(after="", ttl_seconds=60)
        records = await db.query_raw(
            "SELECT spend::text,soft_budget::text,status FROM deltallm_budgetnotification WHERE organization_id=$1",
            org,
        )
        assert len(records) == 1 and Decimal(records[0]["spend"]) == Decimal("0.6")
        assert (
            Decimal(records[0]["soft_budget"]) == Decimal("0.5")
            and records[0]["status"] == "pending"
        )
    finally:
        await db.execute_raw("DELETE FROM deltallm_organizationtable WHERE organization_id=$1", org)


@pytest.mark.parametrize("limit", [None, 10])
async def test_all_admin_scope_balances_include_native_charges(accounting_db, limit):
    clients, generation = accounting_db
    db = clients[0]
    item = await organization(db, generation, limit=limit)
    attribution = item.attribution
    try:
        await db.execute_raw(
            "INSERT INTO deltallm_teamtable(team_id,organization_id,max_budget,updated_at) VALUES ($1,$2,$3,NOW())",
            attribution.team_id,
            attribution.organization_id,
            limit,
        )
        await db.execute_raw(
            "INSERT INTO deltallm_usertable(user_id,team_id,max_budget,updated_at) VALUES ($1,$2,$3,NOW())",
            attribution.user_id,
            attribution.team_id,
            limit,
        )
        await db.execute_raw(
            "INSERT INTO deltallm_verificationtoken(id,token,user_id,team_id,max_budget,updated_at) VALUES (gen_random_uuid(),$1,$2,$3,$4,NOW())",
            attribution.api_key,
            attribution.user_id,
            attribution.team_id,
            limit,
        )
        permit = (
            await issuer(db, generation).reserve_batch([item], expires_at=deadline())
        ).permits[0]
        await materialize(
            db,
            generation,
            [
                LocalPermitFinalization(
                    receipt=permit.proof,
                    finalization=reporting_finalization(reporting_handle(permit.proof)),
                )
            ],
        )
        await owner(db).return_batch(
            [LocalPermitReturn(grant=permit.proof.grant, first_unused_ordinal=1)],
            expires_at=deadline(),
        )
        assert await _settle_grants(db, generation) == 1
        await project_reports(db, generation)
        for scope, scope_id in (
            (AccountingScope.API_KEY, attribution.api_key),
            (AccountingScope.USER, attribution.user_id),
            (AccountingScope.TEAM, attribution.team_id),
            (AccountingScope.ORGANIZATION, attribution.organization_id),
        ):
            balance = await AccountingBudgetReadRepository(db).balances(scope, [scope_id])
            assert balance[scope_id].spend == Decimal("0.6")
    finally:
        await db.execute_raw(
            "DELETE FROM deltallm_verificationtoken WHERE token=$1", attribution.api_key
        )
        await db.execute_raw("DELETE FROM deltallm_usertable WHERE user_id=$1", attribution.user_id)
        await db.execute_raw("DELETE FROM deltallm_teamtable WHERE team_id=$1", attribution.team_id)
        await db.execute_raw(
            "DELETE FROM deltallm_organizationtable WHERE organization_id=$1",
            attribution.organization_id,
        )


@pytest.mark.parametrize("scope", list(AccountingScope))
async def test_new_budget_for_each_scope_is_fenced_until_matching_work_drains(accounting_db, scope):
    clients, generation = accounting_db
    db = clients[0]
    item = await organization(db, generation)
    item = item.model_copy(
        update={
            "attribution": item.attribution.model_copy(
                update={"team_id": item.attribution.team_id + ":west", "model": "gpt:test/sub"}
            )
        }
    )
    attribution = item.attribution
    identities = {
        AccountingScope.API_KEY: attribution.api_key,
        AccountingScope.USER: attribution.user_id,
        AccountingScope.TEAM: attribution.team_id,
        AccountingScope.ORGANIZATION: attribution.organization_id,
        AccountingScope.TEAM_MODEL: attribution.team_id + ":" + attribution.model,
    }
    scope_id = identities[scope]
    try:
        first = (await issuer(db, generation).reserve_batch([item], expires_at=deadline())).permits[
            0
        ]
        with pytest.raises(RawQueryError, match="accounting_budget_policy_requires_drain"):
            await db.execute_raw(
                "SELECT deltallm_accounting_sync_budget($1,$2,0::numeric,0::numeric,NULL,NULL,NULL)",
                scope.value,
                scope_id,
            )
        await owner(db).return_batch(
            [LocalPermitReturn(grant=first.proof.grant, first_unused_ordinal=1)],
            expires_at=deadline(),
        )
        await materialize(
            db,
            generation,
            [
                LocalPermitFinalization(
                    receipt=first.proof,
                    finalization=_finalization(item, AccountingOutcome.NOT_DISPATCHED),
                )
            ],
        )
        assert await _settle_grants(db, generation) == 1
        await db.execute_raw(
            "SELECT deltallm_accounting_sync_budget($1,$2,0::numeric,0::numeric,NULL,NULL,NULL)",
            scope.value,
            scope_id,
        )
        denied = (
            await issuer(db, generation).reserve_batch([fresh(item)], expires_at=deadline())
        ).permits[0]
        assert denied.decision is ReserveDecision.BUDGET_EXHAUSTED
    finally:
        await db.execute_raw(
            "DELETE FROM deltallm_organizationtable WHERE organization_id=$1",
            attribution.organization_id,
        )


async def test_older_grant_without_scope_data_requires_generation_drain(accounting_db):
    clients, generation = accounting_db
    db = clients[0]
    item = _reservation(generation, str(uuid4()), explicit_window=False)
    permit = (await issuer(db, generation).reserve_batch([item], expires_at=deadline())).permits[0]
    await db.execute_raw(
        "UPDATE deltallm_accounting_grants SET policy_attribution=NULL WHERE generation=$1",
        generation,
    )
    with pytest.raises(RawQueryError, match="accounting_budget_policy_requires_drain"):
        await organization(db, generation, limit=10)
    await owner(db).return_batch(
        [LocalPermitReturn(grant=permit.proof.grant, first_unused_ordinal=1)], expires_at=deadline()
    )
    await materialize(
        db,
        generation,
        [
            LocalPermitFinalization(
                receipt=permit.proof,
                finalization=_finalization(item, AccountingOutcome.NOT_DISPATCHED),
            )
        ],
    )
    assert await _settle_grants(db, generation) == 1


@pytest.mark.parametrize("scope", list(AccountingScope))
async def test_direct_reservation_also_fences_new_budget_scope(accounting_db, scope):
    clients, generation = accounting_db
    db = clients[0]
    item = _reservation(generation, str(uuid4()), explicit_window=False)
    repository = AccountingProtocolRepository(db, grants_enabled=False)
    permit = (await repository.reserve_batch([item], expires_at=deadline()))[0]
    assert permit.decision is ReserveDecision.DISPATCH
    attribution = item.attribution
    scope_id = {
        AccountingScope.API_KEY: attribution.api_key,
        AccountingScope.USER: attribution.user_id,
        AccountingScope.TEAM: attribution.team_id,
        AccountingScope.ORGANIZATION: attribution.organization_id,
        AccountingScope.TEAM_MODEL: attribution.team_id + ":" + attribution.model,
    }[scope]
    with pytest.raises(RawQueryError, match="accounting_budget_policy_requires_drain"):
        await db.execute_raw(
            "SELECT deltallm_accounting_sync_budget($1,$2,0::numeric,0::numeric,NULL,NULL,NULL)",
            scope.value,
            scope_id,
        )
    await repository.finalize_batch(
        [_finalization(item, AccountingOutcome.NOT_DISPATCHED)], expires_at=deadline()
    )
    await db.execute_raw(
        "SELECT deltallm_accounting_sync_budget($1,$2,0::numeric,0::numeric,NULL,NULL,NULL)",
        scope.value,
        scope_id,
    )


@pytest.mark.parametrize("character", ["\U0001f600", "\x01"])
async def test_scope_snapshot_preserves_existing_unicode_and_escaped_identity_bounds(
    accounting_db, character
):
    clients, generation = accounting_db
    db = clients[0]
    item = _reservation(generation, str(uuid4()), explicit_window=False)
    item = item.model_copy(
        update={
            "attribution": item.attribution.model_copy(
                update={
                    field: character * 256
                    for field in ("api_key", "user_id", "team_id", "organization_id", "model")
                }
            )
        }
    )
    # Check the existing public contract, not only an unchecked model copy.
    item = type(item).model_validate(item.model_dump(mode="python"))
    permit = (await issuer(db, generation).reserve_batch([item], expires_at=deadline())).permits[0]
    assert permit.decision is ReserveDecision.DISPATCH
    rows = await db.query_raw(
        "SELECT octet_length(policy_attribution::text) AS bytes FROM deltallm_accounting_grants WHERE generation=$1",
        generation,
    )
    assert 2048 < rows[0]["bytes"] <= 8192
    await owner(db).return_batch(
        [LocalPermitReturn(grant=permit.proof.grant, first_unused_ordinal=1)], expires_at=deadline()
    )
    await materialize(
        db,
        generation,
        [
            LocalPermitFinalization(
                receipt=permit.proof,
                finalization=_finalization(item, AccountingOutcome.NOT_DISPATCHED),
            )
        ],
    )
    assert await _settle_grants(db, generation) == 1
