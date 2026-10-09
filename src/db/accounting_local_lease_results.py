"""Validate local lease acknowledgements against their immutable proofs."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal, localcontext

from src.billing.accounting_local_leases import (
    LocalPermitFinalization,
    LocalPermitGrant,
    LocalPermitReturn,
)
from src.billing.accounting_protocol import (
    FinalizationReceipt,
    PreissuedPermitAllocation,
    ReserveDecision,
)
from src.db.accounting_permit_results import allocation_result as permit_allocation_result
from src.db.accounting_permit_results import claim_identity_matches, invalid_result


def timestamp(value: object) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("local lease timestamp must be timezone-aware")
    return value


def allocation_result(
    item: PreissuedPermitAllocation, row: Mapping[str, object], owner: str, observed: float
) -> LocalPermitGrant | ReserveDecision:
    try:
        observed_at = timestamp(row["observed_at"])
    except (KeyError, TypeError, ValueError):
        raise invalid_result() from None
    grant = permit_allocation_result(item, row, owner, observed_at=observed_at)
    if isinstance(grant, ReserveDecision):
        if row.get("dispatch_expires_at") is not None:
            raise invalid_result()
        return grant
    try:
        return LocalPermitGrant(
            **grant.model_dump(),
            dispatch_expires_at=timestamp(row["dispatch_expires_at"]),
            observed_at=observed_at,
            observed_monotonic=observed,
        )
    except (KeyError, TypeError, ValueError, ArithmeticError):
        raise invalid_result() from None


def same_funding(
    first: LocalPermitGrant | ReserveDecision, second: LocalPermitGrant | ReserveDecision
) -> bool:
    if isinstance(first, ReserveDecision) or isinstance(second, ReserveDecision):
        return first == second
    excluded = {"observed_at", "observed_monotonic"}
    return first.model_dump(exclude=excluded) == second.model_dump(exclude=excluded)


def recovered_grant(
    item: PreissuedPermitAllocation, row: Mapping[str, object], owner: str, observed: float
) -> LocalPermitGrant | ReserveDecision:
    if (
        row.get("protocol_name") != "primary"
        or row.get("generation") != item.reservation.protocol_generation
        or row.get("subject_matches") is not True
        or row.get("dispatch_mode") != "preissued"
        or row.get("local_dispatch") is not True
        or row.get("state") != "active"
        or row.get("consumed_operations") != 0
        or row.get("returned_operations") != 0
    ):
        raise invalid_result()
    return allocation_result(
        item,
        {**row, "decision": "dispatch", "allowance_exact": row.get("unit_allowance_exact")},
        owner,
        observed,
    )


def return_payload(item: LocalPermitReturn) -> dict[str, object]:
    return {
        "grant_id": item.grant.grant_id,
        "fence_token": str(item.grant.fence_token),
        "first_unused_ordinal": item.first_unused_ordinal,
    }


def return_result(item: LocalPermitReturn, row: Mapping[str, object]) -> int:
    expected = item.grant.operation_limit - item.first_unused_ordinal
    if (
        str(row.get("fence_token")) != str(item.grant.fence_token)
        or type(row.get("first_unused_ordinal")) is not int
        or row.get("first_unused_ordinal") != item.first_unused_ordinal
        or type(row.get("returned_operations")) is not int
        or row.get("returned_operations") != expected
    ):
        raise invalid_result()
    return expected


def recovered_return(item: LocalPermitReturn, row: Mapping[str, object], owner: str) -> int | None:
    grant = item.grant
    try:
        if (
            row.get("protocol_name") != "primary"
            or row.get("generation") != grant.protocol_generation
            or row.get("dispatch_mode") != "preissued"
            or row.get("local_dispatch") is not True
            or row.get("state") not in {"active", "draining", "closed"}
            or row.get("grantee_id") != grant.grantee_id
            or grant.grantee_id != f"{owner}:{grant.fence_token}"
            or str(row.get("fence_token")) != str(grant.fence_token)
            or row.get("operation_limit") != grant.operation_limit
            or Decimal(str(row.get("unit_allowance_exact"))) != grant.allowance
            or timestamp(row.get("dispatch_expires_at")) != grant.dispatch_expires_at
            or timestamp(row.get("expires_at")) != grant.expires_at
        ):
            raise invalid_result()
        expected = grant.operation_limit - item.first_unused_ordinal
        count = row.get("returned_operations")
        money = Decimal(str(row.get("returned_exact")))
        if type(count) is not int:
            raise invalid_result()
        if count == 0 and money == 0 and expected > 0:
            return None
        with localcontext() as context:
            context.prec = 80
            expected_money = grant.allowance * expected
        if count != expected or money != expected_money:
            raise invalid_result()
        return expected
    except (KeyError, TypeError, ValueError, ArithmeticError):
        raise invalid_result() from None


def finalization_payload(item: LocalPermitFinalization) -> dict[str, object]:
    receipt = item.receipt
    return {
        "grant_id": receipt.grant.grant_id,
        "grantee_id": receipt.grant.grantee_id,
        "fence_token": str(receipt.grant.fence_token),
        "permit_ordinal": receipt.permit_ordinal,
        "reservation": receipt.reservation.model_dump(mode="json"),
        "finalization": item.finalization.model_dump(mode="json"),
    }


def finalization_result(
    item: LocalPermitFinalization, row: Mapping[str, object]
) -> FinalizationReceipt:
    finalization = item.finalization
    if (
        row.get("outcome") != finalization.outcome.value
        or type(row.get("event_sequence")) is not int
        or type(row.get("replayed")) is not bool
    ):
        raise invalid_result()
    try:
        return FinalizationReceipt(
            protocol_generation=finalization.protocol_generation,
            operation_id=finalization.operation_id,
            event_sequence=row["event_sequence"],
            outcome=finalization.outcome,
            replayed=row["replayed"],
        )
    except (KeyError, TypeError, ValueError):
        raise invalid_result() from None


def recovered_finalization(
    item: LocalPermitFinalization, row: Mapping[str, object]
) -> FinalizationReceipt:
    finalization = item.finalization
    payload = row.get("payload_json")
    serialized = finalization.model_dump(mode="json")
    try:
        if (
            not claim_identity_matches(item.receipt, row)
            or row.get("accounting_state") not in {"finalized", "released", "provisional"}
            or str(row.get("event_id")) != str(finalization.event_id)
            or row.get("component_id") != finalization.component_id
            or row.get("event_protocol") != "primary"
            or row.get("event_generation") != finalization.protocol_generation
            or row.get("event_operation_id") != str(finalization.operation_id)
            or row.get("event_type") != "finalized"
            or row.get("outcome") != finalization.outcome.value
            or not isinstance(payload, dict)
            or payload.get("spend") != serialized["spend_payload"]
            or (
                None
                if payload.get("exact_charge") is None
                else Decimal(str(payload["exact_charge"]))
            )
            != finalization.exact_charge
            or payload.get("uncertainty_reason") != finalization.uncertainty_reason
            or payload.get("unresolved_attempts", 0) != finalization.unresolved_attempts
            or row.get("audit_envelope_json") != serialized["audit_envelope"]
            or timestamp(row.get("occurred_at")) != finalization.occurred_at
        ):
            raise invalid_result()
        return finalization_result(
            item, {**row, "event_sequence": row.get("sequence"), "replayed": True}
        )
    except (KeyError, TypeError, ValueError, ArithmeticError):
        raise invalid_result() from None
