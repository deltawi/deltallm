"""Immutable local issue and return proofs for database-funded capacity."""

from __future__ import annotations

from datetime import timedelta

from pydantic import AwareDatetime, Field, model_validator

from src.billing.accounting_protocol import (
    AccountingFinalization,
    PreissuedPermitClaim,
    PreissuedPermitGrant,
)
from src.billing.selector_charge import FrozenBillingContract


class LocalPermitGrant(PreissuedPermitGrant):
    """Keep database deadlines and a conservative local observation anchor."""

    dispatch_expires_at: AwareDatetime
    observed_at: AwareDatetime
    observed_monotonic: float = Field(ge=0, allow_inf_nan=False, repr=False)

    @model_validator(mode="after")
    def validate_deadlines(self) -> LocalPermitGrant:
        if not self.observed_at < self.dispatch_expires_at <= self.expires_at:
            raise ValueError("local grant has no live dispatch horizon")
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
