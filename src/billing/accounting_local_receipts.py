"""Byte-bounded immutable local issue proofs, with no financial eviction policy."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import json
from uuid import UUID

from src.billing.accounting_local_leases import LocalPermitGrant, LocalPermitReceipt
from src.billing.accounting_protocol import AccountingReservation, FinalizationReceipt


def reservation_bytes(item: AccountingReservation) -> bytes:
    validated = AccountingReservation.model_validate(item.model_dump())
    return json.dumps(
        validated.model_dump(mode="json"),
        allow_nan=False,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


@dataclass(frozen=True, slots=True, repr=False)
class RetainedLocalReceipt:
    grant: LocalPermitGrant
    permit_ordinal: int
    reservation_json: bytes

    @classmethod
    def freeze(cls, receipt: LocalPermitReceipt) -> RetainedLocalReceipt:
        encoded = reservation_bytes(receipt.reservation)
        # Recheck nested dictionaries, which a frozen model does not freeze.
        copy = LocalPermitReceipt(
            grant=LocalPermitGrant.model_validate_json(receipt.grant.model_dump_json()),
            permit_ordinal=receipt.permit_ordinal,
            reservation=AccountingReservation.model_validate_json(encoded),
        )
        return cls(copy.grant, copy.permit_ordinal, encoded)

    @property
    def retained_bytes(self) -> int:
        # Keep the large audit/pricing graph as immutable bytes. Fixed space
        # covers the scalar grant, wrapper, UUID key, and ordered-map entry.
        return (
            8192
            + len(self.reservation_json)
            + 4 * (len(self.grant.grant_id) + len(self.grant.grantee_id))
        )

    def restore(self) -> LocalPermitReceipt:
        return LocalPermitReceipt(
            grant=self.grant,
            permit_ordinal=self.permit_ordinal,
            reservation=AccountingReservation.model_validate_json(self.reservation_json),
        )


class LocalReceiptStore:
    """Only an acknowledged terminal owner can remove an issued proof."""

    def __init__(self, *, max_entries: int, max_retained_bytes: int) -> None:
        if not 1 <= max_entries <= 100_000:
            raise ValueError("local receipt entry capacity is invalid")
        if not 1 <= max_retained_bytes <= 64 * 1024 * 1024:
            raise ValueError("local receipt byte capacity is invalid")
        self._max_entries = max_entries
        self._max_bytes = max_retained_bytes
        self._values: OrderedDict[UUID, RetainedLocalReceipt] = OrderedDict()
        self._bytes = 0

    @property
    def retained_bytes(self) -> int:
        return self._bytes

    @property
    def entries(self) -> int:
        return len(self._values)

    def capacity_for(self, *, entries: int, retained_bytes: int) -> bool:
        if entries < 0 or retained_bytes < 0:
            raise ValueError("local receipt capacity request must not be negative")
        return (
            self.entries + entries <= self._max_entries
            and self._bytes + retained_bytes <= self._max_bytes
        )

    def retain(self, receipt: LocalPermitReceipt) -> bool:
        item = receipt.reservation
        retained = RetainedLocalReceipt.freeze(receipt)
        previous = self._values.get(item.operation_id)
        if previous is not None:
            if previous != retained:
                raise ValueError("local issue identity cannot change")
            return True
        if not self.capacity_for(entries=1, retained_bytes=retained.retained_bytes):
            return False
        self._values[item.operation_id] = retained
        self._bytes += retained.retained_bytes
        return True

    def get(self, operation_id: UUID) -> LocalPermitReceipt | None:
        retained = self._values.get(operation_id)
        return None if retained is None else retained.restore()

    def matches(self, item: AccountingReservation) -> bool:
        retained = self._values.get(item.operation_id)
        if retained is None:
            return False
        if retained.reservation_json != reservation_bytes(item):
            raise ValueError("local issue identity cannot change")
        return True

    def acknowledge(self, receipt: LocalPermitReceipt, terminal: FinalizationReceipt) -> bool:
        operation_id = receipt.reservation.operation_id
        if (
            terminal.operation_id != operation_id
            or terminal.protocol_generation != receipt.reservation.protocol_generation
        ):
            raise ValueError("local terminal acknowledgement does not match its issue")
        retained = self._values.get(operation_id)
        if retained is None:
            return False
        if retained != RetainedLocalReceipt.freeze(receipt):
            raise ValueError("local receipt acknowledgement does not match its issue")
        del self._values[operation_id]
        self._bytes -= retained.retained_bytes
        return True

    def recovery_candidates(self, *, limit: int = 256) -> tuple[UUID, ...]:
        if not 1 <= limit <= 256:
            raise ValueError("local receipt recovery slice must contain 1 to 256 entries")
        result = []
        for _ in range(min(limit, self.entries)):
            key = next(iter(self._values))
            result.append(key)
            self._values.move_to_end(key)
        return tuple(result)
