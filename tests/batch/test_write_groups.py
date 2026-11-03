"""Writes that would wait for a database slot go out together."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

import pytest

from src.batch.write_groups import WriteGroups
from src.metrics.batch import deltallm_batch_write_group_size_metric


class _Store:
    """Fake statement runner: a write waits while the store is held."""

    def __init__(self, *, reject: set[str] | None = None, fail: bool = False) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.reject = reject or set()
        self.fail = fail
        self.gates: list[asyncio.Event] = []
        self.held = False

    def release(self) -> None:
        """Let every write sent so far finish, and new ones run straight through."""
        self.held = False
        for gate in self.gates:
            gate.set()

    async def write(self, requests: Sequence[str]) -> bool:
        self.calls.append(tuple(requests))
        gate = asyncio.Event()
        self.gates.append(gate)
        if not self.held:
            gate.set()
        await gate.wait()
        if self.fail:
            raise ConnectionError("database went away")
        # All or nothing, like the real statements.
        return not (set(requests) & self.reject)


def _groups(store: _Store, *, limit: int = 1, max_rows: int = 10, **kwargs) -> WriteGroups:
    return WriteGroups(
        kind="test",
        limit=limit,
        write=store.write,
        accepted=bool,
        max_rows=max_rows,
        rows=kwargs.pop("rows", lambda request: 1),
        **kwargs,
    )


async def _settle() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


async def _hold_the_only_slot(store: _Store, groups: WriteGroups) -> asyncio.Task:
    store.held = True
    first = asyncio.create_task(groups.run("first"))
    await _settle()
    assert groups.active == 1
    return first


async def test_a_database_that_keeps_up_sees_one_write_each():
    store = _Store()
    groups = _groups(store, limit=3)
    assert await asyncio.gather(*(groups.run(name) for name in "abc")) == [True] * 3
    assert sorted(store.calls) == [("a",), ("b",), ("c",)]
    assert groups.active == 0


async def test_writes_that_would_wait_go_out_together():
    store = _Store()
    groups = _groups(store)
    first = await _hold_the_only_slot(store, groups)
    waiting = [asyncio.create_task(groups.run(name)) for name in "abcd"]
    await _settle()
    assert groups.waiting == 4
    store.release()
    assert await asyncio.gather(first, *waiting) == [True] * 5
    assert store.calls == [("first",), ("a", "b", "c", "d")]
    assert groups.active == 0


async def test_a_group_never_exceeds_its_row_or_size_limit():
    store = _Store()
    groups = _groups(
        store,
        max_rows=4,
        rows=lambda request: 2,
        size=len,
        max_size=6,
    )
    first = await _hold_the_only_slot(store, groups)
    waiting = [asyncio.create_task(groups.run(name)) for name in ("aa", "bbb", "cc", "ddddd")]
    await _settle()
    store.release()
    await asyncio.gather(first, *waiting)
    # 2 rows each and at most 4 rows: two per group. "ddddd" with "cc" is too big.
    assert store.calls == [("first",), ("aa", "bbb"), ("cc",), ("ddddd",)]


async def test_conflicting_writes_are_not_grouped():
    store = _Store()
    groups = _groups(
        store,
        conflicts=lambda group, request: any(member[0] == request[0] for member in group),
    )
    first = await _hold_the_only_slot(store, groups)
    waiting = [asyncio.create_task(groups.run(name)) for name in ("x1", "y1", "x2")]
    await _settle()
    store.release()
    await asyncio.gather(first, *waiting)
    assert store.calls == [("first",), ("x1", "y1"), ("x2",)]


async def test_a_rejected_group_writes_each_member_alone_for_its_own_answer():
    store = _Store(reject={"b"})
    groups = _groups(store)
    first = await _hold_the_only_slot(store, groups)
    waiting = [asyncio.create_task(groups.run(name)) for name in "abc"]
    await _settle()
    store.release()
    await first
    # A newer write arrives while the group is being split.
    await _settle()
    late = asyncio.create_task(groups.run("late"))
    assert await asyncio.gather(*waiting, late) == [True, False, True, True]
    # The members go first, alone, ahead of the newer write.
    assert store.calls == [("first",), ("a", "b", "c"), ("a",), ("b",), ("c",), ("late",)]
    assert groups.active == 0


async def test_a_failed_group_writes_each_member_alone_for_its_own_error():
    store = _Store()
    groups = _groups(store)
    first = await _hold_the_only_slot(store, groups)
    waiting = [asyncio.create_task(groups.run(name)) for name in "ab"]
    await _settle()
    store.fail = True
    store.release()
    results = await asyncio.gather(first, *waiting, return_exceptions=True)
    assert all(isinstance(result, ConnectionError) for result in results)
    assert store.calls == [("first",), ("a", "b"), ("a",), ("b",)]
    assert groups.active == 0


async def test_one_bad_row_never_fails_the_rest_of_its_group():
    class _OneBadRow(_Store):
        async def write(self, requests):
            if "bad" in requests:
                self.calls.append(tuple(requests))
                raise ValueError("constraint violated")
            return await super().write(requests)

    store = _OneBadRow()
    groups = _groups(store)
    first = await _hold_the_only_slot(store, groups)
    waiting = [asyncio.create_task(groups.run(name)) for name in ("a", "bad", "c")]
    await _settle()
    store.release()
    results = await asyncio.gather(first, *waiting, return_exceptions=True)
    assert results[:2] == [True, True] and results[3] is True
    assert isinstance(results[2], ValueError)
    assert store.calls == [("first",), ("a", "bad", "c"), ("a",), ("bad",), ("c",)]


async def test_a_cancelled_waiting_write_is_never_sent():
    store = _Store()
    groups = _groups(store)
    first = await _hold_the_only_slot(store, groups)
    keep = asyncio.create_task(groups.run("keep"))
    drop = asyncio.create_task(groups.run("drop"))
    await _settle()
    drop.cancel()
    with pytest.raises(asyncio.CancelledError):
        await drop
    store.release()
    assert await asyncio.gather(first, keep) == [True, True]
    assert store.calls == [("first",), ("keep",)]


async def test_cancelling_one_member_does_not_cancel_its_group():
    store = _Store()
    groups = _groups(store)
    first = await _hold_the_only_slot(store, groups)
    waiting = {name: asyncio.create_task(groups.run(name)) for name in "ab"}
    await _settle()
    # Finish "first" only: the group of "a" and "b" starts and stays held.
    store.gates[0].set()
    await first
    await _settle()
    assert store.calls[-1] == ("a", "b")
    waiting["a"].cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting["a"]
    store.release()
    assert await waiting["b"] is True
    assert groups.active == 0


async def test_with_grouping_off_waiting_writes_go_one_at_a_time_in_order():
    store = _Store()
    groups = _groups(store, enabled=False)
    first = await _hold_the_only_slot(store, groups)
    waiting = [asyncio.create_task(groups.run(name)) for name in "abc"]
    await _settle()
    store.release()
    await asyncio.gather(first, *waiting)
    assert store.calls == [("first",), ("a",), ("b",), ("c",)]


async def test_a_slot_handed_to_a_cancelled_write_is_given_back():
    store = _Store()
    groups = _groups(store)
    first = await _hold_the_only_slot(store, groups)
    alone = asyncio.create_task(groups.run("alone"))
    await _settle()
    store.gates[0].set()
    # Wait until the release has handed the only slot to "alone", then cancel
    # it before it resumes.
    while not first.done():
        await asyncio.sleep(0)
    assert groups.active == 1
    alone.cancel()
    with pytest.raises(asyncio.CancelledError):
        await alone
    assert store.calls == [("first",)]
    assert groups.active == 0
    store.release()
    assert await groups.run("next") is True


async def test_group_sizes_are_measured():
    def _count(size_label: str) -> float:
        for family in deltallm_batch_write_group_size_metric.collect():
            for sample in family.samples:
                if sample.name.endswith("_count") and sample.labels == {"kind": size_label}:
                    return sample.value
        return 0.0

    store = _Store()
    groups = WriteGroups(
        kind="measured", limit=1, write=store.write, accepted=bool, max_rows=10, rows=len
    )
    before = _count("measured")
    first = await _hold_the_only_slot(store, groups)
    waiting = [asyncio.create_task(groups.run(name)) for name in "ab"]
    await _settle()
    store.release()
    await asyncio.gather(first, *waiting)
    # One statement alone and one group of two.
    assert _count("measured") == before + 2
