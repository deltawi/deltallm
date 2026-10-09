"""Financial queues keep immutable payloads and split valid large records."""

import asyncio
import ast
import json
from pathlib import Path

import pytest

from src.billing.accounting_protocol import DispatchPermit, FinalizationReceipt, ReserveDecision
from src.billing.accounting_service import AccountingProtocolService
from src.billing.durable_microbatch import DurableBatchFull
from tests.test_accounting_protocol import finalization, reservation
from tests.test_preissued_permit_bytes import retained_object_bytes


class Repository:
    def __init__(self):
        self.reservations, self.finalizations = [], []

    async def reserve_batch(self, values, *, expires_at):
        self.reservations.append(list(values))
        return [
            DispatchPermit(
                protocol_generation=item.protocol_generation,
                operation_id=item.operation_id,
                decision=ReserveDecision.DISPATCH,
                dispatch_token=item.owner_token,
                accounting_partition=0,
            )
            for item in values
        ]

    async def finalize_batch(self, values, *, expires_at):
        self.finalizations.append(list(values))
        assert (
            len(
                json.dumps(
                    [item.model_dump(mode="json") for item in values],
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode()
            )
            <= 1048576
        )
        return [
            FinalizationReceipt(
                protocol_generation=item.protocol_generation,
                operation_id=item.operation_id,
                event_sequence=index + 1,
                outcome=item.outcome,
            )
            for index, item in enumerate(values)
        ]


def test_every_production_durable_microbatch_has_an_explicit_byte_owner():
    callers = []
    for path in (Path(__file__).resolve().parents[1] / "src").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id == "DurableMicrobatcher":
                    fields = {keyword.arg for keyword in node.keywords}
                    assert {"payload_size", "max_batch_bytes", "max_retained_bytes"} <= fields, path
                    callers.append(path)
    assert callers


async def test_eight_valid_large_terminal_records_are_split_without_losing_one():
    repository = Repository()
    service = AccountingProtocolService(repository, generation=7, dwell_seconds=0.01)
    records = [finalization(reservation()) for _ in range(8)]
    for record in records:
        record.spend_payload["data"] = "s" * 200000
        record.audit_envelope["data"] = "a" * 64000
    service.start()
    try:
        receipts = await asyncio.gather(*(service.finalize(record) for record in records))
    finally:
        await service.close()
    assert [item.operation_id for item in receipts] == [item.operation_id for item in records]
    assert [len(batch) for batch in repository.finalizations] == [3, 3, 2]
    assert [item for batch in repository.finalizations for item in batch] == records
    assert service.finalizations.retained_bytes == 0


@pytest.mark.parametrize("phase", ["reservation", "finalization"])
async def test_mutation_after_enqueue_cannot_change_financial_facts(phase):
    repository = Repository()
    service = AccountingProtocolService(repository, generation=7, dwell_seconds=0)
    item = reservation() if phase == "reservation" else finalization(reservation())
    original = item.model_dump(mode="json")
    queue = service.reservations if phase == "reservation" else service.finalizations

    def mutate(depth):
        if depth:
            item.audit_envelope["action"] = "changed after enqueue"
            if phase == "reservation":
                item.pricing_snapshot["version"] = "changed after enqueue"
            else:
                item.spend_payload["request_id"] = "changed after enqueue"

    queue._set_queue_depth = mutate
    service.start()
    try:
        await (service.reserve(item) if phase == "reservation" else service.finalize(item))
    finally:
        await service.close()
    observed = repository.reservations if phase == "reservation" else repository.finalizations
    assert observed[0][0].model_dump(mode="json") == original
    assert observed[0][0] is not item


@pytest.mark.parametrize("phase", ["reservation", "finalization"])
@pytest.mark.parametrize("invalid", ["nonfinite", "oversized"])
async def test_changed_invalid_graph_is_rejected_before_enqueue_or_database(phase, invalid):
    repository = Repository()
    service = AccountingProtocolService(repository, generation=7)
    item = reservation() if phase == "reservation" else finalization(reservation())
    item.audit_envelope["data"] = float("nan") if invalid == "nonfinite" else "x" * 65536
    service.start()
    try:
        with pytest.raises(ValueError):
            await (service.reserve(item) if phase == "reservation" else service.finalize(item))
    finally:
        await service.close()
    assert repository.reservations == repository.finalizations == []
    assert service.reservations.retained_bytes == service.finalizations.retained_bytes == 0


async def test_queue_budget_includes_selected_work_and_keeps_queues_independent():
    repository = Repository()
    entered, release = asyncio.Event(), asyncio.Event()
    original = repository.finalize_batch

    async def blocked(values, *, expires_at):
        entered.set()
        await release.wait()
        return await original(values, expires_at=expires_at)

    repository.finalize_batch = blocked
    service = AccountingProtocolService(
        repository,
        generation=7,
        dwell_seconds=0,
        max_finalization_retained_bytes=6000,
        max_reservation_retained_bytes=6000,
    )
    terminal = finalization(reservation())
    service.start()
    first = asyncio.create_task(service.finalize(terminal))
    await entered.wait()
    try:
        assert service.finalizations.pending == 0
        assert 4096 < service.finalizations.retained_bytes <= 6000
        with pytest.raises(DurableBatchFull):
            await service.finalize(finalization(reservation()))
        assert (await service.reserve(reservation())).decision is ReserveDecision.DISPATCH
    finally:
        release.set()
        await first
        await service.close()
    assert service.reservations.retained_bytes == service.finalizations.retained_bytes == 0


@pytest.mark.parametrize("size", [1, 64000])
async def test_complete_queue_object_graph_is_below_its_byte_charge(size):
    repository = Repository()
    entered, release, queued = asyncio.Event(), asyncio.Event(), asyncio.Event()
    original = repository.finalize_batch

    async def blocked(values, *, expires_at):
        entered.set()
        await release.wait()
        return await original(values, expires_at=expires_at)

    repository.finalize_batch = blocked
    service = AccountingProtocolService(repository, generation=7, dwell_seconds=0)
    service.start()
    first = asyncio.create_task(service.finalize(finalization(reservation())))
    await entered.wait()
    service.finalizations._set_queue_depth = lambda depth: queued.set() if depth else None
    second_value = finalization(reservation())
    second_value.audit_envelope["data"] = "a" * size
    second = asyncio.create_task(service.finalize(second_value))
    await queued.wait()
    try:
        assert service.finalizations.retained_bytes >= retained_object_bytes(
            service.finalizations._queue
        )
        assert isinstance(next(iter(service.finalizations._queue.values())).value, bytes)
    finally:
        release.set()
        await asyncio.gather(first, second)
        await service.close()
