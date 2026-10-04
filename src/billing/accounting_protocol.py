"""Immutable contracts for the generation-fenced accounting protocol."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from src.billing.money import MONEY_MAX_ABS, canonical_money
from src.billing.selector_charge import FrozenBillingContract, Identifier

Money = Annotated[
    Decimal, Field(ge=0, lt=MONEY_MAX_ABS, allow_inf_nan=False, max_digits=38, decimal_places=18)
]
Fingerprint = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class AccountingScope(StrEnum):
    API_KEY = "api_key"
    USER = "user"
    TEAM = "team"
    ORGANIZATION = "organization"
    TEAM_MODEL = "team_model"


class AccountingOutcome(StrEnum):
    COMPLETED = "completed"
    NOT_DISPATCHED = "not_dispatched"
    UNCERTAIN = "uncertain"


class ReserveDecision(StrEnum):
    DISPATCH = "dispatch"
    REPLAY = "replay"
    BUDGET_EXHAUSTED = "budget_exhausted"
    CAPACITY_EXHAUSTED = "capacity_exhausted"


class AccountingAttribution(FrozenBillingContract):
    api_key: Identifier = Field(repr=False)
    user_id: Identifier | None = Field(default=None, repr=False)
    team_id: Identifier | None = Field(default=None, repr=False)
    organization_id: Identifier | None = Field(default=None, repr=False)
    owner_account_id: Identifier | None = Field(default=None, repr=False)
    end_user_id: Identifier | None = Field(default=None, repr=False)
    model: Identifier
    deployment_id: Identifier
    provider: Identifier
    call_type: Identifier


class AccountingAttempt(FrozenBillingContract):
    deployment_id: Identifier
    provider: Identifier
    model: Identifier
    pricing_snapshot: dict[str, object]

    @model_validator(mode="after")
    def validate_pricing_snapshot(self) -> AccountingAttempt:
        _bounded_json(self.pricing_snapshot, name="pricing snapshot", maximum=32_768)
        return self


class BudgetWindowRef(FrozenBillingContract):
    window_id: UUID
    scope_type: AccountingScope
    scope_id: Identifier
    policy_generation: int = Field(ge=0, le=2**63 - 1)


class AccountingReservation(FrozenBillingContract):
    """A server-created reservation; client correlation IDs are never its identity."""

    protocol_generation: int = Field(ge=1, le=2**63 - 1)
    operation_id: UUID
    owner_token: UUID = Field(repr=False)
    request_fingerprint: Fingerprint
    attribution: AccountingAttribution = Field(repr=False)
    allowance: Money
    # Empty means resolve every active authoritative window for the attribution
    # inside the reservation statement. Explicit refs are useful for immutable
    # control snapshots but are always revalidated by PostgreSQL.
    windows: tuple[BudgetWindowRef, ...] = Field(default=(), max_length=8)
    pricing_snapshot: dict[str, object]
    audit_envelope: dict[str, object]
    expires_at: AwareDatetime

    @model_validator(mode="after")
    def validate_reservation(self) -> AccountingReservation:
        if len({window.scope_type for window in self.windows}) != len(self.windows):
            raise ValueError("accounting reservation contains duplicate budget scopes")
        if len({window.window_id for window in self.windows}) != len(self.windows):
            raise ValueError("accounting reservation contains duplicate budget windows")
        _bounded_json(self.pricing_snapshot, name="pricing snapshot", maximum=32_768)
        _bounded_json(self.audit_envelope, name="audit envelope", maximum=65_536)
        canonical_money(self.allowance)
        return self


class PreissuedPermitGrant(FrozenBillingContract):
    """Durable capacity for one owner under one database fence."""

    protocol_generation: int = Field(ge=1, le=2**63 - 1)
    grant_id: Identifier
    grantee_id: Identifier = Field(repr=False)
    fence_token: UUID = Field(repr=False)
    accounting_partition: int = Field(ge=0, le=63)
    allowance: Money
    operation_limit: int = Field(ge=1, le=1024)
    expires_at: AwareDatetime


class PreissuedPermitAllocation(FrozenBillingContract):
    """Stable refill identity created before any database attempt."""

    reservation: AccountingReservation = Field(repr=False)
    fence_token: UUID = Field(repr=False)
    target_operations: int = Field(ge=1, le=1024)


class PreissuedPermitClaim(FrozenBillingContract):
    """One ordinal from an allocated grant; this is not a dispatch proof."""

    grant: PreissuedPermitGrant = Field(repr=False)
    permit_ordinal: int = Field(ge=0, le=1023)
    reservation: AccountingReservation = Field(repr=False)

    @model_validator(mode="after")
    def validate_claim(self) -> PreissuedPermitClaim:
        if self.reservation.protocol_generation != self.grant.protocol_generation:
            raise ValueError("permit claim uses a stale accounting generation")
        if self.reservation.allowance != self.grant.allowance:
            raise ValueError("permit claim allowance does not match its grant")
        if self.permit_ordinal >= self.grant.operation_limit:
            raise ValueError("permit ordinal exceeds its grant capacity")
        return self


class DispatchPermit(FrozenBillingContract):
    protocol_generation: int
    operation_id: UUID
    decision: ReserveDecision
    dispatch_token: UUID | None = Field(default=None, repr=False)
    accounting_partition: int | None = None

    @model_validator(mode="after")
    def validate_decision(self) -> DispatchPermit:
        dispatch = self.decision is ReserveDecision.DISPATCH
        if dispatch != (self.dispatch_token is not None and self.accounting_partition is not None):
            raise ValueError("only a new durable reservation can grant provider dispatch")
        return self


class AccountingOperationHandle(FrozenBillingContract):
    """Request-local proof that provider dispatch was durably admitted."""

    reservation: AccountingReservation = Field(repr=False)
    dispatch_token: UUID = Field(repr=False)
    accounting_partition: int = Field(ge=0, le=63)
    attempts: tuple[AccountingAttempt, ...] = Field(min_length=1, max_length=128)


class AccountingFinalization(FrozenBillingContract):
    protocol_generation: int = Field(ge=1, le=2**63 - 1)
    operation_id: UUID
    owner_token: UUID = Field(repr=False)
    request_fingerprint: Fingerprint
    component_id: Identifier
    event_id: UUID
    outcome: AccountingOutcome
    exact_charge: Money | None = None
    spend_payload: dict[str, object] | None = None
    audit_envelope: dict[str, object]
    occurred_at: AwareDatetime
    uncertainty_reason: Identifier | None = None
    unresolved_attempts: int = Field(default=0, ge=0, le=127)

    @model_validator(mode="after")
    def validate_outcome(self) -> AccountingFinalization:
        if self.outcome is AccountingOutcome.COMPLETED:
            if self.exact_charge is None or self.spend_payload is None:
                raise ValueError("completed accounting requires an exact charge and spend payload")
            if self.uncertainty_reason is not None:
                raise ValueError("completed accounting cannot have an uncertainty reason")
        elif self.outcome is AccountingOutcome.NOT_DISPATCHED:
            if self.exact_charge not in (None, Decimal(0)) or self.spend_payload is not None:
                raise ValueError("not-dispatched accounting cannot contain provider spend")
            if self.uncertainty_reason is not None:
                raise ValueError("not-dispatched accounting cannot have an uncertainty reason")
            if self.unresolved_attempts:
                raise ValueError("not-dispatched accounting cannot have unresolved attempts")
        else:
            if self.exact_charge is not None or self.spend_payload is not None:
                raise ValueError("uncertain accounting uses its full provisional debit")
            if self.uncertainty_reason is None:
                raise ValueError("uncertain accounting requires a bounded reason")
            if self.unresolved_attempts:
                raise ValueError("uncertain accounting already represents the unresolved attempt")
        if self.spend_payload is not None:
            _bounded_json(self.spend_payload, name="spend payload", maximum=262_144)
        _bounded_json(self.audit_envelope, name="audit envelope", maximum=65_536)
        return self


class FinalizationReceipt(FrozenBillingContract):
    protocol_generation: int
    operation_id: UUID
    event_sequence: int = Field(ge=1)
    outcome: AccountingOutcome
    replayed: bool = False


def request_fingerprint(*, operation_kind: str, payload: dict[str, object]) -> str:
    """Hash a canonical server-owned intent without retaining request content."""

    encoded = json.dumps(
        {"kind": operation_kind, "payload": payload},
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
        default=_json_default,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _bounded_json(value: dict[str, object], *, name: str, maximum: int) -> None:
    try:
        size = len(
            json.dumps(
                value,
                allow_nan=False,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
                default=_json_default,
            ).encode()
        )
    except (TypeError, ValueError):
        raise ValueError(f"{name} is not valid JSON") from None
    if size > maximum:
        raise ValueError(f"{name} exceeds its durable size limit")


def _json_default(value: object) -> str:
    if isinstance(value, (datetime, Decimal, UUID)):
        return str(value)
    raise TypeError(f"unsupported durable value: {type(value).__name__}")
