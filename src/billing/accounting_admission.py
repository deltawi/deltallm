"""Use one dispatch decision and audit contract for every accounting caller."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from datetime import timedelta
from typing import Literal
from uuid import UUID

from src.billing.accounting_local_leases import LocalAccountingHandle, LocalDispatchPermit
from src.billing.accounting_protocol import (
    AccountingAttribution,
    AccountingAttempt,
    AccountingOperationHandle,
    AccountingReservation,
    ReserveDecision,
)
from src.billing.accounting_service import AccountingProtocolService
from src.billing.spend_operations import SpendPersistenceUnavailable
from src.models.errors import BudgetExceededError

ACCOUNTING_RECOVERY_LIFETIME = timedelta(minutes=14)


async def admit_accounting_reservation(
    accounting: AccountingProtocolService,
    *,
    reservation: AccountingReservation,
    attempt: AccountingAttempt,
) -> AccountingOperationHandle:
    try:
        permit = await accounting.reserve(reservation)
    except asyncio.CancelledError:
        raise
    except Exception:
        raise SpendPersistenceUnavailable() from None
    if permit.decision is ReserveDecision.BUDGET_EXHAUSTED:
        raise BudgetExceededError()
    if permit.decision is not ReserveDecision.DISPATCH:
        raise SpendPersistenceUnavailable()
    if (
        permit.dispatch_token is None
        or permit.accounting_partition is None
        or permit.protocol_generation != reservation.protocol_generation
        or permit.operation_id != reservation.operation_id
    ):
        raise SpendPersistenceUnavailable()
    if isinstance(permit, LocalDispatchPermit):
        try:
            return LocalAccountingHandle(
                reservation=reservation,
                dispatch_token=permit.dispatch_token,
                accounting_partition=permit.accounting_partition,
                attempts=(attempt,),
                proof=permit.proof,
            )
        except (TypeError, ValueError):
            raise SpendPersistenceUnavailable() from None
    if accounting.requires_local_proof:
        raise SpendPersistenceUnavailable()
    return AccountingOperationHandle(
        reservation=reservation,
        dispatch_token=permit.dispatch_token,
        accounting_partition=permit.accounting_partition,
        attempts=(attempt,),
    )


def reservation_audit_envelope(
    attribution: AccountingAttribution,
    *,
    operation_id: UUID,
    allowance: Decimal,
    action: Literal[
        "ACCOUNTING_PROVIDER_RESERVED", "ACCOUNTING_CACHE_RESERVED"
    ] = "ACCOUNTING_PROVIDER_RESERVED",
    cache_hit: bool = False,
) -> dict[str, object]:
    metadata: dict[str, str | bool] = {"allowance_exact": str(allowance)}
    if cache_hit:
        metadata["cache_hit"] = True
    event = {
        "action": action,
        "organization_id": attribution.organization_id,
        "actor_type": "api_key",
        "actor_id": attribution.user_id or attribution.api_key,
        "api_key": attribution.api_key,
        "resource_type": "model",
        "resource_id": attribution.model,
        "request_id": str(operation_id),
        "correlation_id": str(operation_id),
        "status": "success",
        "metadata": metadata,
        "event_id": f"{operation_id}:reservation",
    }
    payload = {"event": event, "payloads": [], "critical": True}
    return {
        "event_id": f"{operation_id}:reservation:audit",
        "record_type": "audit_event",
        "organization_id": attribution.organization_id,
        "payload": payload,
        "redacted_payload": payload,
    }
