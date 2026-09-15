"""Reproducible PR5 SQL/cardinality probe on the disposable concurrency database."""

from __future__ import annotations

import argparse
import asyncio
import json
import hashlib
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

from scripts.measure_gateway_load import (
    RequestResult,
    run_constant_arrival,
    summarize,
    write_results,
)
from src.billing.budget import BudgetEnforcementService
from src.db.allocated_client import AllocatedPrisma, DatabaseOwner
from src.db.allocation_config import DatabasePolicy
from src.db.budgets import BudgetRepository
from tests.performance.gateway_concurrency_dependencies import fixture_database_url

TABLES = (
    ("deltallm_teammodelspend", "team_id"),
    ("deltallm_verificationtoken", "token"),
    ("deltallm_usertable", "user_id"),
    ("deltallm_teamtable", "team_id"),
    ("deltallm_organizationtable", "organization_id"),
)


def source_manifest():
    digest = hashlib.sha256()
    for path in sorted(Path("src").rglob("*.py")) + [Path("uv.lock")]:
        digest.update(str(path).encode() + b"\0" + path.read_bytes())
    return dict(
        commit=subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        source_sha256=digest.hexdigest(),
        python=sys.version.split()[0],
        harness_sha256={
            str(path): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (
                Path("tests/performance/measure_budget_dependencies.py"),
                Path("tests/performance/measure_prompt_fills.py"),
            )
        },
    )


class CountedDB:
    def __init__(self, db):
        self.db, self.calls = db, 0

    async def query_raw(self, query, *values):
        self.calls += 1
        return await self.db.query_raw(query, *values)

    async def execute_raw(self, query, *values):
        self.calls += 1
        return await self.db.execute_raw(query, *values)


async def seed(db, prefix, count):
    await db.execute_raw(
        """
        WITH identities AS MATERIALIZED (SELECT $1 || n::text AS id FROM generate_series(1,$2::int) n),
        org AS (
          INSERT INTO deltallm_organizationtable (id,organization_id,max_budget,spend)
          SELECT id,id,100,1 FROM identities RETURNING organization_id
        ), team AS (
          INSERT INTO deltallm_teamtable (team_id,organization_id,models,max_budget,spend,model_max_budget)
          SELECT organization_id,organization_id,ARRAY[]::text[],100,1,'{"model":100,"other-model-10000":100}'::jsonb FROM org RETURNING team_id
        ), principal AS (
          INSERT INTO deltallm_usertable (user_id,team_id,models,max_budget,spend)
          SELECT team_id,team_id,ARRAY[]::text[],100,1 FROM team RETURNING user_id,team_id
        ), keys AS (
          INSERT INTO deltallm_verificationtoken (id,token,user_id,team_id,models,max_budget,spend)
          SELECT user_id,user_id,user_id,team_id,ARRAY[]::text[],100,1 FROM principal RETURNING team_id
        )
        INSERT INTO deltallm_teammodelspend (team_id,model,spend,updated_at,reconciled_at)
        SELECT team_id,'model',1,NOW(),NOW() FROM keys
        """,
        prefix,
        count,
    )
    await db.execute_raw(
        """INSERT INTO deltallm_teammodelspend (team_id,model,spend,updated_at,reconciled_at)
           SELECT $1 || '1','other-model-' || n::text,1,NOW(),NOW() FROM generate_series(1,10000) n""",
        prefix,
    )
    for table, _ in TABLES:
        await db.execute_raw(f"ANALYZE {table}")


async def history(db, identity, count):
    # Zero-cost historical fixture rows change only cardinality, not budget policy.
    await db.execute_raw(
        """INSERT INTO deltallm_spendlog_events
            (id,request_id,call_type,api_key,user_id,team_id,organization_id,model,spend,spend_exact,start_time,end_time)
            SELECT $1 || '-event-' || n::text,$1 || '-event-' || n::text,'completion',$1,$1,$1,$1,'model',0,0,NOW(),NOW()
            FROM generate_series(1,$2::int) n""",
        identity,
        count,
    )
    await db.execute_raw("ANALYZE deltallm_spendlog_events")


