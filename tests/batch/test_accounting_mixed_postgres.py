import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from src.batch.accounting_delivery import NativeBatchCompletionDelivery
from src.batch.accounting_native import NativeBatchBilling
from src.batch.models import BatchItemCreate
from src.batch.repository import BatchRepository
from src.batch.repositories.accounting_repository import BatchAccountingRepository
from src.billing.accounting_admission import (
    admit_accounting_reservation,
    reservation_audit_envelope,
)
from src.billing.accounting_finalization import accounting_audit_envelope
from src.billing.accounting_protocol import AccountingAttempt, request_fingerprint
from src.billing.accounting_terminal_preparation import prepare_accounting_charge
from src.billing.operation_reservation import SoftSelectorOperation, token_price_allowance
from src.billing.realtime_accounting_bounds import RealtimeCostBounds
from src.billing.realtime_native import NativeRealtimeBilling
from src.billing.selector_native import NativeSelectorBilling
from src.db.realtime_billing import RealtimeBillingRepository
from src.models.requests import ChatCompletionRequest
from src.models.responses import UserAPIKeyAuth
from src.router.router import Deployment
from tests.realtime.test_pricing import charge_context, receipt
from tests.test_accounting_protocol_postgres import accounting_db as _accounting_db
from tests.test_accounting_role_runtime_postgres import graph
from tests.test_batch_db_integration import batch_db as _batch_db, _seed_batch_file
from tests.test_selector_charge_db_integration import selector_billing_db as _identity_db
from tests.accounting_read_model_fixtures import wait_for_native_projection

pytestmark = pytest.mark.postgres
accounting_db = _accounting_db
selector_billing_db = _identity_db
batch_db = _batch_db


def deadline():
    return asyncio.get_running_loop().time() + 3


async def batch_subjects(db, service, charge):
    key, model = charge.attribution.api_key, charge.attribution.model_group
    repository = BatchRepository(db)
    file_id = await _seed_batch_file(repository)
    job = await repository.create_job(
        endpoint="/v1/chat/completions",
        input_file_id=file_id,
        model=model,
        metadata=None,
        created_by_api_key=key,
        created_by_user_id=key,
        created_by_team_id=key,
        created_by_organization_id=key,
        status="in_progress",
        total_items=2,
    )
    payload = ChatCompletionRequest(
        model=model, messages=[{"role": "user", "content": "hello"}], max_tokens=8
    )
    await repository.create_items(
        job.batch_id,
        [
            BatchItemCreate(
                line_number=index,
                custom_id=str(index),
                request_body=payload.model_dump(mode="json"),
            )
            for index in (1, 2)
        ],
    )
    items = await repository.claim_items(batch_id=job.batch_id, worker_id="batch", limit=2)
    owner = NativeBatchBilling(
        service, BatchAccountingRepository(db), tier_policy=None, max_provider_attempts=3
    )
    auth = UserAPIKeyAuth(api_key=key, user_id=key, team_id=key, organization_id=key)
    executions = [owner.bind(job, item, worker_id="batch", auth=auth) for item in items]
    deployment = Deployment(
        deployment_id="batch",
        model_name=model,
        deltallm_params={"model": model, "custom_llm_provider": "openai"},
        model_info={
            "max_input_tokens": 100,
            "max_output_tokens": 10,
            "input_cost_per_token": "0.00001",
            "output_cost_per_token": "0.00002",
            "batch_input_cost_per_token": "0.000001",
            "batch_output_cost_per_token": "0.000002",
        },
    )
    return repository, owner, executions, payload, auth, deployment


