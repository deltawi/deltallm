"""Freeze one terminal result before the first persistence attempt."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from uuid import UUID, uuid5

from src.billing.accounting.accounting_protocol import (
    AccountingFinalization,
    AccountingOperationHandle,
    AccountingOutcome,
)
from src.billing.money import canonical_money, money_string
from src.request_identity import valid_request_id


def prepare_accounting_charge(
    operation: AccountingOperationHandle,
    *,
    payload: Mapping[str, object],
    occurred_at: datetime,
    audit_envelope: dict[str, object],
    event_id: UUID | None = None,
) -> AccountingFinalization:
    accepted = dict(payload)
    charge = canonical_money(accepted.get("cost_exact", accepted.get("cost")))
    accepted["cost"] = money_string(charge)
    accepted["cost_exact"] = money_string(charge)
    accepted["spend_event_version"] = 2
    reservation = operation.reservation
    # Non-HTTP callers also need a stable reporting ID across terminal retries.
    if not valid_request_id(accepted.get("request_id")):
        accepted["request_id"] = str(reservation.operation_id)
    return AccountingFinalization(
        protocol_generation=reservation.protocol_generation,
        operation_id=reservation.operation_id,
        owner_token=reservation.owner_token,
        request_fingerprint=reservation.request_fingerprint,
        component_id="provider",
        event_id=event_id or uuid5(reservation.operation_id, "provider-finalization:v2"),
        outcome=AccountingOutcome.COMPLETED,
        exact_charge=charge,
        spend_payload=accepted,
        audit_envelope=audit_envelope,
        occurred_at=occurred_at,
        unresolved_attempts=max(0, len(operation.attempts) - 1),
    )


def prepare_accounting_uncertain(
    operation: AccountingOperationHandle,
    *,
    reason: str,
    occurred_at: datetime,
    audit_envelope: dict[str, object],
    event_id: UUID | None = None,
) -> AccountingFinalization:
    reservation = operation.reservation
    return AccountingFinalization(
        protocol_generation=reservation.protocol_generation,
        operation_id=reservation.operation_id,
        owner_token=reservation.owner_token,
        request_fingerprint=reservation.request_fingerprint,
        component_id="provider",
        event_id=event_id or uuid5(reservation.operation_id, "provider-finalization:v2"),
        outcome=AccountingOutcome.UNCERTAIN,
        audit_envelope=audit_envelope,
        occurred_at=occurred_at,
        uncertainty_reason=reason,
    )


def prepare_accounting_not_dispatched(
    operation: AccountingOperationHandle,
    *,
    occurred_at: datetime,
    audit_envelope: dict[str, object],
    event_id: UUID | None = None,
) -> AccountingFinalization:
    reservation = operation.reservation
    return AccountingFinalization(
        protocol_generation=reservation.protocol_generation,
        operation_id=reservation.operation_id,
        owner_token=reservation.owner_token,
        request_fingerprint=reservation.request_fingerprint,
        component_id="provider",
        event_id=event_id or uuid5(reservation.operation_id, "provider-finalization:v2"),
        outcome=AccountingOutcome.NOT_DISPATCHED,
        audit_envelope=audit_envelope,
        occurred_at=occurred_at,
    )
