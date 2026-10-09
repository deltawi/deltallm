"""Immutable local issue and return proofs for database-funded capacity."""

from __future__ import annotations

from datetime import timedelta

from pydantic import AwareDatetime, Field, model_validator

from src.billing.accounting.accounting_protocol import (
    AccountingFinalization,
    AccountingOperationHandle,
    DispatchPermit,
    PreissuedPermitClaim,
    PreissuedPermitGrant,
    ReserveDecision,
)
from src.billing.accounting.accounting_snapshots import reservation_snapshot
from src.billing.charges.selector_charge import FrozenBillingContract


class LocalPermitGrant(PreissuedPermitGrant):
    """A funding proof can remain returnable after its dispatch horizon expires."""

    dispatch_expires_at: AwareDatetime
    observed_at: AwareDatetime
    observed_monotonic: float = Field(ge=0, allow_inf_nan=False, repr=False)

    @model_validator(mode="after")
    def validate_deadlines(self) -> LocalPermitGrant:
        if not self.dispatch_expires_at <= self.expires_at or self.expires_at <= self.observed_at:
            raise ValueError("local grant has no live recovery horizon")
        if self.dispatch_expires_at > self.observed_at + timedelta(minutes=5):
            raise ValueError("local grant exceeds its dispatch lifetime bound")
        if self.expires_at > self.observed_at + timedelta(minutes=20):
            raise ValueError("local grant exceeds its recovery lifetime bound")
        return self

    @property
    def dispatch_deadline(self) -> float:
        return (
            self.observed_monotonic + (self.dispatch_expires_at - self.observed_at).total_seconds()
        )

    @property
    def recovery_deadline(self) -> float:
        return self.observed_monotonic + (self.expires_at - self.observed_at).total_seconds()


class LocalPermitReceipt(PreissuedPermitClaim):
    """An immutable local issue; a database claim is not yet required."""

    grant: LocalPermitGrant = Field(repr=False)

    @model_validator(mode="after")
    def validate_recovery_lifetime(self) -> LocalPermitReceipt:
        if self.reservation.expires_at > self.grant.expires_at:
            raise ValueError("local receipt exceeds its funded recovery lifetime")
        return self


class LocalDispatchPermit(DispatchPermit):
    """Carry the complete issue proof without changing assigned permits."""

    proof: LocalPermitReceipt = Field(repr=False)

    @model_validator(mode="after")
    def validate_issue(self) -> LocalDispatchPermit:
        proof, _ = _validated_issue_proof(self.proof)
        reservation = proof.reservation
        if (
            self.decision is not ReserveDecision.DISPATCH
            or self.protocol_generation != reservation.protocol_generation
            or self.operation_id != reservation.operation_id
            or self.dispatch_token != reservation.owner_token
            or self.accounting_partition != proof.grant.accounting_partition
        ):
            raise ValueError("local dispatch proof does not match its permit")
        object.__setattr__(self, "proof", proof)
        return self


class LocalAccountingHandle(AccountingOperationHandle):
    """Keep the issue proof when the request adds a provider attempt."""

    proof: LocalPermitReceipt = Field(repr=False)

    @model_validator(mode="after")
    def validate_issue(self) -> LocalAccountingHandle:
        proof, proof_document = _validated_issue_proof(self.proof)
        reservation, reservation_document = reservation_snapshot(self.reservation)
        if (
            proof_document != reservation_document
            or self.dispatch_token != proof.reservation.owner_token
            or self.accounting_partition != proof.grant.accounting_partition
        ):
            raise ValueError("local operation proof does not match its handle")
        object.__setattr__(self, "proof", proof)
        object.__setattr__(self, "reservation", reservation)
        return self


def _validated_issue_proof(proof: LocalPermitReceipt) -> tuple[LocalPermitReceipt, bytes]:
    # Model copies can bypass scalar validation. Recheck the full graph before
    # dispatch and give each handle its own request dictionaries.
    reservation, encoded = reservation_snapshot(proof.reservation)
    copy = LocalPermitReceipt(
        grant=LocalPermitGrant.model_validate(proof.grant.model_dump()),
        permit_ordinal=proof.permit_ordinal,
        reservation=reservation,
    )
    return copy, encoded


class LocalPermitReturn(FrozenBillingContract):
    grant: LocalPermitGrant = Field(repr=False)
    first_unused_ordinal: int = Field(ge=0, le=1024)

    @model_validator(mode="after")
    def validate_suffix(self) -> LocalPermitReturn:
        if self.first_unused_ordinal > self.grant.operation_limit:
            raise ValueError("local return exceeds its grant capacity")
        return self


class LocalPermitFinalization(FrozenBillingContract):
    receipt: LocalPermitReceipt = Field(repr=False)
    finalization: AccountingFinalization = Field(repr=False)

    @model_validator(mode="after")
    def validate_identity(self) -> LocalPermitFinalization:
        reservation = self.receipt.reservation
        finalization = self.finalization
        if (
            reservation.protocol_generation != finalization.protocol_generation
            or reservation.operation_id != finalization.operation_id
            or reservation.owner_token != finalization.owner_token
            or reservation.request_fingerprint != finalization.request_fingerprint
        ):
            raise ValueError("local finalization does not match its issue receipt")
        return self
