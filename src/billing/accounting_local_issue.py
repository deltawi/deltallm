"""Prepare complete local issue proofs, then commit both stores without an await."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
import json
import math
from uuid import UUID

from src.billing.accounting_local_cursors import LocalCursorStore
from src.billing.accounting_local_leases import LocalDispatchPermit, LocalPermitReceipt
from src.billing.accounting_local_receipts import LocalReceiptStore, RetainedLocalReceipt
from src.billing.accounting_protocol import DispatchPermit, ReserveDecision
from src.billing.durable_microbatch import DurableBatchFull


@dataclass(frozen=True, slots=True, repr=False)
class LocalIssuedBatch:
    permits: tuple[DispatchPermit, ...]
    proofs: tuple[RetainedLocalReceipt, ...]


class LocalIssueCommit:
    """Call only after all funding awaits end, while the admission owner is held."""

    def __init__(
        self,
        cursors: LocalCursorStore,
        receipts: LocalReceiptStore,
        *,
        minimum_validity_seconds: float = 0.1,
    ) -> None:
        if not 0 <= minimum_validity_seconds <= 5:
            raise ValueError("local issue minimum validity is invalid")
        self._cursors = cursors
        self._receipts = receipts
        self._minimum_validity = minimum_validity_seconds

    def commit(
        self,
        proposed: Sequence[LocalPermitReceipt],
        *,
        expires_at: float,
        non_dispatch: Sequence[DispatchPermit] = (),
        operation_order: Sequence[UUID] | None = None,
    ) -> LocalIssuedBatch:
        if len(proposed) + len(non_dispatch) > 256:
            raise ValueError("local issue must contain at most 256 entries")
        _caller_deadline(expires_at)
        proofs, receipts = _freeze(proposed)
        if not self._receipts.prepare_issue(proofs):
            raise DurableBatchFull("local issued receipt capacity is full")
        plans = self._cursors.prepare_issue(receipts)
        result = _result(receipts, proofs, non_dispatch, operation_order, self._cursors.generation)
        retained = tuple(
            (receipt.reservation.operation_id, proof)
            for receipt, proof in zip(receipts, proofs, strict=True)
        )
        _caller_deadline(expires_at)
        _dispatch_deadlines(receipts, self._minimum_validity)
        # All validation, allocation, and result construction precede this commit.
        self._cursors._commit_issue(plans)
        self._receipts._commit_issue(retained)
        return result


def _result(
    receipts: Sequence[LocalPermitReceipt],
    proofs: tuple[RetainedLocalReceipt, ...],
    non_dispatch: Sequence[DispatchPermit],
    operation_order: Sequence[UUID] | None,
    generation: int,
) -> LocalIssuedBatch:
    permits = {receipt.reservation.operation_id: _permit(receipt) for receipt in receipts}
    for value in non_dispatch:
        permit = DispatchPermit.model_validate(value.model_dump())
        if (
            permit.decision is ReserveDecision.DISPATCH
            or permit.protocol_generation != generation
            or permit.operation_id in permits
        ):
            raise ValueError("local non-dispatch result does not match its batch")
        permits[permit.operation_id] = permit
    order = tuple(permits) if operation_order is None else tuple(operation_order)
    if len(order) != len(permits) or set(order) != set(permits):
        raise ValueError("local result order does not match its batch")
    result = LocalIssuedBatch(permits=tuple(permits[key] for key in order), proofs=proofs)
    size = len(
        json.dumps(
            {
                # Each full proof is already present once in the proofs array.
                "permits": [
                    permit.model_dump(mode="json", exclude={"proof"}) for permit in result.permits
                ],
                "proofs": [receipt.model_dump(mode="json") for receipt in receipts],
            },
            allow_nan=False,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    )
    if size > 1_048_576:
        raise DurableBatchFull("local complete reply exceeds its byte limit")
    return result


def _freeze(
    proposed: Sequence[LocalPermitReceipt],
) -> tuple[tuple[RetainedLocalReceipt, ...], tuple[LocalPermitReceipt, ...]]:
    proofs, receipts = [], []
    size = 2
    for item in proposed:
        proof = RetainedLocalReceipt.freeze(item)
        receipt = proof.restore()
        size += len(
            json.dumps(
                receipt.model_dump(mode="json"),
                allow_nan=False,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ) + bool(proofs)
        if size > 1_048_576:
            raise DurableBatchFull("local issue proof batch exceeds its byte limit")
        proofs.append(proof)
        receipts.append(receipt)
    return tuple(proofs), tuple(receipts)


def _permit(receipt: LocalPermitReceipt) -> DispatchPermit:
    item = receipt.reservation
    return LocalDispatchPermit(
        protocol_generation=item.protocol_generation,
        operation_id=item.operation_id,
        decision=ReserveDecision.DISPATCH,
        dispatch_token=item.owner_token,
        accounting_partition=receipt.grant.accounting_partition,
        proof=receipt,
    )


def _caller_deadline(expires_at: float) -> None:
    if not math.isfinite(expires_at) or expires_at <= asyncio.get_running_loop().time():
        raise ValueError("local issue caller deadline is invalid or expired")


def _dispatch_deadlines(receipts: Sequence[LocalPermitReceipt], minimum_validity: float) -> None:
    now = asyncio.get_running_loop().time()
    for receipt in receipts:
        grant = receipt.grant
        operation_deadline = (
            grant.observed_monotonic
            + (receipt.reservation.expires_at - grant.observed_at).total_seconds()
        )
        if grant.dispatch_deadline <= now + minimum_validity or operation_deadline <= now:
            raise ValueError("local issue has no live dispatch lifetime")
