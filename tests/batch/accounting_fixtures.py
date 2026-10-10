from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from src.batch.accounting_checkpoint import BatchAccountingCheckpoint
from src.batch.selector_identity import batch_selector_operation_id
from src.billing.accounting.accounting_protocol import (
    AccountingAttribution,
    AccountingAttempt,
    AccountingOperationHandle,
    AccountingReservation,
    request_fingerprint,
)
from src.billing.accounting.journal.accounting_terminal_preparation import (
    prepare_accounting_uncertain,
)


def checkpoint_for(claim, *, generation=7):
    operation_id = batch_selector_operation_id(claim.batch_id, claim.item_id)
    reservation = AccountingReservation(
        protocol_generation=generation,
        operation_id=operation_id,
        owner_token=uuid4(),
        request_fingerprint=request_fingerprint(operation_kind="batch", payload={}),
        attribution=AccountingAttribution(
            api_key=claim.api_key,
            model="test-model",
            deployment_id="test-deployment",
            provider="openai",
            call_type="completion_batch",
        ),
        allowance=Decimal("0.1"),
        pricing_snapshot={},
        audit_envelope={},
        expires_at=datetime.now(UTC) + timedelta(minutes=14),
    )
    handle = AccountingOperationHandle(
        reservation=reservation,
        dispatch_token=reservation.owner_token,
        accounting_partition=0,
        attempts=(
            AccountingAttempt(
                deployment_id="test-deployment",
                provider="openai",
                model="test-model",
                pricing_snapshot={},
            ),
        ),
    )
    return BatchAccountingCheckpoint(
        operation_id=operation_id,
        claim_epoch=claim.claim_epoch,
        operation=handle,
    )


def uncertain(checkpoint, *, claim_epoch=None):
    terminal = prepare_accounting_uncertain(
        checkpoint.operation,
        reason="batch_owner_lost",
        occurred_at=datetime.now(UTC),
        audit_envelope={},
    )
    return BatchAccountingCheckpoint(
        operation_id=checkpoint.operation_id,
        claim_epoch=claim_epoch or checkpoint.claim_epoch,
        operation=checkpoint.operation,
        terminal=terminal,
    )
