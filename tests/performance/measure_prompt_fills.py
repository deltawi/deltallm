"""PR5 prompt cache round trips with real local PostgreSQL and Redis."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from scripts.measure_gateway_load import (
    RequestResult,
    run_constant_arrival,
    summarize,
    write_results,
)
from src.db.allocated_client import AllocatedPrisma, DatabaseOwner
from src.db.allocation_config import DatabasePolicy
from src.db.prompt_registry import PromptRegistryRepository
from src.redis_runtime import build_redis_client
from src.services.prompt_registry import PromptRegistryService
from tests.performance.gateway_concurrency_dependencies import (
    fixture_database_url,
    require_local_url,
)
from tests.performance.measure_budget_dependencies import CountedDB, source_manifest

SCOPE_TYPES = ("user", "api_key", "team", "organization", "group")


class CountedRedis:
    def __init__(self, redis):
        self.redis = redis
        self.reads = self.writes = self.pipelines = 0

    async def mget(self, keys):
        self.reads += 1
        return await self.redis.mget(keys)

    async def setex(self, *args):
        self.writes += 1
        return await self.redis.setex(*args)

    def pipeline(self, **kwargs):
        self.pipelines += 1
        return self.redis.pipeline(**kwargs)


class SerialFillBaseline(PromptRegistryService):
    async def _write_binding_fills(self, fills):
        # The previous fill implementation, isolated from all other changes.
        for key, payload in fills.items():
            await self._write_l2(key, payload)


async def binding_plan(db, identity):
    class Capture:
        async def query_raw(self, query, *values):
            self.query, self.values = query, values
            return []

    capture = Capture()
    await PromptRegistryRepository(capture).resolve_binding_chain(
        scopes=[("organization", identity)]
    )
    rows = await db.query_raw(
        "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + capture.query, *capture.values
    )
    report = rows[0]["QUERY PLAN"][0]

    def redact(node):
        safe = {
            key: node[key]
            for key in (
                "Node Type",
                "Relation Name",
                "Index Name",
                "Actual Rows",
                "Actual Loops",
                "Rows Removed by Filter",
                "Shared Hit Blocks",
                "Shared Read Blocks",
            )
            if key in node
        }
        if "Plans" in node:
            safe["Plans"] = [redact(child) for child in node["Plans"]]
        return safe

    plan = redact(report["Plan"])
    assert report["Plan"]["Actual Rows"] == 1
    assert "deltallm_promptbinding_enabled_top_idx" in json.dumps(plan)
    return dict(
        plan=plan, planning_ms=report["Planning Time"], execution_ms=report["Execution Time"]
    )


async def run(output):
    identity = "pr5-prompt-" + uuid4().hex
    config = SimpleNamespace(
        redis_url=require_local_url(os.environ["REDIS_URL"], schemes={"redis", "rediss"}),
        redis_cache_max_connections=32,
    )
    redis = build_redis_client(config, config, allocation="cache")
    owner = DatabaseOwner(DatabasePolicy("foreground", 32, 0.2, 1, 0.2, 2))
    db = AllocatedPrisma(
        allocation=owner, datasource={"url": owner.policy.connection_url(fixture_database_url())}
    )
    maintenance_owner = DatabaseOwner(DatabasePolicy("control", 1, 0.2, 30, 1, 60))
    maintenance = AllocatedPrisma(
        allocation=maintenance_owner,
        datasource={"url": maintenance_owner.policy.connection_url(fixture_database_url())},
    )
    services, keys, report = [], set(), {"source": source_manifest()}
    output.mkdir(parents=True, exist_ok=True)
    try:
        await db.connect()
        await maintenance.connect()
        await maintenance.execute_raw(
            "INSERT INTO deltallm_organizationtable (id,organization_id) VALUES ($1,$1)", identity
        )
        await maintenance.execute_raw(
            "INSERT INTO deltallm_prompttemplate (prompt_template_id,template_key,name,created_at,updated_at) VALUES ($1,$1,$1,NOW(),NOW())",
            identity,
        )
        await maintenance.execute_raw(
            """INSERT INTO deltallm_promptbinding (prompt_binding_id,scope_type,scope_id,prompt_template_id,label,priority,created_at,updated_at)
               SELECT $1 || n::text,'organization',$1,$1,'label-' || n::text,100,NOW(),NOW() FROM generate_series(1,10000) n""",
            identity,
        )
        await maintenance.execute_raw("ANALYZE deltallm_promptbinding")
        report["hot_scope_plan"] = await binding_plan(db, identity)
        report["hot_scope_binding_rows"] = 10000
        report["profile"] = (
            "One Python process; simulated independent L1 owners; shared bounded PostgreSQL/Redis pools of 32; no pod qualification"
        )
        for name, cls in (("serial", SerialFillBaseline), ("pipeline", PromptRegistryService)):
            counted_db, counted_redis = CountedDB(db), CountedRedis(redis)
            service = cls(
                repository=PromptRegistryRepository(counted_db), redis_client=counted_redis
            )
            services.append(service)

            async def request(index, _request_id):
                scopes = [(scope, f"{identity}-{name}-{index}") for scope in SCOPE_TYPES]
                keys.update(service._binding_cache_key(*scope) for scope in scopes)
                assert await service._resolve_binding_chain(scopes) == [None] * 5
                return RequestResult(status_code=200)

            result = await run_constant_arrival(
                rate=10, duration_seconds=5, max_in_flight=32, request=request
            )
            summary = summarize(result, target_rate=10)
            raw, _ = write_results(result, summary, output / name)
            report[name] = dict(
                summary=summary,
                sql=counted_db.calls,
                mget=counted_redis.reads,
                setex_round_trips=counted_redis.writes,
                pipelines=counted_redis.pipelines,
                raw=str(raw.relative_to(output)),
            )
            assert counted_db.calls == counted_redis.reads == 50
            assert (counted_redis.writes, counted_redis.pipelines) == (
                (250, 0) if name == "serial" else (0, 50)
            )
            warm_scopes = [(scope, f"{identity}-{name}-49") for scope in SCOPE_TYPES]
            for _ in range(100):
                assert await service._resolve_binding_chain(warm_scopes) == [None] * 5
            assert counted_db.calls == counted_redis.reads == 50
            report[name]["warm_100_extra_sql_or_redis"] = 0

        # A hot cold-start wave across independent L1/singleflight owners. Shared
        # pools bound the aggregate more tightly than 25 production replica pools.
        scopes = [(scope, identity + "-wave") for scope in SCOPE_TYPES]
        counted = CountedDB(db)
        wave = [
            PromptRegistryService(repository=PromptRegistryRepository(counted), redis_client=redis)
            for _ in range(25)
        ]
        services.extend(wave)
        keys.update(wave[0]._binding_cache_key(*scope) for scope in scopes)
        results = await asyncio.gather(
            *(service._resolve_binding_chain(scopes) for service in wave)
        )
        assert results == [[None] * 5] * 25
        report["cold_wave"] = dict(independent_l1_owners=25, sql=counted.calls, successful=25)
        before = counted.calls
        new_owner = PromptRegistryService(
            repository=PromptRegistryRepository(counted), redis_client=redis
        )
        services.append(new_owner)
        assert await new_owner._resolve_binding_chain(scopes) == [None] * 5
        assert counted.calls == before
        report["new_owner_after_fill_extra_sql"] = 0
        assert owner.gate.active == redis.connection_pool.gate.active == 0
        (output / "prompt-report.json").write_text(json.dumps(report, indent=2) + "\n")
    finally:
        for service in services:
            await service.shutdown()
        if keys:
            await redis.delete(*keys)
        await redis.aclose()
        await maintenance.execute_raw(
            "DELETE FROM deltallm_prompttemplate WHERE prompt_template_id=$1", identity
        )
        await maintenance.execute_raw(
            "DELETE FROM deltallm_organizationtable WHERE organization_id=$1", identity
        )
        await owner.close()
        await db.disconnect()
        await maintenance_owner.close()
        await maintenance.disconnect()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(run(parser.parse_args().output))