async def other_features(service, db, charge, template):
    operation = SoftSelectorOperation(
        attribution=charge.attribution,
        owner_token=uuid4(),
        pricing=charge.pricing,
        admission_allowance=token_price_allowance(
            charge.pricing, input_tokens=1000, output_tokens=64
        ),
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    selector = NativeSelectorBilling(service).bind(operation, max_input_tokens=1000)
    await selector.reserve(operation, expires_at=deadline())
    await selector.dispatch(operation, component="selector", expires_at=deadline())
    await selector.accept_selector(operation, charge, expires_at=deadline())
    await selector.close()
    context = charge_context(charge.attribution.api_key)
    context = replace(
        context,
        attribution=replace(context.attribution, model=charge.attribution.model_group),
        cost_bounds=RealtimeCostBounds(input_tokens=100, output_tokens=10),
    )
    realtime = NativeRealtimeBilling(service, RealtimeBillingRepository(db))
    await realtime.check_owner(context)
    turn = str(uuid4())
    await realtime.dispatch(turn, context, expires_at=datetime.now(UTC) + timedelta(minutes=5))
    usage = receipt(context.attribution.session_id)
    await realtime.accept(turn, context, usage)
    await realtime.close(context.attribution.session_id)
    expected = charge.customer_charge + context.customer.cost(usage.usage)
    for cache_hit in (False, True):
        attribution = template.reservation.attribution.model_copy(
            update={"call_type": "chat_completion"}
        )
        operation_id = uuid4()
        reservation = template.reservation.model_copy(
            update={
                "operation_id": operation_id,
                "owner_token": uuid4(),
                "attribution": attribution,
                "request_fingerprint": request_fingerprint(
                    operation_kind="cached" if cache_hit else "http", payload={}
                ),
                "allowance": Decimal(".0001"),
                "pricing_snapshot": {},
                "audit_envelope": reservation_audit_envelope(
                    attribution, operation_id=operation_id, allowance=Decimal(".0001")
                ),
            }
        )
        handle = await admit_accounting_reservation(
            service,
            reservation=reservation,
            attempt=AccountingAttempt(
                deployment_id="answer",
                provider="openai",
                model=attribution.model,
                pricing_snapshot={},
            ),
        )
        spend = charge.spend_payload() | {
            "request_id": str(operation_id),
            "call_type": "chat_completion",
            "cost_exact": ".000005",
            "provider_cost_exact": ".000002",
            "cache_hit": cache_hit,
            "metadata": {},
        }
        terminal = prepare_accounting_charge(
            handle,
            payload=spend,
            occurred_at=datetime.now(UTC),
            audit_envelope=accounting_audit_envelope(
                handle, event_id=uuid4(), status="success", metadata={}
            ),
        )
        await service.finalize_operation(handle, terminal)
        expected += Decimal(".000005")
    return expected


@pytest.mark.parametrize("outcome", ["completed", "unknown", "unsent"])
async def test_native_batch_and_mixed_features_share_one_scope_and_replay_authority(
    accounting_db,
    selector_billing_db,
    batch_db,
    outcome,
):
    clients, generation = accounting_db
    db, charge = selector_billing_db
    key, model = charge.attribution.api_key, charge.attribution.model_group
    for table, column in (
        ("deltallm_verificationtoken", "token"),
        ("deltallm_usertable", "user_id"),
        ("deltallm_teamtable", "team_id"),
        ("deltallm_organizationtable", "organization_id"),
    ):
        await db.execute_raw(f"UPDATE {table} SET max_budget=.05 WHERE {column}=$1", key)
    await db.execute_raw(
        "UPDATE deltallm_teamtable SET model_max_budget=jsonb_build_object($2::text,.05) WHERE team_id=$1",
        key,
        model,
    )
    projection, request, api, transport, _, projected = await graph(clients, generation)
    try:
        await projection.start(expires_at=deadline())
        await request.start(expires_at=deadline())
        await api.start(expires_at=deadline())
        service = api.local.service
        repository, owner, executions, payload, auth, deployment = await batch_subjects(
            batch_db, service, charge
        )
        if outcome == "unsent":
            for execution in executions:
                await owner._prepare_attempt(
                    execution,
                    payload=payload,
                    auth=auth,
                    deployment=deployment,
                    selector_expected=False,
                )
        else:
            await owner.prepare_group(
                executions, payloads=[payload] * 2, auths=[auth] * 2, deployment=deployment
            )
        expected, provisional = Decimal(0), Decimal(0)
        if outcome == "completed":
            rows = []
            for execution in executions:
                usage = {"prompt_tokens": 5, "completion_tokens": 2}
                outbox = execution.completion_payload(
                    {
                        "batch_id": execution.claim.batch_id,
                        "item_id": execution.claim.item_id,
                        "completed_at": datetime.now(UTC).isoformat(),
                    },
                    usage,
                )
                rows.append(
                    {
                        "item_id": execution.claim.item_id,
                        "claim_epoch": execution.claim.claim_epoch,
                        "response_body": {"usage": usage},
                        "usage": usage,
                        "provider_cost": 0.00009,
                        "billed_cost": 0.000009,
                        "outbox_payload": outbox,
                    }
                )
                expected += execution.checkpoint.terminal.exact_charge
            assert (
                await repository.complete_items_with_outbox_bulk(items=rows, worker_id="batch")
                == "completed"
            )
            assert (
                await repository.complete_items_with_outbox_bulk(items=rows, worker_id="batch")
                == "already_completed"
            )
            assert (
                await repository.claim_items(
                    batch_id=executions[0].claim.batch_id, worker_id="other"
                )
                == []
            )
            expected += await other_features(
                service, db, charge, executions[0].checkpoint.operation
            )
        else:
            await owner.close_group(executions)
            if outcome == "unknown":
                provisional = sum(
                    execution.checkpoint.operation.reservation.allowance for execution in executions
                )
        records = await repository.claim_completion_outbox_due(
            worker_id="delivery", lease_seconds=30
        )
        assert len(records) == 2
        for record in records:
            recovered = NativeBatchCompletionDelivery(service, repository)
            assert await recovered.deliver(record, worker_id="delivery") is (outcome == "completed")
            assert not await recovered.deliver(record, worker_id="delivery")
        saved = await repository.list_completion_outbox_by_item_ids(
            [execution.claim.item_id for execution in executions]
        )
        assert all(record.status == "sent" for record in saved)
        await api.close(expires_at=deadline())
        await wait_for_native_projection(clients[0], projected.progress, generation, timeout=3)
        windows = await db.query_raw(
            "SELECT scope_type,committed_exact::text AS committed,reserved_exact::text AS reserved,"
            "provisional_exact::text AS provisional FROM deltallm_accounting_budget_windows "
            "WHERE generation=$1 AND scope_id=ANY($2::text[])",
            generation,
            [key, f"{key}:{model}"],
        )
        assert {row["scope_type"] for row in windows} == {
            "api_key",
            "user",
            "team",
            "organization",
            "team_model",
        }
        assert len(windows) == 5
        for row in windows:
            assert Decimal(row["committed"]) == expected
            assert Decimal(row["reserved"]) == 0
            assert Decimal(row["provisional"]) == provisional
        assert (
            await db.query_raw("SELECT id FROM deltallm_spendlog_events WHERE api_key=$1", key)
            == []
        )
        facts = await db.query_raw(
            "SELECT request_id FROM deltallm_accounting_usage_facts_v2 WHERE protocol_generation=$1 AND api_key=$2",
            generation,
            key,
        )
        assert len(facts) == (6 if outcome == "completed" else 0)
    finally:
        try:
            await api.close(expires_at=deadline())
        finally:
            try:
                await request.close(expires_at=deadline())
            finally:
                try:
                    await projection.close(expires_at=deadline())
                finally:
                    await transport.close()
