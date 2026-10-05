"""Finite active and return-only local grant cursors; no dependency owner."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, replace
import math

from src.billing.accounting_local_leases import LocalPermitGrant, LocalPermitReturn
from src.billing.preissued_permits import PermitSubject


@dataclass(frozen=True, slots=True, repr=False)
class LocalGrantCursor:
    subject: PermitSubject
    grant: LocalPermitGrant
    next_ordinal: int = 0

    @property
    def remaining(self) -> int:
        return self.grant.operation_limit - self.next_ordinal

    @property
    def retained_bytes(self) -> int:
        # Extra space covers local clock fields and both bounded map entries.
        return self.subject.retained_bytes + 2048


class LocalCursorStore:
    def __init__(self, *, generation: int, max_entries: int, max_retained_bytes: int) -> None:
        if not 1 <= generation <= 2**63 - 1:
            raise ValueError("local cursor generation is invalid")
        if not 1 <= max_entries <= 100_000:
            raise ValueError("local cursor entry capacity is invalid")
        if not 1 <= max_retained_bytes <= 64 * 1024 * 1024:
            raise ValueError("local cursor byte capacity is invalid")
        self._generation = generation
        self._max_entries = max_entries
        self._max_bytes = max_retained_bytes
        self._active: OrderedDict[PermitSubject, LocalGrantCursor] = OrderedDict()
        self._retiring: OrderedDict[str, LocalGrantCursor] = OrderedDict()
        self._grant_ids: set[str] = set()
        self._bytes = 0
        self._available = 0

    @property
    def active_subjects(self) -> int:
        return len(self._active)

    @property
    def retiring_grants(self) -> int:
        return len(self._retiring)

    @property
    def available_permits(self) -> int:
        return self._available

    @property
    def entries(self) -> int:
        return self.active_subjects + self.retiring_grants

    @property
    def retained_bytes(self) -> int:
        return self._bytes

    def capacity_for(self, *, entries: int, retained_bytes: int) -> bool:
        if entries < 0 or retained_bytes < 0:
            raise ValueError("local cursor capacity request must not be negative")
        return (
            self.entries + entries <= self._max_entries
            and self._bytes + retained_bytes <= self._max_bytes
        )

    def get(self, subject: PermitSubject) -> LocalGrantCursor | None:
        return self._active.get(subject)

    def add(self, subject: PermitSubject, grant: LocalPermitGrant) -> bool:
        if (
            subject.generation != self._generation
            or grant.protocol_generation != self._generation
            or subject.allowance != grant.allowance
        ):
            raise ValueError("local cursor funding identity does not match")
        if subject in self._active or grant.grant_id in self._grant_ids:
            raise ValueError("local cursor funding cannot replace retained capacity")
        cursor = LocalGrantCursor(
            subject, LocalPermitGrant.model_validate_json(grant.model_dump_json())
        )
        if not self.capacity_for(entries=1, retained_bytes=cursor.retained_bytes):
            return False
        self._active[subject] = cursor
        self._grant_ids.add(grant.grant_id)
        self._bytes += cursor.retained_bytes
        self._available += cursor.remaining
        return True

    def advance(self, subject: PermitSubject) -> None:
        cursor = self._active[subject]
        if cursor.remaining <= 0:
            raise ValueError("local cursor has no unused ordinal")
        cursor = replace(cursor, next_ordinal=cursor.next_ordinal + 1)
        self._available -= 1
        if cursor.remaining == 0:
            del self._active[subject]
            self._grant_ids.remove(cursor.grant.grant_id)
            self._bytes -= cursor.retained_bytes
        else:
            self._active[subject] = cursor

    def retire(self, subject: PermitSubject) -> bool:
        cursor = self._active.pop(subject, None)
        if cursor is None:
            return False
        self._available -= cursor.remaining
        self._retiring[cursor.grant.grant_id] = cursor
        return True

    def prune(self, *, now: float, minimum_validity_seconds: float, limit: int = 256) -> None:
        _slice_limit(limit)
        if not math.isfinite(now) or now < 0 or not 0 <= minimum_validity_seconds <= 5:
            raise ValueError("local cursor scan clock or validity is invalid")
        for _ in range(min(limit, self.active_subjects)):
            subject = next(iter(self._active))
            if self._active[subject].grant.dispatch_deadline <= now + minimum_validity_seconds:
                self.retire(subject)
            else:
                self._active.move_to_end(subject)

    def retire_slice(self, *, limit: int = 256) -> None:
        _slice_limit(limit)
        for _ in range(min(limit, self.active_subjects)):
            self.retire(next(iter(self._active)))

    def return_candidates(self, *, limit: int = 256) -> tuple[LocalPermitReturn, ...]:
        _slice_limit(limit)
        result = []
        for _ in range(min(limit, self.retiring_grants)):
            key = next(iter(self._retiring))
            cursor = self._retiring[key]
            result.append(
                LocalPermitReturn(grant=cursor.grant, first_unused_ordinal=cursor.next_ordinal)
            )
            self._retiring.move_to_end(key)
        return tuple(result)

    def acknowledge_return(self, returned: LocalPermitReturn, count: int) -> bool:
        cursor = self._retiring.get(returned.grant.grant_id)
        if cursor is None:
            return False
        if (
            cursor.grant != returned.grant
            or cursor.next_ordinal != returned.first_unused_ordinal
            or type(count) is not int
            or count != cursor.remaining
        ):
            raise ValueError("local suffix acknowledgement does not match")
        del self._retiring[returned.grant.grant_id]
        self._grant_ids.remove(returned.grant.grant_id)
        self._bytes -= cursor.retained_bytes
        return True


def _slice_limit(limit: int) -> None:
    if not 1 <= limit <= 256:
        raise ValueError("local cursor slice must contain 1 to 256 entries")
