"""Result saves that would wait for a write slot are saved together."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from src.batch.worker_persistence import WorkerPersistenceMixin
from src.batch.worker_types import BatchWorkerConfig


class _Repository:
    def __init__(self, *, not_owned: set[str] | None = None) -> None:
        self.saves: list[tuple[list[str], dict]] = []
        self.not_owned = not_owned or set()
        self.held = False
        self.gates: list[asyncio.Event] = []
        self.renewed: list[str] = []
        self.active = 0
        self.peak = 0

    def release(self) -> None:
        self.held = False
        for gate in self.gates:
            gate.set()

    async def complete_items_with_outbox_bulk(self, *, items, worker_id, **kwargs):
        assert worker_id == "w1"
        item_ids = [row["item_id"] for row in items]
        self.saves.append((item_ids, kwargs))
        self.active += 1
        self.peak = max(self.peak, self.active)
        gate = asyncio.Event()
        self.gates.append(gate)
        if not self.held:
            gate.set()
        try:
            await gate.wait()
        finally:
            self.active -= 1
        # All or nothing, like the real statement.
        return "not_owned" if self.not_owned & set(item_ids) else "completed"

    async def release_items_for_retry(self, **kwargs):
        raise AssertionError(f"unexpected requeue: {kwargs}")

    async def list_items_by_ids(self, item_ids):
        # Another worker claimed the not owned items.
        return [
            SimpleNamespace(
                item_id=item_id,
                status="in_progress",
                locked_by="w2" if item_id in self.not_owned else "w1",
                claim_epoch=1,
            )
            for item_id in item_ids
        ]

    async def renew_item_lease(self, *, item_id, **kwargs):
        del kwargs
        self.renewed.append(item_id)
        return True


class _Engine(WorkerPersistenceMixin):
    def __init__(self, repository: _Repository, *, grouping: bool, concurrency: int = 1) -> None:
        self.repository = repository
        self.config = BatchWorkerConfig(
            worker_id="w1",
            result_save_concurrency=concurrency,
            write_grouping_enabled=grouping,
        )


def _row(item_id: str) -> dict:
    return {"item_id": item_id, "claim_epoch": 1}


async def _save(engine: _Engine, item_id: str) -> bool:
    return await engine._persist_completion_rows_with_outbox(
        items=[_row(item_id)], item_ids=[item_id], context_label="test"
    )


async def _settle() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


async def test_saves_waiting_for_a_slot_are_saved_in_one_statement():
    repository = _Repository()
    engine = _Engine(repository, grouping=True)
    repository.held = True
    first = asyncio.create_task(_save(engine, "first"))
    await _settle()
    waiting = [asyncio.create_task(_save(engine, name)) for name in "abc"]
    await _settle()
    repository.release()
    assert await asyncio.gather(first, *waiting) == [True] * 4
    assert repository.saves == [
        (["first"], {}),
        (["a", "b", "c"], {"count_rejections": False}),
    ]
    assert repository.peak == 1


async def test_a_lost_item_splits_its_group_and_only_it_is_lost():
    repository = _Repository(not_owned={"b"})
    engine = _Engine(repository, grouping=True)
    repository.held = True
    first = asyncio.create_task(_save(engine, "first"))
    await _settle()
    waiting = [asyncio.create_task(_save(engine, name)) for name in "abc"]
    await _settle()
    repository.release()
    assert await asyncio.gather(first, *waiting) == [True, True, False, True]
    assert repository.saves == [
        (["first"], {}),
        (["a", "b", "c"], {"count_rejections": False}),
        (["a"], {}),
        (["b"], {}),
        (["c"], {}),
    ]


async def test_grouping_off_keeps_result_saves_unlimited_and_alone():
    repository = _Repository()
    engine = _Engine(repository, grouping=False)
    repository.held = True
    saves = [asyncio.create_task(_save(engine, name)) for name in "abcd"]
    await _settle()
    assert repository.active == 4
    repository.release()
    assert await asyncio.gather(*saves) == [True] * 4
    assert sorted(ids for ids, _ in repository.saves) == [["a"], ["b"], ["c"], ["d"]]


async def test_grouping_skips_the_separate_lease_check_before_a_save():
    prepared = SimpleNamespace(item=SimpleNamespace(item_id="item", claim_epoch=1))
    lost = asyncio.Event()

    repository = _Repository()
    grouped = _Engine(repository, grouping=True)
    assert await grouped._still_owns_item_for_save(prepared, lease_lost=lost) is True
    assert repository.renewed == []

    single = _Engine(repository, grouping=False)
    assert await single._still_owns_item_for_save(prepared, lease_lost=lost) is True
    assert repository.renewed == ["item"]

    lost.set()
    assert await grouped._still_owns_item_for_save(prepared, lease_lost=lost) is False
    assert await single._still_owns_item_for_save(prepared, lease_lost=lost) is False
    assert repository.renewed == ["item"]
