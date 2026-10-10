"""A changed checkpoint must not turn a successful empty claim into an outage."""

import asyncio
from decimal import Decimal
import os
from uuid import uuid4

import asyncpg
import pytest

from src.db.accounting.reporting.accounting_read_model_queries import CLAIM
from tests.test_accounting_protocol_postgres import _window, accounting_db as _accounting_db
from tests.test_accounting_read_model_postgres import effects, next_page, repository, source

pytestmark = pytest.mark.postgres
accounting_db = _accounting_db


class PausedClaimClient:
    def __init__(self, db, key):
        self.db = db
        self.key = key

    async def query_raw(self, query, *parameters):
        assert query == CLAIM
        rows = await self.db.query_raw(
            "SELECT prosrc AS source FROM pg_proc WHERE "
            "oid='deltallm_accounting_claim_read_model"
            "(text,bigint,text,uuid,integer,integer)'::regprocedure"
        )
        source = rows[0]["source"]
        assert isinstance(source, str) and len(source) <= 16000
        prefix = "\n#variable_conflict use_column\nBEGIN\n RETURN QUERY\n"
        suffix = ";\nEND;\n"
        assert source.startswith(prefix) and source.endswith(suffix)
        query = source[len(prefix) : -len(suffix)]
        # Pause after the candidate snapshot, before its checkpoint row lock.
        marker = "), candidate AS MATERIALIZED ("
        assert query.count(marker) == 1
        query = (
            query.replace(
                marker,
                "), barrier AS MATERIALIZED ("
                "SELECT pg_advisory_xact_lock($7::bigint)::text AS ready FROM candidates" + marker,
            )
            .replace(
                "FROM candidates\n CROSS JOIN LATERAL unnest",
                "FROM candidates CROSS JOIN barrier\n CROSS JOIN LATERAL unnest",
            )
            .replace(
                "unnest(candidates.parts)",
                "unnest(CASE WHEN barrier.ready IS NOT NULL THEN candidates.parts END)",
            )
        )
        return await self.db.query_raw(query, *parameters, self.key)


async def wait_for_claim_snapshot(connection):
    blocker = connection.get_server_pid()
    async with asyncio.timeout(0.5):
        while not await connection.fetchval(
            "SELECT EXISTS(SELECT 1 FROM pg_stat_activity "
            "WHERE $1::integer=ANY(pg_blocking_pids(pid)))",
            blocker,
        ):
            await asyncio.sleep(0)


@pytest.mark.parametrize("first_limit", [4, 2])
async def test_claim_rechecks_work_after_another_worker_advances_checkpoint(
    accounting_db, first_limit
):
    clients, generation = accounting_db
    db = clients[0]
    window, _ = await source(db, generation)
    first = repository(db)
    await first.initialize(generation=generation, expires_at=asyncio.get_running_loop().time() + 6)
    key = uuid4().int % (2**63 - 1)
    second = repository(PausedClaimClient(clients[1], key))
    connection = await asyncpg.connect(os.environ["DATABASE_URL"], timeout=5, command_timeout=5)
    task = None
    try:
        async with connection.transaction():
            await connection.execute("SELECT pg_advisory_xact_lock($1::bigint)", key)
            task = asyncio.create_task(next_page(second, generation, worker="paused"))
            await wait_for_claim_snapshot(connection)
            page = await next_page(first, generation, worker="winner", limit=first_limit)
            assert len(page.sequences) == first_limit
            assert (
                await first.materialize(page, expires_at=asyncio.get_running_loop().time() + 6)
                == first_limit
            )
        page = await task
        if first_limit == 4:
            assert page is None
        else:
            assert page.after_sequence > 0 and len(page.sequences) == 2
            assert (
                await first.materialize(page, expires_at=asyncio.get_running_loop().time() + 6) == 2
            )
        assert await next_page(first, generation) is None
        assert await effects(db, generation) == {"facts": 4, "audits": 4, "rolled": 4}
        assert await _window(db, window) == (Decimal(0), Decimal(4), Decimal(0))
        held = await db.query_raw(
            "SELECT count(*)::integer AS count FROM deltallm_accounting_projection_checkpoints "
            "WHERE projection_name='accounting-read-model-v2' AND generation=$1 "
            "AND lease_token IS NOT NULL",
            generation,
        )
        assert held == [{"count": 0}]
    finally:
        if task is not None:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await connection.close(timeout=5)
