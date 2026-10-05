"""Byte-bounded immutable local issue proofs, with no financial eviction policy."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from src.billing.accounting_local_leases import LocalPermitGrant, LocalPermitReceipt
from src.billing.accounting_protocol import AccountingReservation
from src.billing.accounting_terminal_receipts import TerminalReceipt
from src.billing.accounting_snapshots import reservation_bytes


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

    def same_issue(self, other: RetainedLocalReceipt) -> bool:
        excluded = {"observed_at", "observed_monotonic"}
        return (
            self.permit_ordinal == other.permit_ordinal
            and self.reservation_json == other.reservation_json
            and self.grant.model_dump(exclude=excluded) == other.grant.model_dump(exclude=excluded)
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
            if not previous.same_issue(retained):
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

    def prepare_issue(self, values: Sequence[RetainedLocalReceipt]) -> bool:
        if len(values) > 256:
            raise ValueError("local issue must contain at most 256 entries")
        operation_ids = tuple(value.restore().reservation.operation_id for value in values)
        if len(set(operation_ids)) != len(operation_ids):
            raise ValueError("one local issue cannot repeat an operation")
        if any(operation_id in self._values for operation_id in operation_ids):
            raise ValueError("local operation is already issued")
        return self.capacity_for(
            entries=len(values), retained_bytes=sum(value.retained_bytes for value in values)
        )

    def _commit_issue(self, values: Sequence[tuple[UUID, RetainedLocalReceipt]]) -> None:
        # No validation or model conversion may occur after the cursor commit.
        for operation_id, retained in values:
            self._values[operation_id] = retained
            self._bytes += retained.retained_bytes

    def matches(self, item: AccountingReservation) -> bool:
        retained = self._values.get(item.operation_id)
        if retained is None:
            return False
        if retained.reservation_json != reservation_bytes(item):
            raise ValueError("local issue identity cannot change")
        return True

    def acknowledge(self, receipt: LocalPermitReceipt, terminal: TerminalReceipt) -> bool:
        _, retained = self._prepare_acknowledgement(receipt, terminal)
        if retained is None:
            return False
        self.acknowledge_batch(((receipt, terminal),))
        return True

    def acknowledge_batch(
        self, values: Sequence[tuple[LocalPermitReceipt, TerminalReceipt]]
    ) -> None:
        self._commit_acknowledgements(self.prepare_acknowledgements(values))

    def prepare_acknowledgements(
        self, values: Sequence[tuple[LocalPermitReceipt, TerminalReceipt]]
    ) -> tuple[tuple[UUID, RetainedLocalReceipt | None], ...]:
        if len(values) > 256:
            raise ValueError("local terminal acknowledgement exceeds its entry limit")
        prepared = tuple(self._prepare_acknowledgement(receipt, ack) for receipt, ack in values)
        if len({key for key, _ in prepared}) != len(prepared):
            raise ValueError("local terminal acknowledgement repeats an operation")
        return prepared

    def _commit_acknowledgements(
        self, prepared: Sequence[tuple[UUID, RetainedLocalReceipt | None]]
    ) -> None:
        # Validate every proof before the first removal; no await or conversion
        # can enter the removal loop. A replay can have no local charge to remove.
        for key, retained in prepared:
            if retained is not None:
                del self._values[key]
                self._bytes -= retained.retained_bytes

    def _prepare_acknowledgement(
        self, receipt: LocalPermitReceipt, terminal: TerminalReceipt
    ) -> tuple[UUID, RetainedLocalReceipt | None]:
        operation_id = receipt.reservation.operation_id
        if (
            terminal.operation_id != operation_id
            or terminal.protocol_generation != receipt.reservation.protocol_generation
        ):
            raise ValueError("local terminal acknowledgement does not match its issue")
        retained = self._values.get(operation_id)
        if retained is not None and not retained.same_issue(RetainedLocalReceipt.freeze(receipt)):
            raise ValueError("local receipt acknowledgement does not match its issue")
        return operation_id, retained

    def recovery_candidates(self, *, limit: int = 256) -> tuple[UUID, ...]:
        if not 1 <= limit <= 256:
            raise ValueError("local receipt recovery slice must contain 1 to 256 entries")
        result = []
        for _ in range(min(limit, self.entries)):
            key = next(iter(self._values))
            result.append(key)
            self._values.move_to_end(key)
        return tuple(result)
