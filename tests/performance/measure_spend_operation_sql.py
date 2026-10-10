"""Bounded dependency counts and representative plans in disposable migrated tables."""

import argparse
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

from scripts.benchmarks.ingestion_database import ingestion_database
from src.billing.spend_operations import (
    OperationAttempt,
    OperationHandle,
    OperationPrincipal,
    SpendOperationIntent,
)
from src.db.spend_ingestion import SpendIngestionRepository
from src.db.spend_operations import SpendOperationRepository
from tests.performance.gateway_concurrency_dependencies import fixture_database_url


class CountedDatabase:
    def __init__(self, db, shared=None):
        self.db = db
        self.shared = shared if shared is not None else {"transactions": 0, "statements": []}

    def is_transaction(self):
        return self.db.is_transaction()

    @asynccontextmanager
    async def tx(self, **kwargs):
        self.shared["transactions"] += 1
        async with self.db.tx(**kwargs) as tx:
            yield CountedDatabase(tx, self.shared)

    async def query_raw(self, query, *values):
        self.shared["statements"].append((query, values))
        return await self.db.query_raw(query, *values)


def operation():
    return OperationHandle(
        event_id=uuid4(),
        owner_token=uuid4(),
        expires_at=datetime.now(UTC) + timedelta(minutes=15),
        intent=SpendOperationIntent(
            principal=OperationPrincipal(api_key="fixture-hash"),
            model="fixture-model",
            call_type="completion",
            started_at=datetime.now(UTC),
            attempts=(
                OperationAttempt(
                    deployment_id="fixture-deployment",
                    provider="openai",
                    model="fixture-model",
                    pricing={"currency": "USD"},
                ),
            ),
        ),
    )


def safe_plan(node):
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
        safe["Plans"] = [safe_plan(child) for child in node["Plans"]]
    if node.get("Relation Name") in {"deltallm_spend_ingestion_outbox", "deltallm_spendlog_events"}:
        assert node["Node Type"] != "Seq Scan", safe
    return safe


async def explain(db, query, values):
    result = await db.query_raw("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + query, *values)
    report = result[0]["QUERY PLAN"][0]
    return {
        "plan": safe_plan(report["Plan"]),
        "planning_ms": report["Planning Time"],
        "execution_ms": report["Execution Time"],
    }


async def measure(args: argparse.Namespace, *, database_url: str | None = None) -> None:
    import asyncio

    async with ingestion_database(database_url or fixture_database_url()) as db:
        await db.observer.execute_raw(
            "CREATE TABLE deltallm_spendlog_events (LIKE public.deltallm_spendlog_events INCLUDING ALL)"
        )
        await db.observer.execute_raw(
            "INSERT INTO deltallm_spendlog_events(id,request_id,call_type,api_key,model,spend,start_time,end_time) SELECT 'history-'||n,'history-'||n,'completion','fixture-hash','fixture-model',0,NOW(),NOW() FROM generate_series(1,100000) n"
        )
        await db.observer.execute_raw(
            "INSERT INTO deltallm_spend_ingestion_outbox(event_id,event_type,payload_json,status,processed_at) SELECT 'retained-'||n,'spend','{}','completed',NOW() FROM generate_series(1,10000) n"
        )
        await db.observer.execute_raw("ANALYZE deltallm_spendlog_events")
        await db.observer.execute_raw("ANALYZE deltallm_spend_ingestion_outbox")
        await db.acceptance.query_raw("SELECT 1")
        await db.worker.query_raw("SELECT 1")
        counted = CountedDatabase(db.acceptance)
        await SpendIngestionRepository(counted).enqueue(
            event_id=str(uuid4()),
            event_type="spend",
            payload={"cost_exact": "0"},
            max_attempts=2,
            max_pending_events=100000,
        )
        baseline = {
            "transactions": counted.shared["transactions"],
            "statements": len(counted.shared["statements"]),
        }
        counted.shared.update(transactions=0, statements=[])
        op = operation()
        repository = SpendOperationRepository(counted)
        await repository.begin(
            op, capacity=100000, max_attempts=2, expires_at=asyncio.get_running_loop().time() + 0.25
        )
        admission = {
            "transactions": counted.shared["transactions"],
            "statements": len(counted.shared["statements"]),
        }
        query, values = counted.shared["statements"][-1]
        # New server ID tests the insertion branch; the original remains available for receipt.
        alternative = operation()
        changed = (str(alternative.event_id), str(alternative.owner_token), *values[2:])
        admission_plan = await explain(db.observer, query, changed)
        counted.shared.update(transactions=0, statements=[])
        await repository.accept(
            event_id=str(op.event_id),
            owner_token=str(op.owner_token),
            payload={
                **op.intent.principal.model_dump(),
                "model": op.intent.model,
                "call_type": op.intent.call_type,
                "cost_exact": "0.1",
            },
            expires_at=asyncio.get_running_loop().time() + 0.25,
        )
        receipt = {
            "transactions": counted.shared["transactions"],
            "statements": len(counted.shared["statements"]),
        }
        query, values = counted.shared["statements"][-1]
        receipt_plan = await explain(db.observer, query, values)
        await db.observer.execute_raw(
            "UPDATE deltallm_spend_ingestion_outbox SET operation_expires_at=NOW() WHERE operation_state='dispatched'"
        )
        await db.observer.execute_raw(
            "INSERT INTO deltallm_spend_ingestion_outbox (event_id,event_type,payload_json,status,blocked_at,operation_owner,operation_intent,operation_state,operation_expires_at) "
            "SELECT 'recovery-'||n,'spend','{}','blocked',NOW(),$1,$2::jsonb,'dispatched',NOW() FROM generate_series(1,1000) n",
            str(op.owner_token),
            op.intent.model_dump_json(),
        )
        await db.observer.execute_raw("ANALYZE deltallm_spend_ingestion_outbox")
        counted.shared.update(transactions=0, statements=[])
        await repository.recover_expired()
        query, values = counted.shared["statements"][-1]
        recovery_plan = await explain(db.observer, query, values)
        assert baseline == {"transactions": 1, "statements": 2}
        assert admission == {"transactions": 1, "statements": 3}
        # One owner-fenced UPDATE commits atomically without interactive
        # transaction start, timeout setup or commit round trips.
        assert receipt == {"transactions": 0, "statements": 1}
        # LIKE INCLUDING ALL creates schema-local index names. Check its
        # definition instead of requiring the production index name.
        indexes = await db.observer.query_raw(
            "SELECT indexname,indexdef FROM pg_indexes WHERE schemaname=$1 AND tablename='deltallm_spend_ingestion_outbox'",
            db.schema,
        )
        recovery_indexes = [
            r["indexname"] for r in indexes if "operation_expires_at" in r["indexdef"]
        ]
        assert any(name in json.dumps(recovery_plan) for name in recovery_indexes)
        assert '"Node Type": "Tid Scan"' in json.dumps(recovery_plan)
        report = {
            "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
            "python": sys.version.split()[0],
            "history_rows": 100000,
            "retained_outbox_rows": 10000,
            "expired_operations": 1000,
            "before": baseline,
            "after_admission": admission,
            "after_receipt": receipt,
            "admission_plan": admission_plan,
            "receipt_plan": receipt_plan,
            "recovery_plan": recovery_plan,
        }
        report["repository_sha256"] = hashlib.sha256(
            Path("src/db/spend_operations.py").read_bytes()
        ).hexdigest()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n")
        print(
            json.dumps(
                {key: report[key] for key in ("before", "after_admission", "after_receipt")},
                indent=2,
            )
        )


if __name__ == "__main__":
    import asyncio

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(measure(parser.parse_args()))
