"""Mark native outbox rows sent together while the mark slots are busy.

A mark that finds a free slot is written alone, with exactly the statement and
transaction it always used. Marks that arrive while every slot is busy wait,
and go out together as one statement when a slot frees. Every row keeps its own
fence (processing, owner, attempt and lease) and gets its own answer, so one
row's lost lease never changes another row's result.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from src.batch.write_groups import WriteGroups

SENT_MARK_GROUP_MAX_ROWS = 50


class SentMarkStore(Protocol):
    async def mark_completion_outbox_sent(
        self,
        completion_id: str,
        *,
        worker_id: str,
        attempt_count: int | None = None,
    ) -> bool: ...

    async def mark_completion_outbox_sent_many(
        self,
        marks: list[tuple[str, int]],
        *,
        worker_id: str,
    ) -> frozenset[tuple[str, int]]: ...


@dataclass(frozen=True, slots=True)
class _SentMark:
    completion_id: str
    attempt_count: int
    worker_id: str


def _conflicts(group: Sequence[_SentMark], mark: _SentMark) -> bool:
    # Never two marks for one row, and one owner per statement.
    return any(
        member.completion_id == mark.completion_id or member.worker_id != mark.worker_id
        for member in group
    )


class GroupedSentMarks:
    """A drop-in sent-mark store for native delivery, capped at ``limit`` statements."""

    def __init__(self, store: SentMarkStore, *, limit: int) -> None:
        self._store = store
        self._marks: WriteGroups[_SentMark, frozenset[tuple[str, int]]] = WriteGroups(
            kind="outbox_sent",
            limit=limit,
            write=self._write,
            # Each row answers for itself, so a group is never rejected as a
            # whole; only an error splits it.
            accepted=lambda _marked: True,
            max_rows=SENT_MARK_GROUP_MAX_ROWS,
            rows=lambda _mark: 1,
            conflicts=_conflicts,
        )

    async def mark_completion_outbox_sent(
        self,
        completion_id: str,
        *,
        worker_id: str,
        attempt_count: int,
    ) -> bool:
        mark = _SentMark(completion_id, attempt_count, worker_id)
        marked = await self._marks.run(mark)
        return (completion_id, attempt_count) in marked

    async def _write(self, marks: Sequence[_SentMark]) -> frozenset[tuple[str, int]]:
        if len(marks) == 1:
            (mark,) = marks
            sent = await self._store.mark_completion_outbox_sent(
                mark.completion_id,
                worker_id=mark.worker_id,
                attempt_count=mark.attempt_count,
            )
            return frozenset({(mark.completion_id, mark.attempt_count)}) if sent else frozenset()
        return await self._store.mark_completion_outbox_sent_many(
            [(mark.completion_id, mark.attempt_count) for mark in marks],
            worker_id=marks[0].worker_id,
        )
