from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from src.auth.external_errors import ExternalAuthError, ExternalAuthUnavailable
from src.db.identity.external.external_auth_transactions import ExternalAuthTransactions
from src.metrics.external_auth import external_auth_denials
from src.services.external_auth_runtime import ExternalAuthRuntime


class Database:
    def __init__(self):
        self.active = 0
        self.maximum = 0
        self.deadlines = []
        self.statements = []

    @asynccontextmanager
    async def tx(self, **deadlines):
        self.deadlines.append(deadlines)
        self.active += 1
        self.maximum = max(self.active, self.maximum)
        try:
            yield self
        finally:
            self.active -= 1

    async def query_raw(self, statement):
        self.statements.append(statement)


async def test_mutation_overload_does_not_consume_validation_or_maintenance_slots():
    db = Database()
    owner = ExternalAuthTransactions(db)
    release = asyncio.Event()

    async def hold(kind="mutation"):
        async with owner.transaction(kind):
            await release.wait()

    active = [asyncio.create_task(hold()) for _ in range(2)]
    await asyncio.sleep(0)
    waiting = [asyncio.create_task(hold()) for _ in range(8)]
    await asyncio.sleep(0)
    assert owner.gates["mutation"].active == 2 and owner.gates["mutation"].waiters == 8
    with pytest.raises(ExternalAuthUnavailable):
        async with owner.transaction():
            pytest.fail("Overflow entered the pool")
    async with owner.transaction("validation"):
        async with owner.transaction("maintenance"):
            assert db.active == 4
    waiting[0].cancel()
    await asyncio.gather(waiting[0], return_exceptions=True)
    await asyncio.sleep(0.06)
    errors = await asyncio.gather(*waiting[1:], return_exceptions=True)
    assert all(isinstance(error, ExternalAuthUnavailable) for error in errors)
    release.set()
    await asyncio.gather(*active)
    assert db.maximum == 4 and db.active == 0
    assert all(gate.active == 0 and gate.waiters == 0 for gate in owner.gates.values())
    assert all(
        item["timeout"].total_seconds() == 0.75 and item["max_wait"].total_seconds() == 0.05
        for item in db.deadlines
    )
    assert all("250ms" in query and "100ms" in query and "UTC" in query for query in db.statements)


async def test_runtime_deadline_and_denial_labels_never_use_caller_values():
    runtime = ExternalAuthRuntime.__new__(ExternalAuthRuntime)
    runtime.closed = False
    runtime.cache_worker_ready = lambda: True
    runtime.crypto = SimpleNamespace(ready=True)
    runtime.cleanup_healthy = True
    runtime.cleanup_task = asyncio.create_task(asyncio.Event().wait())
    runtime.audit = SimpleNamespace(
        service=SimpleNamespace(worker_health=SimpleNamespace(ready=True))
    )
    before = external_auth_denials.labels("invalid_request")._value.get()
    try:
        with pytest.raises(ExternalAuthError):
            async with runtime.operation("exchange"):
                raise ExternalAuthError("private-caller-value")
        assert external_auth_denials.labels("invalid_request")._value.get() == before + 1
        with pytest.raises(ExternalAuthUnavailable):
            async with runtime.operation("exchange"):
                await asyncio.Event().wait()
        assert not runtime.cleanup_task.done()
    finally:
        runtime.cleanup_task.cancel()
        await asyncio.gather(runtime.cleanup_task, return_exceptions=True)
