"""Compact local proofs use receiver clocks, not another process's clock."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import timedelta
import json
import math

from pydantic import AwareDatetime, Field, TypeAdapter, model_validator

from src.billing.accounting.permits.accounting_local_leases import (
    LocalDispatchPermit,
    LocalPermitFinalization,
    LocalPermitGrant,
    LocalPermitReceipt,
)
from src.billing.accounting.journal.accounting_local_terminal import freeze_local_terminals
from src.billing.accounting.accounting_protocol import (
    AccountingFinalization,
    AccountingReservation,
    DispatchPermit,
    PreissuedPermitGrant,
    ReserveDecision,
)
from src.billing.accounting.accounting_snapshots import reservation_snapshot
from src.billing.accounting.journal.accounting_terminal_snapshots import (
    LocalTerminalValue,
    freeze_terminal_snapshots,
)
from src.billing.charges.selector_charge import FrozenBillingContract


class WireLocalGrant(PreissuedPermitGrant):
    dispatch_expires_at: AwareDatetime
    observed_at: AwareDatetime

    @model_validator(mode="after")
    def validate_horizons(self) -> WireLocalGrant:
        self.restore(observed_monotonic=0)
        return self

    def restore(self, *, observed_monotonic: float) -> LocalPermitGrant:
        return LocalPermitGrant(**self.model_dump(), observed_monotonic=observed_monotonic)


class CompactLocalPermit(DispatchPermit):
    protocol_generation: int = Field(ge=1, le=2**63 - 1)
    grant: WireLocalGrant | None = Field(default=None, repr=False)
    permit_ordinal: int | None = Field(default=None, ge=0, le=1023)

    @model_validator(mode="after")
    def validate_local_proof(self) -> CompactLocalPermit:
        if self.decision is ReserveDecision.DISPATCH:
            if (
                self.grant is None
                or self.permit_ordinal is None
                or self.grant.protocol_generation != self.protocol_generation
                or self.grant.accounting_partition != self.accounting_partition
                or self.permit_ordinal >= self.grant.operation_limit
            ):
                raise ValueError("compact local dispatch proof is incomplete or mismatched")
        elif any(
            value is not None
            for value in (
                self.grant,
                self.permit_ordinal,
                self.dispatch_token,
                self.accounting_partition,
            )
        ):
            raise ValueError("compact non-dispatch reply cannot contain a dispatch proof")
        return self


class WireLocalTerminal(FrozenBillingContract):
    grant: WireLocalGrant = Field(repr=False)
    permit_ordinal: int = Field(ge=0, le=1023)
    reservation: AccountingReservation = Field(repr=False)
    finalization: AccountingFinalization = Field(repr=False)

    def restore(self, *, observed_monotonic: float) -> LocalPermitFinalization:
        return LocalPermitFinalization(
            receipt=LocalPermitReceipt(
                grant=self.grant.restore(observed_monotonic=observed_monotonic),
                permit_ordinal=self.permit_ordinal,
                reservation=self.reservation,
            ),
            finalization=self.finalization,
        )


_COMPACT_BATCH = TypeAdapter(list[CompactLocalPermit])
_TERMINAL_BATCH = TypeAdapter(list[WireLocalTerminal])


def compact_local_permit(
    permit: DispatchPermit, *, observed_monotonic: float
) -> CompactLocalPermit:
    if not isinstance(permit, LocalDispatchPermit):
        if permit.decision is ReserveDecision.DISPATCH:
            raise ValueError("compact local dispatch requires its complete issue proof")
        return CompactLocalPermit(**permit.model_dump())
    grant = permit.proof.grant
    elapsed = observed_monotonic - grant.observed_monotonic
    if not math.isfinite(elapsed) or elapsed < 0:
        raise ValueError("compact grant observation uses an invalid local clock")
    fields = grant.model_dump(exclude={"observed_monotonic"})
    fields["observed_at"] = grant.observed_at + timedelta(seconds=elapsed)
    return CompactLocalPermit(
        **permit.model_dump(exclude={"proof"}),
        grant=WireLocalGrant(**fields),
        permit_ordinal=permit.proof.permit_ordinal,
    )


def expand_compact_local_permits(
    body: bytes,
    reservations: Sequence[AccountingReservation],
    *,
    observed_monotonic: float,
    now: float,
    minimum_validity_seconds: float = 0.1,
) -> tuple[DispatchPermit, ...]:
    if len(body) > 1_048_576 or not 1 <= len(reservations) <= 256:
        raise ValueError("compact local reply exceeds its batch limit")
    if (
        not math.isfinite(observed_monotonic)
        or observed_monotonic < 0
        or not math.isfinite(now)
        or now < observed_monotonic
        or not math.isfinite(minimum_validity_seconds)
        or not 0 <= minimum_validity_seconds <= 5
    ):
        raise ValueError("compact local reply uses an invalid receiver clock")
    values = _COMPACT_BATCH.validate_json(body)
    if len(values) != len(reservations):
        raise ValueError("compact local reply has the wrong entry count")
    result = []
    for wire, reservation in zip(values, reservations, strict=True):
        item, _ = reservation_snapshot(reservation)
        if (
            wire.operation_id != item.operation_id
            or wire.protocol_generation != item.protocol_generation
        ):
            raise ValueError("compact local reply does not match its request")
        fields = wire.model_dump(exclude={"grant", "permit_ordinal"})
        if wire.decision is ReserveDecision.DISPATCH:
            grant = wire.grant.restore(observed_monotonic=observed_monotonic)
            operation_deadline = (
                observed_monotonic + (item.expires_at - grant.observed_at).total_seconds()
            )
            if (
                grant.dispatch_deadline <= now + minimum_validity_seconds
                or operation_deadline <= now
            ):
                raise ValueError("compact local reply has no live dispatch lifetime")
            proof = LocalPermitReceipt(
                grant=grant, permit_ordinal=wire.permit_ordinal, reservation=item
            )
            result.append(LocalDispatchPermit(**fields, proof=proof))
        else:
            result.append(DispatchPermit(**fields))
    return tuple(result)


def wire_local_terminals(values: Sequence[LocalTerminalValue], *, generation: int) -> bytes:
    frozen = freeze_terminal_snapshots(values, generation=generation)
    if not frozen:
        raise ValueError("local wire batch must contain 1 to 256 entries")
    body = b"[" + b",".join(value.wire_document() for value in frozen) + b"]"
    if len(body) > 1_048_576:
        raise ValueError("local wire batch exceeds its byte limit")
    return body


def restore_wire_terminals(
    body: bytes, *, generation: int, observed_monotonic: float
) -> tuple[LocalPermitFinalization, ...]:
    if len(body) > 1_048_576:
        raise ValueError("wire local terminal batch exceeds its byte limit")
    values = _TERMINAL_BATCH.validate_json(body)
    if not 1 <= len(values) <= 256:
        raise ValueError("wire local terminal batch exceeds its entry limit")
    return freeze_local_terminals(
        tuple(value.restore(observed_monotonic=observed_monotonic) for value in values),
        generation=generation,
    )


def compact_local_batch(permits: Sequence[DispatchPermit], *, observed_monotonic: float) -> bytes:
    return _batch_json(
        tuple(
            compact_local_permit(permit, observed_monotonic=observed_monotonic)
            for permit in permits
        )
    )


def _batch_json(values: Sequence[CompactLocalPermit | WireLocalTerminal]) -> bytes:
    if not 1 <= len(values) <= 256:
        raise ValueError("local wire batch must contain 1 to 256 entries")
    body = json.dumps(
        [value.model_dump(mode="json") for value in values],
        allow_nan=False,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    if len(body) > 1_048_576:
        raise ValueError("local wire batch exceeds its byte limit")
    return body
