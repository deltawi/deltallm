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


class UnqualifiedAccountingReceipt(ValueError):
    """Provider evidence cannot be represented as a completed accounting event."""


_TOKEN_TOTAL_FIELDS = (
    "prompt_tokens",
    "completion_tokens",
    "input_audio_tokens",
    "output_audio_tokens",
)
_REPORTING_COUNT_FIELDS = (
    *_TOKEN_TOTAL_FIELDS,
    "total_tokens",
    "prompt_tokens_cached",
    "completion_tokens_cached",
    "input_characters",
    "output_characters",
    "rerank_units",
)


def _validate_reporting_counts(payload: Mapping[str, object]) -> None:
    usage = payload.get("usage")
    metadata = payload.get("metadata")
    billing = metadata.get("billing") if isinstance(metadata, Mapping) else None
    snapshot = billing.get("usage_snapshot") if isinstance(billing, Mapping) else None
    if usage is not None and not isinstance(usage, Mapping):
        raise UnqualifiedAccountingReceipt("provider_usage_unqualified")
    if snapshot is not None and not isinstance(snapshot, Mapping):
        raise UnqualifiedAccountingReceipt("provider_usage_unqualified")
    usage = usage if usage is not None else {}
    snapshot = snapshot if snapshot is not None else {}
    counts: dict[str, int] = {}
    # Reporting prefers explicit usage over the billing snapshot.
    for source in (usage, snapshot):
        for field in _REPORTING_COUNT_FIELDS:
            value = source.get(field)
            if value is None or field in counts:
                continue
            if type(value) is not int or not 0 <= value < 2**31:
                raise UnqualifiedAccountingReceipt("provider_usage_unqualified")
            counts[field] = value
    image_count = next(
        (
            value
            for value in (usage.get("images"), snapshot.get("image_count"), snapshot.get("images"))
            if value is not None
        ),
        0,
    )
    if type(image_count) is not int or not 0 <= image_count < 2**31:
        raise UnqualifiedAccountingReceipt("provider_usage_unqualified")
    # PostgreSQL evaluates the fallback sum even when total_tokens is present.
    if sum(counts.get(field, 0) for field in _TOKEN_TOTAL_FIELDS) >= 2**31:
        raise UnqualifiedAccountingReceipt("provider_usage_unqualified")


def accounting_audit_token_counts(
    payload: Mapping[str, object],
) -> tuple[int | None, int | None]:
    """Return the input and output token counts of a charge for its audit event.

    The source order is the same as reporting: explicit usage first, then the
    billing usage snapshot. A count that is missing or that does not fit the
    audit column is None, never zero, so an unknown count stays unknown.
    """

    usage = payload.get("usage")
    metadata = payload.get("metadata")
    billing = metadata.get("billing") if isinstance(metadata, Mapping) else None
    snapshot = billing.get("usage_snapshot") if isinstance(billing, Mapping) else None
    sources = [source for source in (usage, snapshot) if isinstance(source, Mapping)]

    def count(field: str) -> int | None:
        for source in sources:
            value = source.get(field)
            if value is None:
                continue
            if type(value) is int and 0 <= value < 2**31:
                return value
            return None
        return None

    return count("prompt_tokens"), count("completion_tokens")


def _with_audit_token_counts(
    audit_envelope: dict[str, object], payload: Mapping[str, object]
) -> dict[str, object]:
    """Add the charge's token counts to its audit event where the caller left them unset.

    Every completed charge passes here (live calls, Realtime, batch and the
    selector), so each of their audit events gets the counts. The result is a
    pure function of its inputs, so a replay of the same charge builds the same
    envelope. The redacted copy gets the counts too: they are not content.
    """

    input_tokens, output_tokens = accounting_audit_token_counts(payload)
    if input_tokens is None and output_tokens is None:
        return audit_envelope
    result = dict(audit_envelope)
    copies: dict[int, object] = {}
    for key in ("payload", "redacted_payload"):
        body = audit_envelope.get(key)
        if id(body) in copies:
            result[key] = copies[id(body)]
            continue
        event = body.get("event") if isinstance(body, Mapping) else None
        if not isinstance(event, Mapping):
            continue
        event = dict(event)
        if event.get("input_tokens") is None:
            event["input_tokens"] = input_tokens
        if event.get("output_tokens") is None:
            event["output_tokens"] = output_tokens
        result[key] = copies[id(body)] = {**body, "event": event}
    return result


def prepare_accounting_charge(
    operation: AccountingOperationHandle,
    *,
    payload: Mapping[str, object],
    occurred_at: datetime,
    audit_envelope: dict[str, object],
    event_id: UUID | None = None,
) -> AccountingFinalization:
    accepted = dict(payload)
    try:
        charge = canonical_money(accepted.get("cost_exact", accepted.get("cost")))
    except ValueError:
        raise UnqualifiedAccountingReceipt("provider_charge_unqualified") from None
    reservation = operation.reservation
    if not 0 <= charge <= reservation.allowance:
        raise UnqualifiedAccountingReceipt("provider_charge_outside_allowance")
    _validate_reporting_counts(accepted)
    accepted["cost"] = money_string(charge)
    accepted["cost_exact"] = money_string(charge)
    accepted["spend_event_version"] = 2
    # Non-HTTP callers also need a stable reporting ID across terminal retries.
    if not valid_request_id(accepted.get("request_id")):
        accepted["request_id"] = str(reservation.operation_id)
    # Reporting rejects a completed event without both times, and one rejected
    # event blocks its whole partition. Use the terminal instant for a missing time.
    for field in ("start_time", "end_time"):
        if accepted.get(field) is None:
            accepted[field] = occurred_at
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
        audit_envelope=_with_audit_token_counts(audit_envelope, accepted),
        occurred_at=occurred_at,
        unresolved_attempts=operation.unresolved_attempts(),
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
