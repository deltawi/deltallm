"""Observe real accepted work, leases and economic effects in the isolated fixture."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
from decimal import Decimal
import json
from pathlib import Path
from time import monotonic
from uuid import uuid4

import httpx

from src.db.spend_ingestion import SpendIngestionRepository
from tests.performance.gateway_concurrency_dependencies import local_database
from tests.performance.lifecycle_cluster import LOAD_KEY


@asynccontextmanager
async def held_ledger():
    async with local_database() as db:
        async with db.tx(timeout=timedelta(seconds=180)) as tx:
            # Compatible with principal existence checks, but blocks ledger
            # updates. Acceptance still commits through its separate allocation.
            await tx.query_raw(
                "SELECT organization_id FROM deltallm_organizationtable "
                "WHERE organization_id=$1 FOR NO KEY UPDATE",
                "concurrency-org",
            )
            await tx.execute_raw("LOCK TABLE deltallm_auditevent IN SHARE MODE")
            yield


async def accept(url: str) -> str:
    request_id = uuid4().hex
    async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
        response = await client.post(
            url + "/v1/chat/completions",
            headers={"Authorization": "Bearer " + LOAD_KEY, "x-request-id": request_id},
            json={
                "model": "concurrency-fixture",
                "max_tokens": 1,
                "messages": [{"role": "user", "content": "Reply with OK."}],
                "metadata": {"cache": False},
            },
        )
        assert response.status_code == 200, response.text
    return request_id


async def claim(request_id: str) -> dict:
    async with local_database() as db:
        deadline = monotonic() + 10
        while monotonic() < deadline:
            rows = await db.query_raw(
                "SELECT event_id,locked_by,claim_token,status FROM deltallm_spend_ingestion_outbox "
                "WHERE payload_json->>'request_id'=$1 AND status='processing' LIMIT 2",
                request_id,
            )
            if rows:
                assert len(rows) == 1
                return rows[0]
            await asyncio.sleep(0.01)
    raise TimeoutError("accepted fixture event was not claimed")


async def assert_backlog(request_id: str) -> None:
    async with local_database() as db:
        rows = await db.query_raw(
            "SELECT status FROM deltallm_spend_ingestion_outbox "
            "WHERE payload_json->>'request_id'=$1 LIMIT 2",
            request_id,
        )
        assert len(rows) == 1
        assert rows[0]["status"] in {"queued", "processing", "retry"}, rows
        audit = await db.query_raw(
            "SELECT status FROM deltallm_audit_ingestion_outbox "
            "WHERE payload_json->'event'->>'request_id'=$1 LIMIT 16",
            request_id,
        )
        assert audit and any(row["status"] != "completed" for row in audit), audit


async def recovered(request_id: str, *, old_claim: dict | None = None) -> dict:
    async with local_database() as db:
        deadline = monotonic() + 90
        while monotonic() < deadline:
            rows = await db.query_raw(
                "SELECT event_id,status,attempt_count,operation_state FROM deltallm_spend_ingestion_outbox "
                "WHERE payload_json->>'request_id'=$1 LIMIT 2",
                request_id,
            )
            if len(rows) == 1 and rows[0]["status"] == "completed":
                break
            await asyncio.sleep(0.2)
        else:
            raise TimeoutError("accepted spend did not recover")
        ledger = await db.query_raw(
            "SELECT count(*)::int AS rows, COALESCE(sum(spend_exact),0)::text AS spend "
            "FROM deltallm_spendlog_events WHERE request_id=$1",
            request_id,
        )
        assert ledger[0]["rows"] == 1, ledger
        assert Decimal(ledger[0]["spend"]) == Decimal("0.000007"), ledger
        if old_claim:
            stale = await SpendIngestionRepository(db).mark_completed(
                event_ids=[old_claim["event_id"]],
                worker_id=old_claim["locked_by"],
                claim_token=old_claim["claim_token"],
            )
            assert stale == 0, "lost-pod claim was allowed to acknowledge completion"
        while monotonic() < deadline:
            audit = await db.query_raw(
                "SELECT o.event_id,o.status,(a.event_id IS NOT NULL) AS persisted "
                "FROM deltallm_audit_ingestion_outbox o LEFT JOIN deltallm_auditevent a "
                "ON a.event_id::text=o.event_id WHERE o.payload_json->'event'->>'request_id'=$1 LIMIT 16",
                request_id,
            )
            if audit and all(row["status"] == "completed" and row["persisted"] for row in audit):
                break
            await asyncio.sleep(0.2)
        else:
            raise TimeoutError("accepted audit did not recover")
        return {"request_id": request_id, "outbox": rows[0], "ledger": ledger[0], "audit": audit}


async def export_records(output: Path) -> None:
    async with local_database() as db:
        # Explicit columns: no API keys, prompts, provider credentials or payloads.
        records = {
            "ledger": await db.query_raw(
                "SELECT id,request_id,spend_exact::text AS spend,status "
                "FROM deltallm_spendlog_events WHERE organization_id=$1 ORDER BY id LIMIT 4096",
                "concurrency-org",
            ),
            "spend": await db.query_raw(
                "SELECT event_id,status,attempt_count,operation_state,payload_json->>'request_id' AS request_id "
                "FROM deltallm_spend_ingestion_outbox ORDER BY event_id LIMIT 4096"
            ),
            "audit": await db.query_raw(
                "SELECT event_id,status,attempt_count,payload_json->'event'->>'request_id' AS request_id "
                "FROM deltallm_audit_ingestion_outbox ORDER BY event_id LIMIT 4096"
            ),
            "batch_items": await db.query_raw(
                "SELECT batch_id,item_id,custom_id,status,attempts FROM deltallm_batch_item "
                "ORDER BY item_id LIMIT 4096"
            ),
            "batch_completions": await db.query_raw(
                "SELECT completion_id,batch_id,item_id,status FROM deltallm_batch_completion_outbox "
                "ORDER BY completion_id LIMIT 4096"
            ),
        }
    output.write_text(json.dumps(records, indent=2) + "\n")
