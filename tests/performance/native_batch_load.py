"""Submit and observe batch jobs against the native fixture through the public API."""

from __future__ import annotations

import asyncio
import json
from time import monotonic

import httpx
from prisma import Prisma

from tests.performance.gateway_concurrency_fixture import MODEL
from tests.performance.lifecycle_cluster import LOAD_KEY
from tests.performance.native_fixture import EMBEDDING_MODEL

TERMINAL = {"completed", "failed", "expired", "cancelled"}
CHAT_ENDPOINT = "/v1/chat/completions"
EMBEDDING_ENDPOINT = "/v1/embeddings"
MAX_JOB_ITEMS = 10_000


def batch_rows(endpoint: str, prefix: str, count: int) -> list[dict]:
    if not 1 <= count <= MAX_JOB_ITEMS:
        raise ValueError("Batch job size is outside the gateway's per-batch limit")
    if endpoint == CHAT_ENDPOINT:
        body = {
            "model": MODEL,
            "max_tokens": 1,
            "messages": [{"role": "user", "content": "Soak batch fixture."}],
            "metadata": {"cache": False},
        }
        return [
            {"custom_id": f"{prefix}-{index}", "method": "POST", "url": endpoint, "body": body}
            for index in range(count)
        ]
    if endpoint == EMBEDDING_ENDPOINT:
        return [
            {
                "custom_id": f"{prefix}-{index}",
                "method": "POST",
                "url": endpoint,
                # Unique inputs keep any response cache out of the result.
                "body": {"model": EMBEDDING_MODEL, "input": f"{prefix} item {index}"},
            }
            for index in range(count)
        ]
    raise ValueError("Unsupported batch endpoint")


def job_plan(total_items: int, jobs: int) -> list[tuple[str, int]]:
    """Split items across jobs, alternating chat and embeddings, sizes differing by one."""
    if not 1 <= jobs <= 20 or total_items < jobs:
        raise ValueError("Batch plan needs 1 to 20 jobs and at least one item per job")
    base, extra = divmod(total_items, jobs)
    return [
        (CHAT_ENDPOINT if index % 2 == 0 else EMBEDDING_ENDPOINT, base + (index < extra))
        for index in range(jobs)
    ]


def client(url: str, *, timeout: float = 60) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=url,
        timeout=timeout,
        trust_env=False,
        headers={"Authorization": "Bearer " + LOAD_KEY},
    )


async def submit_job(http: httpx.AsyncClient, endpoint: str, prefix: str, count: int) -> str:
    rows = batch_rows(endpoint, prefix, count)
    uploaded = await http.post(
        "/v1/files",
        data={"purpose": "batch"},
        files={
            "file": (
                f"{prefix}.jsonl",
                "\n".join(json.dumps(row) for row in rows),
                "application/jsonl",
            )
        },
    )
    if uploaded.status_code != 200:
        raise AssertionError(
            f"Batch upload failed with {uploaded.status_code}: {uploaded.text[:500]}"
        )
    created = await http.post(
        "/v1/batches",
        json={
            "input_file_id": uploaded.json()["id"],
            "endpoint": endpoint,
            "completion_window": "24h",
        },
    )
    if created.status_code != 200:
        raise AssertionError(
            f"Batch create failed with {created.status_code}: {created.text[:500]}"
        )
    return created.json()["id"]


async def wait_jobs(
    http: httpx.AsyncClient, batch_ids: list[str], *, timeout: float
) -> tuple[dict[str, dict], float, bool]:
    """Poll every job until terminal. Return final documents, elapsed, and timed-out."""
    started = monotonic()
    final: dict[str, dict] = {}
    latest: dict[str, dict] = {}
    while len(final) < len(batch_ids):
        for batch_id in batch_ids:
            if batch_id in final:
                continue
            response = await http.get("/v1/batches/" + batch_id)
            if response.status_code != 200:
                raise AssertionError(f"Batch read failed with {response.status_code}")
            document = response.json()
            latest[batch_id] = document
            if document["status"] in TERMINAL:
                document["observed_terminal_seconds"] = monotonic() - started
                final[batch_id] = document
        if len(final) == len(batch_ids):
            break
        if monotonic() - started >= timeout:
            return {**latest, **final}, monotonic() - started, True
        await asyncio.sleep(2)
    return final, monotonic() - started, False


async def verify_outputs(http: httpx.AsyncClient, document: dict) -> int:
    """Every output row is present once and carries no error."""
    output = await http.get("/v1/files/" + document["output_file_id"] + "/content")
    if output.status_code != 200:
        raise AssertionError(f"Batch output read failed with {output.status_code}")
    rows = [json.loads(line) for line in output.text.splitlines() if line]
    if len(rows) != len({row["custom_id"] for row in rows}):
        raise AssertionError("Batch output repeated a custom_id")
    if any(row.get("error") is not None for row in rows):
        raise AssertionError("Batch output carried an item error")
    return len(rows)


async def item_stats(db: Prisma, batch_ids: list[str]) -> list[dict]:
    """Attempts, retries and slowest item per job; no request or response bodies."""
    async with asyncio.timeout(30):
        return await db.query_raw(
            "SELECT batch_id,count(*)::int AS items,"
            "count(*) FILTER (WHERE status='completed')::int AS completed,"
            "count(*) FILTER (WHERE status='failed')::int AS failed,"
            "count(*) FILTER (WHERE attempts>1)::int AS retried,"
            "COALESCE(max(attempts),0)::int AS max_attempts,"
            "COALESCE(max(EXTRACT(EPOCH FROM (completed_at-created_at))),0)::float8 "
            "AS slowest_item_seconds,"
            "array_remove(array_agg(DISTINCT left(last_error,120)),NULL) AS last_errors "
            "FROM deltallm_batch_item WHERE batch_id=ANY($1::text[]) GROUP BY batch_id "
            "ORDER BY batch_id LIMIT 32",
            batch_ids,
        )


async def duplicate_operations(db: Prisma) -> int:
    async with asyncio.timeout(30):
        rows = await db.query_raw(
            "SELECT (count(*)-count(DISTINCT operation_id))::int AS duplicates "
            "FROM deltallm_accounting_usage_facts_v2"
        )
    return int(rows[0]["duplicates"])


async def budget_scopes(db: Prisma) -> list[dict]:
    async with asyncio.timeout(5):
        return await db.query_raw(
            "SELECT scope_type,committed_exact::text AS committed,"
            "reserved_exact::text AS reserved,provisional_exact::text AS provisional "
            "FROM deltallm_accounting_budget_windows "
            "WHERE protocol_name='primary' AND generation=1 ORDER BY scope_type LIMIT 65"
        )
