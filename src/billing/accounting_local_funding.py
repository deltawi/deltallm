"""Prepare finite local funding demands without consuming cursor prefixes."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from uuid import UUID

from src.billing.accounting_local_cursors import LocalCursorStore, LocalGrantCursor
from src.billing.accounting_local_leases import LocalPermitGrant, LocalPermitReceipt
from src.billing.accounting_local_receipts import LocalReceiptStore
from src.billing.accounting_protocol import AccountingReservation, DispatchPermit, ReserveDecision
from src.billing.accounting_snapshots import reservation_bytes
from src.billing.durable_microbatch import DurableBatchFull
from src.billing.preissued_permits import PermitSubject


@dataclass(slots=True, repr=False)
class LocalFundingDemand:
    subject: PermitSubject
    items: list[AccountingReservation]
    warm: LocalGrantCursor | None
    funded: list[LocalPermitGrant] = field(default_factory=list)
    denial: ReserveDecision = ReserveDecision.CAPACITY_EXHAUSTED

    @property
    def needed(self) -> int:
        available = (0 if self.warm is None else self.warm.remaining) + sum(
            grant.operation_limit for grant in self.funded
        )
        return max(0, len(self.items) - available)


def freeze_local_reservations(
    values: Sequence[AccountingReservation], *, generation: int
) -> tuple[AccountingReservation, ...]:
    if len(values) > 256:
        raise ValueError("local reservation batch exceeds its entry limit")
    result, size = [], 2
    for value in values:
        encoded = reservation_bytes(value)
        size += len(encoded) + bool(result)
        if size > 1_048_576:
            raise DurableBatchFull("local reservation batch exceeds its byte limit")
        item = AccountingReservation.model_validate_json(encoded)
        if item.protocol_generation != generation:
            raise ValueError("local reservation uses a stale generation")
        result.append(item)
    if len({item.operation_id for item in result}) != len(result):
        raise ValueError("one local reservation batch cannot repeat an operation")
    return tuple(result)


def prepare_local_demands(
    items: Sequence[AccountingReservation],
    cursors: LocalCursorStore,
    receipts: LocalReceiptStore,
    *,
    now: float,
    minimum_validity_seconds: float,
) -> tuple[dict[PermitSubject, LocalFundingDemand], list[DispatchPermit]]:
    demands: dict[PermitSubject, LocalFundingDemand] = {}
    replays = []
    for item in items:
        if receipts.matches(item):
            replays.append(_denial(item, ReserveDecision.REPLAY))
            continue
        subject = PermitSubject.from_reservation(item)
        if subject not in demands:
            cursor = cursors.get(subject)
            if (
                cursor is not None
                and cursor.grant.dispatch_deadline <= now + minimum_validity_seconds
            ):
                cursors.retire(subject)
                cursor = None
            demands[subject] = LocalFundingDemand(subject, [], cursor)
        demands[subject].items.append(item)
    return demands, replays


def preflight_local_capacity(
    demands: Sequence[LocalFundingDemand],
    cursors: LocalCursorStore,
    receipts: LocalReceiptStore,
) -> None:
    # A funded grant must serve at least one requested item. Two rounds therefore
    # retain at most 256 new grants, not two grants for every possible subject.
    entries = sum(min(2, demand.needed) for demand in demands)
    cursor_bytes = sum(
        min(2, demand.needed) * (demand.subject.retained_bytes + 2048) for demand in demands
    )
    receipt_count = sum(len(demand.items) for demand in demands)
    receipt_bytes = sum(
        10240 + len(reservation_bytes(item)) for demand in demands for item in demand.items
    )
    # The receipt estimate includes both maximum-length grant identifiers. This
    # checks capacity only; accepted proofs alone change the retained counters.
    if not cursors.capacity_for(entries=entries, retained_bytes=cursor_bytes):
        raise DurableBatchFull("local funded cursor capacity is full")
    if not receipts.capacity_for(entries=receipt_count, retained_bytes=receipt_bytes):
        raise DurableBatchFull("local issued receipt capacity is full")


def select_local_prefixes(
    demands: Sequence[LocalFundingDemand],
) -> tuple[list[LocalPermitReceipt], list[DispatchPermit]]:
    proposed, denied = [], []
    for demand in demands:
        grants = ([] if demand.warm is None else [demand.warm]) + [
            LocalGrantCursor(demand.subject, grant) for grant in demand.funded
        ]
        grant_index = 0
        ordinal = grants[0].next_ordinal if grants else 0
        for item in demand.items:
            while (
                grant_index < len(grants) and ordinal >= grants[grant_index].grant.operation_limit
            ):
                grant_index += 1
                ordinal = grants[grant_index].next_ordinal if grant_index < len(grants) else 0
            if grant_index == len(grants):
                denied.append(_denial(item, demand.denial))
            else:
                proposed.append(
                    LocalPermitReceipt(
                        grant=grants[grant_index].grant, permit_ordinal=ordinal, reservation=item
                    )
                )
                ordinal += 1
    return proposed, denied


def _denial(item: AccountingReservation, decision: ReserveDecision) -> DispatchPermit:
    return DispatchPermit(
        protocol_generation=item.protocol_generation,
        operation_id=item.operation_id,
        decision=decision,
    )


def local_operation_order(items: Sequence[AccountingReservation]) -> tuple[UUID, ...]:
    return tuple(item.operation_id for item in items)
