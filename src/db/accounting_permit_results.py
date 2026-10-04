"""Validate each durable permit proof against the caller's immutable contract."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from src.billing.accounting_protocol import (
    DispatchPermit,
    PreissuedPermitAllocation,
    PreissuedPermitClaim,
    PreissuedPermitGrant,
    ReserveDecision,
)
from src.db.accounting_calls import AccountingProtocolUnavailable, AccountingResultFailure

AllocationResult = PreissuedPermitGrant | ReserveDecision


def allocation_result(
    item: PreissuedPermitAllocation,
    row: Mapping[str, object],
    owner: str,
    *,
    observed_at: datetime | None = None,
) -> AllocationResult:
    try:
        decision = ReserveDecision(str(row["decision"]))
    except (KeyError, ValueError):
        raise invalid_result() from None
    if decision in {ReserveDecision.BUDGET_EXHAUSTED, ReserveDecision.CAPACITY_EXHAUSTED}:
        if any(
            row.get(field) is not None
            for field in (
                "grant_id",
                "grantee_id",
                "fence_token",
                "accounting_partition",
                "allowance_exact",
                "operation_limit",
                "expires_at",
            )
        ):
            raise invalid_result()
        return decision
    if decision is not ReserveDecision.DISPATCH:
        raise invalid_result()
    return grant_from_row(item, row, owner, observed_at=observed_at)


def grant_from_row(
    item: PreissuedPermitAllocation,
    row: Mapping[str, object],
    owner: str,
    *,
    observed_at: datetime | None = None,
) -> PreissuedPermitGrant:
    try:
        timestamp = row["expires_at"]
        if isinstance(timestamp, str):
            timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        grant = PreissuedPermitGrant(
            protocol_generation=item.reservation.protocol_generation,
            grant_id=row["grant_id"],
            grantee_id=row["grantee_id"],
            fence_token=UUID(str(row["fence_token"])),
            accounting_partition=row["accounting_partition"],
            allowance=Decimal(str(row["allowance_exact"])),
            operation_limit=row["operation_limit"],
            expires_at=timestamp,
        )
        if (
            grant.fence_token != item.fence_token
            or grant.grantee_id != f"{owner}:{item.fence_token}"
            or grant.allowance != item.reservation.allowance
            or grant.operation_limit > item.target_operations
            or grant.expires_at <= (observed_at or datetime.now(UTC))
        ):
            raise invalid_result()
        return grant
    except (KeyError, TypeError, ValueError, ArithmeticError):
        raise invalid_result() from None


def claim_result(item: PreissuedPermitClaim, row: Mapping[str, object]) -> DispatchPermit:
    try:
        decision = ReserveDecision(str(row["decision"]))
        if decision not in {ReserveDecision.DISPATCH, ReserveDecision.REPLAY}:
            raise invalid_result()
        dispatch = decision is ReserveDecision.DISPATCH
        if row.get("dispatch_token") != (
            str(item.reservation.owner_token) if dispatch else None
        ) or row.get("accounting_partition") != (
            item.grant.accounting_partition if dispatch else None
        ):
            raise invalid_result()
        return DispatchPermit(
            protocol_generation=item.reservation.protocol_generation,
            operation_id=item.reservation.operation_id,
            decision=decision,
            dispatch_token=item.reservation.owner_token if dispatch else None,
            accounting_partition=item.grant.accounting_partition if dispatch else None,
        )
    except (KeyError, ValueError):
        raise invalid_result() from None


def claim_identity_matches(item: PreissuedPermitClaim, row: Mapping[str, object]) -> bool:
    reservation = item.reservation
    return (
        row.get("owner_token") == str(reservation.owner_token)
        and row.get("request_fingerprint") == reservation.request_fingerprint
        and row.get("snapshot") == reservation.model_dump(mode="json")
        and row.get("accounting_protocol") == "primary"
        and row.get("accounting_generation") == reservation.protocol_generation
        and row.get("accounting_partition") == item.grant.accounting_partition
        and row.get("accounting_grant_id") == item.grant.grant_id
        and row.get("accounting_permit_ordinal") == item.permit_ordinal
        and str(row.get("accounting_grant_fence_token")) == str(item.grant.fence_token)
    )


def claim_payload(item: PreissuedPermitClaim) -> dict[str, object]:
    return {
        "grant_id": item.grant.grant_id,
        "grantee_id": item.grant.grantee_id,
        "fence_token": str(item.grant.fence_token),
        "permit_ordinal": item.permit_ordinal,
        "reservation": item.reservation.model_dump(mode="json"),
    }


def invalid_result() -> AccountingProtocolUnavailable:
    return AccountingProtocolUnavailable(AccountingResultFailure.INVALID_RESULT)
