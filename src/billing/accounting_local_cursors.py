"""Finite active, staged, and return-only local cursors; no dependency owner."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass, replace
import math

from src.billing.accounting_local_leases import (
    LocalPermitGrant,
    LocalPermitReceipt,
    LocalPermitReturn,
)
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


@dataclass(frozen=True, slots=True, repr=False)
class LocalCursorIssue:
    before: LocalGrantCursor
    after: LocalGrantCursor | None
    staged: bool


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
        self._staged: OrderedDict[str, LocalGrantCursor] = OrderedDict()
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
    def staged_grants(self) -> int:
        return len(self._staged)

    @property
    def available_permits(self) -> int:
        return self._available

    @property
    def entries(self) -> int:
        return self.active_subjects + self.staged_grants + self.retiring_grants

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
        if subject in self._active:
            raise ValueError("local cursor funding cannot replace retained capacity")
        cursor = self._prepare_cursor(subject, grant)
        if not self.capacity_for(entries=1, retained_bytes=cursor.retained_bytes):
            return False
        self._active[subject] = cursor
        self._retain_cursor(cursor)
        self._available += cursor.remaining
        return True

    def stage(self, subject: PermitSubject, grant: LocalPermitGrant) -> bool:
        cursor = self._prepare_cursor(subject, grant)
        if not self.capacity_for(entries=1, retained_bytes=cursor.retained_bytes):
            return False
        self._staged[grant.grant_id] = cursor
        self._retain_cursor(cursor)
        return True

    def staged(self, grant_id: str) -> LocalGrantCursor | None:
        return self._staged.get(grant_id)

    def activate(self, grant_id: str) -> None:
        cursor = self._staged[grant_id]
        if cursor.subject in self._active:
            raise ValueError("staged funding cannot replace an active cursor")
        del self._staged[grant_id]
        self._active[cursor.subject] = cursor
        self._available += cursor.remaining

    def abort_staging(self, *, limit: int = 256) -> None:
        _slice_limit(limit)
        for _ in range(min(limit, self.staged_grants)):
            key, cursor = self._staged.popitem(last=False)
            self._retiring[key] = cursor

    def _prepare_cursor(self, subject: PermitSubject, grant: LocalPermitGrant) -> LocalGrantCursor:
        if (
            subject.generation != self._generation
            or grant.protocol_generation != self._generation
            or subject.allowance != grant.allowance
        ):
            raise ValueError("local cursor funding identity does not match")
        if grant.grant_id in self._grant_ids:
            raise ValueError("local cursor funding cannot replace retained capacity")
        # Validate raw fields before JSON conversion; non-finite values must fail.
        copy = LocalPermitGrant.model_validate(grant.model_dump())
        return LocalGrantCursor(subject, copy)

    def _retain_cursor(self, cursor: LocalGrantCursor) -> None:
        self._grant_ids.add(cursor.grant.grant_id)
        self._bytes += cursor.retained_bytes

    def prepare_issue(self, receipts: Sequence[LocalPermitReceipt]) -> tuple[LocalCursorIssue, ...]:
        if len(receipts) > 256:
            raise ValueError("local issue must contain at most 256 entries")
        planned: dict[str, LocalCursorIssue] = {}
        subjects: dict[PermitSubject, LocalGrantCursor | None] = {}
        for receipt in receipts:
            subject = PermitSubject.from_reservation(receipt.reservation)
            current = subjects.setdefault(subject, self._active.get(subject))
            grant_id = receipt.grant.grant_id
            previous = planned.get(grant_id)
            cursor = (
                previous.after
                if previous is not None
                else current
                if current is not None and current.grant.grant_id == grant_id
                else self._staged.get(grant_id)
            )
            if (
                cursor is None
                or cursor.subject != subject
                or cursor.grant != receipt.grant
                or cursor.next_ordinal != receipt.permit_ordinal
                or (current is not None and current.grant.grant_id != grant_id)
            ):
                raise ValueError("local issue does not match its retained cursor prefix")
            after = (
                None
                if cursor.remaining == 1
                else replace(cursor, next_ordinal=cursor.next_ordinal + 1)
            )
            planned[grant_id] = LocalCursorIssue(
                before=cursor if previous is None else previous.before,
                after=after,
                staged=grant_id in self._staged,
            )
            subjects[subject] = after
        return tuple(planned.values())

    def _commit_issue(self, plans: Sequence[LocalCursorIssue]) -> None:
        # The local issue owner prepares both stores and calls this without an await.
        for plan in plans:
            before, after = plan.before, plan.after
            if plan.staged:
                del self._staged[before.grant.grant_id]
                self._available += 0 if after is None else after.remaining
            else:
                del self._active[before.subject]
                self._available -= before.remaining - (0 if after is None else after.remaining)
            if after is None:
                self._grant_ids.remove(before.grant.grant_id)
                self._bytes -= before.retained_bytes
            else:
                self._active[before.subject] = after

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
