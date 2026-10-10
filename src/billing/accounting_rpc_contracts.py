"""Typed signed funding and return contracts contain no process clock values."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import timedelta
import json
import math
from uuid import UUID

from pydantic import Field, TypeAdapter, model_validator

from src.billing.accounting_local_leases import LocalPermitGrant, LocalPermitReturn
from src.billing.accounting_local_wire import WireLocalGrant, WireLocalTerminal
from src.billing.accounting_protocol import PreissuedPermitAllocation, ReserveDecision
from src.billing.selector_charge import FrozenBillingContract


class AccountingRpcHealth(FrozenBillingContract):
    generation: int = Field(ge=1, le=2**63 - 1)
    status: str = Field(pattern="^ready$", max_length=5)


class RpcRequest(FrozenBillingContract):
    generation: int = Field(ge=1, le=2**63 - 1)
    budget_ms: int = Field(ge=1, le=5000)


class LocalFundingRequest(RpcRequest):
    owner_id: str = Field(min_length=1, max_length=219)
    values: tuple[PreissuedPermitAllocation, ...] = Field(min_length=1, max_length=256)

    @model_validator(mode="after")
    def validate_batch(self) -> LocalFundingRequest:
        if any(item.reservation.protocol_generation != self.generation for item in self.values):
            raise ValueError("funding request uses another generation")
        if len({item.fence_token for item in self.values}) != len(self.values):
            raise ValueError("funding request repeats a fence")
        return self


class LocalFundingReply(FrozenBillingContract):
    allocation_fence_token: UUID
    decision: ReserveDecision
    grant: WireLocalGrant | None = Field(default=None, repr=False)

    @model_validator(mode="after")
    def validate_reply(self) -> LocalFundingReply:
        if self.decision is ReserveDecision.DISPATCH:
            if self.grant is None or self.grant.fence_token != self.allocation_fence_token:
                raise ValueError("funding reply has an incomplete proof")
        elif self.grant is not None or self.decision not in {
            ReserveDecision.BUDGET_EXHAUSTED,
            ReserveDecision.CAPACITY_EXHAUSTED,
        }:
            raise ValueError("funding reply has an invalid denial")
        return self


class WireLocalReturn(FrozenBillingContract):
    grant: WireLocalGrant = Field(repr=False)
    first_unused_ordinal: int = Field(ge=0, le=1024)

    @model_validator(mode="after")
    def validate_suffix(self) -> WireLocalReturn:
        if self.first_unused_ordinal > self.grant.operation_limit:
            raise ValueError("return exceeds the funded suffix")
        return self

    def restore(self, *, observed_monotonic: float) -> LocalPermitReturn:
        return LocalPermitReturn(
            grant=self.grant.restore(observed_monotonic=observed_monotonic),
            first_unused_ordinal=self.first_unused_ordinal,
        )


class LocalReturnRequest(RpcRequest):
    owner_id: str = Field(min_length=1, max_length=219)
    values: tuple[WireLocalReturn, ...] = Field(min_length=1, max_length=256)

    @model_validator(mode="after")
    def validate_batch(self) -> LocalReturnRequest:
        for value in self.values:
            grant = value.grant
            if (
                grant.protocol_generation != self.generation
                or grant.grantee_id != f"{self.owner_id}:{grant.fence_token}"
            ):
                raise ValueError("return uses another generation or owner")
        if len({value.grant.grant_id for value in self.values}) != len(self.values):
            raise ValueError("return repeats a grant")
        return self


class LocalReturnReply(FrozenBillingContract):
    grant_id: str = Field(min_length=1, max_length=256)
    fence_token: UUID
    first_unused_ordinal: int = Field(ge=0, le=1024)
    returned_operations: int = Field(ge=0, le=1024)


class LocalTerminalRequest(RpcRequest):
    values: tuple[WireLocalTerminal, ...] = Field(min_length=1, max_length=256)

    @model_validator(mode="after")
    def validate_batch(self) -> LocalTerminalRequest:
        if any(value.finalization.protocol_generation != self.generation for value in self.values):
            raise ValueError("terminal uses another generation")
        if len({value.finalization.operation_id for value in self.values}) != len(self.values):
            raise ValueError("terminal repeats an operation")
        return self


FUNDING_REPLIES = TypeAdapter(list[LocalFundingReply])
RETURN_REPLIES = TypeAdapter(list[LocalReturnReply])


def funding_reply(
    allocation: PreissuedPermitAllocation,
    value: LocalPermitGrant | ReserveDecision,
    *,
    now: float,
) -> LocalFundingReply:
    if isinstance(value, ReserveDecision):
        return LocalFundingReply(allocation_fence_token=allocation.fence_token, decision=value)
    elapsed = now - value.observed_monotonic
    if not math.isfinite(elapsed) or elapsed < 0:
        raise ValueError("funding reply uses an invalid local clock")
    fields = value.model_dump(exclude={"observed_monotonic"})
    fields["observed_at"] = value.observed_at + timedelta(seconds=elapsed)
    return LocalFundingReply(
        allocation_fence_token=allocation.fence_token,
        decision=ReserveDecision.DISPATCH,
        grant=WireLocalGrant(**fields),
    )


def rpc_batch_bytes(values: Sequence[FrozenBillingContract]) -> bytes:
    if not 1 <= len(values) <= 256:
        raise ValueError("accounting RPC batch exceeds its entry limit")
    parts, size = [], 2
    for value in values:
        part = value.model_dump_json().encode()
        size += len(part) + bool(parts)
        if size > 1_048_576:
            raise ValueError("accounting RPC batch exceeds its byte limit")
        parts.append(part)
    return b"[" + b",".join(parts) + b"]"


def rpc_request_bytes(
    *, generation: int, budget_ms: int, values: bytes, owner_id: str | None = None
) -> bytes:
    header: dict[str, object] = {"generation": generation, "budget_ms": budget_ms}
    if owner_id is not None:
        header["owner_id"] = owner_id
    encoded = json.dumps(header, ensure_ascii=True, separators=(",", ":")).encode()[:-1]
    encoded += b',"values":' + values + b"}"
    if len(encoded) > 1_048_576:
        raise ValueError("accounting RPC request exceeds its byte limit")
    return encoded
