"""Native sent marks that would wait for a slot are written together."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from src.batch.completion_outbox import (
    BatchCompletionOutboxWorker,
    BatchCompletionOutboxWorkerConfig,
)
from src.batch.completion_sent_marks import GroupedSentMarks
from src.bootstrap.batch_runtime.pool_sizing import completion_outbox_sent_mark_concurrency


class _Store:
    def __init__(self, *, lost: set[str] | None = None, fail_many: bool = False) -> None:
        self.lost = lost or set()
        self.fail_many = fail_many
        self.single: list[tuple[str, str, int]] = []
        self.many: list[tuple[list[tuple[str, int]], str]] = []
        self.held = False
        self.gates: list[asyncio.Event] = []

    def release(self) -> None:
        self.held = False
        for gate in self.gates:
            gate.set()

    async def _wait(self) -> None:
        gate = asyncio.Event()
        self.gates.append(gate)
        if not self.held:
            gate.set()
        await gate.wait()

    async def mark_completion_outbox_sent(self, completion_id, *, worker_id, attempt_count=None):  # noqa: ANN001
        self.single.append((completion_id, worker_id, attempt_count))
        await self._wait()
        return completion_id not in self.lost

    async def mark_completion_outbox_sent_many(self, marks, *, worker_id):  # noqa: ANN001
        self.many.append((list(marks), worker_id))
        await self._wait()
        if self.fail_many:
            raise RuntimeError("database unavailable")
        return frozenset(mark for mark in marks if mark[0] not in self.lost)


async def _settle() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


def _mark(marks: GroupedSentMarks, completion_id: str, attempt: int = 1):  # noqa: ANN202
    return asyncio.create_task(
        marks.mark_completion_outbox_sent(completion_id, worker_id="w1", attempt_count=attempt)
    )


async def test_a_mark_with_a_free_slot_uses_todays_one_row_statement():
    store = _Store()
    marks = GroupedSentMarks(store, limit=1)
    assert await marks.mark_completion_outbox_sent("a", worker_id="w1", attempt_count=3) is True
    assert store.single == [("a", "w1", 3)]
    assert store.many == []


async def test_marks_waiting_for_the_slot_go_out_in_one_statement():
    store = _Store()
    marks = GroupedSentMarks(store, limit=1)
    store.held = True
    first = _mark(marks, "first")
    await _settle()
    waiting = [_mark(marks, name) for name in "abc"]
    await _settle()
    store.release()
    assert await asyncio.gather(first, *waiting) == [True] * 4
    assert store.single == [("first", "w1", 1)]
    assert store.many == [([("a", 1), ("b", 1), ("c", 1)], "w1")]


async def test_each_grouped_mark_gets_its_own_answer_without_a_split():
    store = _Store(lost={"b"})
    marks = GroupedSentMarks(store, limit=1)
    store.held = True
    first = _mark(marks, "first")
    await _settle()
    waiting = [_mark(marks, name) for name in "abc"]
    await _settle()
    store.release()
    assert await asyncio.gather(first, *waiting) == [True, True, False, True]
    # A lost row is one row's answer, not a rejected group: nothing is rewritten.
    assert len(store.many) == 1
    assert store.single == [("first", "w1", 1)]


async def test_a_group_that_errors_is_written_again_one_mark_at_a_time():
    store = _Store(fail_many=True)
    marks = GroupedSentMarks(store, limit=1)
    store.held = True
    first = _mark(marks, "first")
    await _settle()
    waiting = [_mark(marks, name) for name in "ab"]
    await _settle()
    store.release()
    assert await asyncio.gather(first, *waiting) == [True] * 3
    assert [ids for ids, _ in store.many] == [[("a", 1), ("b", 1)]]
    assert [call[0] for call in store.single] == ["first", "a", "b"]


async def test_two_marks_for_one_row_never_share_a_statement():
    store = _Store()
    marks = GroupedSentMarks(store, limit=1)
    store.held = True
    first = _mark(marks, "first")
    await _settle()
    # A stale attempt and the current one for the same row, plus another row.
    waiting = [_mark(marks, "a", 1), _mark(marks, "a", 2), _mark(marks, "b", 1)]
    await _settle()
    store.release()
    await asyncio.gather(first, *waiting)
    for group, _ in store.many:
        ids = [completion_id for completion_id, _attempt in group]
        assert len(ids) == len(set(ids))


@pytest.mark.parametrize(
    ("grouping", "worker_concurrency", "expected"),
    [(True, 8, 2), (True, 4, 1), (True, 1, 1), (True, 32, 2), (False, 8, 0)],
)
def test_sent_mark_slots_stay_below_the_outbox_concurrency(grouping, worker_concurrency, expected):
    general = SimpleNamespace(
        embeddings_batch_write_grouping_enabled=grouping,
        embeddings_batch_worker_concurrency=worker_concurrency,
    )
    assert completion_outbox_sent_mark_concurrency(general) == expected


def test_grouping_off_hands_native_delivery_the_repository_itself():
    repository = _Store()
    app = SimpleNamespace(state=SimpleNamespace(spend_tracking_service=None))
    off = BatchCompletionOutboxWorker(
        app=app,
        repository=repository,  # type: ignore[arg-type]
        config=BatchCompletionOutboxWorkerConfig(),
    )
    assert off._native_delivery.repository is repository
    on = BatchCompletionOutboxWorker(
        app=app,
        repository=repository,  # type: ignore[arg-type]
        config=BatchCompletionOutboxWorkerConfig(sent_mark_concurrency=2),
    )
    assert isinstance(on._native_delivery.repository, GroupedSentMarks)
