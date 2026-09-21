"""Exercise accepted batch work and shared artifacts across the real rollout."""

import asyncio
import json
from time import monotonic

import httpx

from tests.performance.gateway_concurrency_dependencies import local_database
from tests.performance.lifecycle_cluster import LOAD_KEY, LifecycleCluster

ITEMS = 20


async def start_batch(cluster: LifecycleCluster, url: str) -> str:
    rows = [
        {
            "custom_id": f"lifecycle-{index}",
            "method": "POST",
            "url": "/v1/chat/completions",
            "body": {
                "model": "concurrency-fixture",
                "max_tokens": 1,
                "messages": [{"role": "user", "content": "Lifecycle batch fixture."}],
                "metadata": {"cache": False},
            },
        }
        for index in range(ITEMS)
    ]
    async with httpx.AsyncClient(
        timeout=15, trust_env=False, headers={"Authorization": "Bearer " + LOAD_KEY}
    ) as client:
        uploaded = await client.post(
            url + "/v1/files",
            files={
                "file": (
                    "lifecycle.jsonl",
                    "\n".join(json.dumps(row) for row in rows),
                    "application/jsonl",
                )
            },
        )
        assert uploaded.status_code == 200, uploaded.text
        created = await client.post(
            url + "/v1/batches",
            json={"input_file_id": uploaded.json()["id"], "endpoint": "/v1/chat/completions"},
        )
        assert created.status_code == 200, created.text
        batch_id = created.json()["id"]
    async with local_database() as db:
        deadline = monotonic() + 30
        while monotonic() < deadline:
            items = await db.query_raw(
                "SELECT item_id,status,locked_by FROM deltallm_batch_item "
                "WHERE batch_id=$1 AND status='in_progress' LIMIT 20",
                batch_id,
            )
            if items:
                cluster.event("batch_claimed_before_rollout", batch_id=batch_id, active=len(items))
                return batch_id
            await asyncio.sleep(0.2)
    raise TimeoutError("batch worker did not claim accepted input")


async def finish_batch(cluster: LifecycleCluster, url: str, batch_id: str) -> None:
    async with httpx.AsyncClient(
        timeout=10, trust_env=False, headers={"Authorization": "Bearer " + LOAD_KEY}
    ) as client:
        deadline = monotonic() + 240
        while monotonic() < deadline:
            response = await client.get(url + "/v1/batches/" + batch_id)
            assert response.status_code == 200, response.text
            result = response.json()
            if result["status"] in {"completed", "failed", "expired", "cancelled"}:
                break
            await asyncio.sleep(1)
        else:
            raise TimeoutError("accepted batch did not settle after rollout")
        assert result["status"] == "completed", result
        assert result["request_counts"]["completed"] == ITEMS, result
        assert result["request_counts"]["failed"] == 0, result
        output = await client.get(url + "/v1/files/" + result["output_file_id"] + "/content")
        assert output.status_code == 200
        rows = [json.loads(line) for line in output.text.splitlines()]
        assert len(rows) == len({row["custom_id"] for row in rows}) == ITEMS
        assert all(row["error"] is None for row in rows)
        (cluster.output / "batch-result.json").write_text(json.dumps(result, indent=2) + "\n")
    cluster.event("accepted_batch_completed_after_rollout", batch_id=batch_id, completed=ITEMS)
