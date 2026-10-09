"""Use the shared finalizer's frozen attribution and redacted audit contracts."""

import asyncio

from src.billing.accounting.reporting.accounting_read_model_claims import READ_MODEL_PROJECTION

from src.billing.accounting.accounting_finalization import accounting_audit_envelope
from src.billing.accounting.accounting_protocol import (
    AccountingAttempt,
    AccountingOperationHandle,
    AccountingOutcome,
)
from tests.test_accounting_protocol_postgres import _finalization


async def wait_for_native_projection(db, progress, generation, *, timeout):
    """A first settled grant does not prove that later grants and reports drained."""
    async with asyncio.timeout(timeout):
        while True:
            progress.clear()
            rows = await db.query_raw(
                "SELECT NOT EXISTS (SELECT 1 FROM deltallm_accounting_grants "
                "WHERE generation=$1 AND state<>'closed') AND NOT EXISTS ("
                "SELECT 1 FROM deltallm_accounting_events e LEFT JOIN "
                "deltallm_accounting_projection_checkpoints c ON "
                "c.protocol_name=e.protocol_name AND c.generation=e.generation "
                "AND c.accounting_partition=e.accounting_partition AND c.projection_name=$2 "
                "WHERE e.generation=$1 AND e.event_type IN ('finalized','reconciled') "
                "AND (c.last_sequence IS NULL OR e.sequence>c.last_sequence)) AS drained",
                generation,
                READ_MODEL_PROJECTION,
            )
            if rows[0]["drained"]:
                return
            await progress.wait()


def reporting_handle(receipt):
    reservation = receipt.reservation
    attribution = reservation.attribution
    return AccountingOperationHandle(
        reservation=reservation,
        dispatch_token=reservation.owner_token,
        accounting_partition=receipt.grant.accounting_partition,
        attempts=(
            AccountingAttempt(
                deployment_id=attribution.deployment_id,
                provider=attribution.provider,
                model=attribution.model,
                pricing_snapshot=reservation.pricing_snapshot,
            ),
        ),
    )


def reporting_finalization(operation, outcome=AccountingOutcome.COMPLETED):
    terminal = _finalization(operation.reservation, outcome)
    attribution = operation.reservation.attribution
    payload = None
    if outcome is AccountingOutcome.COMPLETED:
        payload = {
            "request_id": str(operation.reservation.operation_id),
            "call_type": attribution.call_type,
            "api_key": attribution.api_key,
            "user_id": attribution.user_id,
            "team_id": attribution.team_id,
            "organization_id": attribution.organization_id,
            "owner_account_id": attribution.owner_account_id,
            "end_user_id": attribution.end_user_id,
            "model": attribution.model,
            "owner_snapshot_complete": True,
            "cost_exact": "0.6",
            "provider_cost_exact": "0.4",
            "start_time": terminal.occurred_at,
            "end_time": terminal.occurred_at,
            "cache_hit": False,
            "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
            "metadata": {
                "provider": attribution.provider,
                "deployment_model": attribution.model,
                "tags": ["native-report"],
                "billing": {"billing_unit": "token"},
            },
        }
    return terminal.model_copy(
        update={
            "spend_payload": payload,
            "audit_envelope": accounting_audit_envelope(
                operation,
                event_id=terminal.event_id,
                status="success" if outcome is AccountingOutcome.COMPLETED else "error",
                metadata={},
            ),
        }
    )
