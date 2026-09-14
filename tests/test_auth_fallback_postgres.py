from __future__ import annotations

import asyncio
from datetime import timedelta
import os
from uuid import uuid4

import pytest
from prisma import Prisma

from src.db.allocated_client import AllocatedPrisma, DatabaseOwner
from src.db.allocation_config import DatabasePolicy
from src.db.repositories import KeyRecord
from src.models.errors import AuthenticationUnavailableError
from src.services.auth_fallback import AuthFallbackLimits
from src.services.key_service import KeyService
from tests import test_database_allocations_postgres as allocation_fixtures

allocated_databases = allocation_fixtures.allocated_databases

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.asyncio,
    pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="test PostgreSQL URL required"),
]


async def test_native_lock_wait_keeps_fallback_slot_after_caller_timeout() -> None:
    db = Prisma(datasource={"url": os.environ["DATABASE_URL"]})
    lock = "auth-fallback-test-" + uuid4().hex
    service = None
    try:
        await db.connect(timeout=timedelta(seconds=5))

        class Repository:
            calls = 0

            async def get_by_token(self, token_hash):
                self.calls += 1
                await db.query_raw("SELECT pg_advisory_xact_lock(hashtext($1))::text", lock)
                return KeyRecord(token=token_hash)

        repo = Repository()
        service = KeyService(
            repo,
            fallback_limits=AuthFallbackLimits(max_active=1, max_waiters=0, timeout_seconds=0.03),
        )
        async with db.tx(timeout=timedelta(seconds=5)) as blocker:
            await blocker.query_raw("SELECT pg_advisory_xact_lock(hashtext($1))::text", lock)
            pid = (await blocker.query_raw("SELECT pg_backend_pid() AS pid"))[0]["pid"]
            with pytest.raises(AuthenticationUnavailableError):
                await service.validate_key("sk-first")
            async with asyncio.timeout(2):
                while True:
                    rows = await db.query_raw(
                        "SELECT EXISTS (SELECT 1 FROM pg_stat_activity WHERE $1::int = ANY(pg_blocking_pids(pid))) AS blocked",
                        pid,
                    )
                    if rows[0]["blocked"]:
                        break
                    await asyncio.sleep(0.005)
            assert service.fallback.gate.active == service.fallback.size == 1
            assert service.fallback.callers == 0
            with pytest.raises(AuthenticationUnavailableError):
                await service.validate_key("sk-second")
            assert repo.calls == 1
        async with asyncio.timeout(2):
            while service.fallback.size:
                await asyncio.sleep(0.005)
        assert service.fallback.gate.active == 0
    finally:
        if service is not None:
            await service.close()
        await db.disconnect(timeout=timedelta(seconds=5))


@pytest.mark.parametrize("termination", ["timeout", "cancel", "shutdown"])
async def test_allocated_sql_remains_bounded_after_auth_caller_leaves(
    allocated_databases, termination
) -> None:
    observer = allocated_databases["control"]
    policy = DatabasePolicy("foreground", 1, 0.2, 4, 2, 5)
    owner = DatabaseOwner(policy)
    db = AllocatedPrisma(
        datasource={"url": policy.connection_url(os.environ["DATABASE_URL"])}, allocation=owner
    )
    lock = uuid4().int % (2**63 - 1)

    class Repository:
        calls = 0

        async def get_by_token(self, token_hash):
            self.calls += 1
            await db.query_raw("SELECT 1 FROM pg_advisory_xact_lock($1)", lock)
            return KeyRecord(token=token_hash)

    repo = Repository()
    service = KeyService(
        repo,
        fallback_limits=AuthFallbackLimits(
            max_active=1,
            max_waiters=0,
            timeout_seconds=0.05 if termination == "timeout" else 1.5,
        ),
    )
    caller = None
    try:
        await db.connect()
        async with observer.tx() as holder:
            await holder.query_raw("SELECT 1 FROM pg_advisory_xact_lock($1)", lock)
            pid = (await holder.query_raw("SELECT pg_backend_pid() AS pid"))[0]["pid"]
            caller = asyncio.create_task(service.validate_key("sk-first"))
            async with asyncio.timeout(1):
                while not (
                    await holder.query_raw(
                        "SELECT EXISTS (SELECT 1 FROM pg_stat_activity WHERE $1::int = ANY(pg_blocking_pids(pid))) AS blocked",
                        pid,
                    )
                )[0]["blocked"]:
                    await asyncio.sleep(0.005)
            if termination == "shutdown":
                await service.close()
            elif termination == "cancel":
                caller.cancel()
            with pytest.raises(
                AuthenticationUnavailableError
                if termination == "timeout"
                else asyncio.CancelledError
            ):
                await caller
            assert owner.gate.active == len(owner.tasks) == 1
            assert service.fallback.callers == 0
            assert service.fallback.gate.active == (termination != "shutdown")
            results = await asyncio.gather(
                *(service.validate_key(f"sk-flood-{i}") for i in range(50)), return_exceptions=True
            )
            assert all(isinstance(result, AuthenticationUnavailableError) for result in results)
            assert repo.calls == 1
            assert owner.gate.active == 1
            assert owner.gate.waiters == service.fallback.gate.waiters == 0
        await asyncio.gather(*tuple(owner.tasks), return_exceptions=True)
        async with asyncio.timeout(1):
            while service.fallback.size:
                await asyncio.sleep(0)
        assert owner.gate.active == service.fallback.gate.active == service.fallback.size == 0
        assert await db.query_raw("SELECT 1 AS alive") == [{"alive": 1}]
    finally:
        if caller is not None:
            caller.cancel()
            await asyncio.gather(caller, return_exceptions=True)
        await service.close()
        await owner.close()
        await db.disconnect()