async def query_plan(db, args):
    class Capture:
        async def query_raw(self, query, *values):
            self.query, self.values = query, values
            return []

    capture = Capture()
    await BudgetRepository(capture).get_snapshot(**args)
    assert "deltallm_spendlog_events" not in capture.query
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
                "Shared Hit Blocks",
                "Shared Read Blocks",
                "Rows Removed by Filter",
            )
            if key in node
        }
        if "Plans" in node:
            safe["Plans"] = [redact(child) for child in node["Plans"]]
        return safe

    plan = redact(report["Plan"])
    assert report["Plan"]["Actual Rows"] == 5

    def assert_counter_lookup(node):
        if node.get("Relation Name") == "deltallm_teammodelspend":
            assert node.get("Index Name") == "deltallm_teammodelspend_pkey"
            assert node["Actual Rows"] <= 1
            assert node.get("Rows Removed by Filter", 0) == 0
        for child in node.get("Plans", []):
            assert_counter_lookup(child)

    assert_counter_lookup(plan)
    assert "Seq Scan" not in json.dumps(plan), "representative hot query must use scope indexes"
    return dict(
        plan=plan, planning_ms=report["Planning Time"], execution_ms=report["Execution Time"]
    )


async def measure(db, args, output, label, rate, duration):
    results = {}
    for mode in ("legacy", "combined"):
        counted = CountedDB(db)
        service = BudgetEnforcementService(counted, query_mode=mode)
        for _ in range(25):
            await service.check_budgets(**args)
        counted.calls = 0

        async def request(_index, _request_id):
            await service.check_budgets(**args)
            return RequestResult(status_code=200)

        result = await run_constant_arrival(
            rate=rate, duration_seconds=duration, max_in_flight=64, request=request
        )
        summary = summarize(result, target_rate=rate)
        raw, summary_file = write_results(result, summary, output / f"{label}-{mode}")
        results[mode] = dict(
            summary=summary,
            sql_calls=counted.calls,
            calls_per_request=counted.calls / len(result.samples),
            raw=str(raw.relative_to(output)),
            summary_file=str(summary_file.relative_to(output)),
        )
        assert all(sample.status_code == 200 for sample in result.samples)
        assert result.generator_dropped_count == 0
        assert counted.calls == len(result.samples) * (6 if mode == "legacy" else 1)
    return results


async def run(args):
    prefix = "pr5-probe-" + uuid4().hex + "-"
    identity = prefix + "1"
    owner = DatabaseOwner(DatabasePolicy("control", 1, 0.2, 60, 1, 120))
    db = AllocatedPrisma(
        allocation=owner, datasource={"url": owner.policy.connection_url(fixture_database_url())}
    )
    foreground_owner = DatabaseOwner(DatabasePolicy("foreground", 8, 0.2, 1, 0.2, 2))
    foreground = AllocatedPrisma(
        allocation=foreground_owner,
        datasource={"url": foreground_owner.policy.connection_url(fixture_database_url())},
    )
    args.output.mkdir(parents=True, exist_ok=True)
    scopes = dict(
        api_key=identity,
        user_id=identity,
        team_id=identity,
        organization_id=identity,
        model="other-model-10000",
    )
    report = {
        "source": source_manifest(),
        "scope_rows_per_table": args.entities,
        "historical_rows_added": args.events,
        "additional_models_for_hot_team": 10000,
        "scope": "Budget service only, real PostgreSQL; no provider, Redis, HTTP or pod qualification",
    }
    try:
        await db.connect()
        await foreground.connect()
        await seed(db, prefix, args.entities)
        report["before_history_plan"] = await query_plan(foreground, scopes)
        report["before_history"] = await measure(
            foreground, scopes, args.output, "before-history", args.rate, args.duration
        )
        await history(db, identity, args.events)
        report["after_history_plan"] = await query_plan(foreground, scopes)
        report["after_history"] = await measure(
            foreground, scopes, args.output, "after-history", args.rate, args.duration
        )
        (args.output / "budget-report.json").write_text(json.dumps(report, indent=2) + "\n")
    finally:
        await foreground_owner.close()
        await foreground.disconnect()
        await db.execute_raw("DELETE FROM deltallm_spendlog_events WHERE api_key=$1", identity)
        for table, column in TABLES:
            await db.execute_raw(f"DELETE FROM {table} WHERE {column} LIKE $1", prefix + "%")
        await owner.close()
        await db.disconnect()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--entities", type=int, default=5000)
    parser.add_argument("--events", type=int, default=100000)
    parser.add_argument("--rate", type=float, default=50)
    parser.add_argument("--duration", type=float, default=10)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not (
        1000 <= args.entities <= 10000
        and 10000 <= args.events <= 200000
        and 1 <= args.rate <= 100
        and 5 <= args.duration <= 60
    ):
        parser.error(
            "bounded probe: entities 1000–10000, events 10000–200000, rate 1–100, duration 5–60"
        )
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
