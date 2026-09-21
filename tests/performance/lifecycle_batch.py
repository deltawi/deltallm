"""Exercise accepted batch work and shared artifacts across the real rollout."""

import asyncio
import json
from decimal import Decimal
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
    async with local_database() as db:
        deadline = monotonic() + 60
        while monotonic() < deadline:
            records = await db.query_raw(
                "SELECT o.completion_id,o.status,count(s.request_id)::int AS ledger_rows,"
                "COALESCE(sum(s.spend_exact),0)::text AS spend "
                "FROM deltallm_batch_completion_outbox o LEFT JOIN deltallm_spendlog_events s "
                "ON s.id=o.completion_id WHERE o.batch_id=$1 "
                "GROUP BY o.completion_id,o.status LIMIT 21",
                batch_id,
            )
            if len(records) == ITEMS and all(row["status"] == "sent" for row in records):
                break
            assert not any(row["status"] == "failed" for row in records), records
            await asyncio.sleep(0.2)
        else:
            raise TimeoutError("accepted batch accounting did not settle after rollout")
        assert all(row["ledger_rows"] == 1 and Decimal(row["spend"]) > 0 for row in records), (
            records
        )
        (cluster.output / "batch-accounting.json").write_text(json.dumps(records, indent=2) + "\n")
    cluster.event("accepted_batch_accounting_completed", batch_id=batch_id, ledger_rows=ITEMS)


async def batch_rollout(cluster: LifecycleCluster, url: str) -> None:
    # Keep this scenario independent of the earlier organization-wide ledger
    # outage. That outage can legitimately exhaust the batch outbox's finite
    # retry budget; this check proves termination of owned batch work instead.
    pods = json.loads(
        cluster.kubectl(
            "get",
            "pods",
            "-l",
            "app.kubernetes.io/instance=gateway,app.kubernetes.io/component=batch-worker",
            "-o",
            "json",
        ).stdout
    )["items"]
    active = [
        pod["metadata"]["name"] for pod in pods if not pod["metadata"].get("deletionTimestamp")
    ]
    assert len(active) == 1
    pod = active[0]
    with (
        cluster.forward("pod/" + pod, 4000) as port,
        cluster.forward("service/provider", 8000) as provider_port,
        cluster.follow_logs(pod),
    ):
        async with httpx.AsyncClient(timeout=3, trust_env=False) as client:
            provider = f"http://127.0.0.1:{provider_port}/fixture"
            response = await client.post(provider + "/batch-hold")
            response.raise_for_status()
            try:
                batch_id = await start_batch(cluster, url)
                await asyncio.to_thread(
                    cluster.kubectl,
                    "rollout",
                    "restart",
                    "deployment/gateway-deltallm-batch-worker",
                )
                deadline = monotonic() + 150
                while monotonic() < deadline:
                    response = await client.get(f"http://127.0.0.1:{port}/health/readiness")
                    if (
                        response.status_code == 503
                        and response.json()["details"]["process"]["state"] == "draining"
                    ):
                        break
                    await asyncio.sleep(0.1)
                else:
                    raise TimeoutError("batch worker drain was not observed")
                async with local_database() as db:
                    owned = await db.query_raw(
                        "SELECT item_id FROM deltallm_batch_item WHERE batch_id=$1 "
                        "AND status='in_progress' AND locked_by LIKE $2 LIMIT 20",
                        batch_id,
                        "batch-executor-" + pod + "-%",
                    )
                    assert owned, "batch worker had no active claims during drain"
                cluster.event(
                    "batch_worker_draining_with_active_claims", pod=pod, active=len(owned)
                )
            finally:
                response = await client.post(provider + "/batch-release")
                response.raise_for_status()
        await asyncio.to_thread(
            cluster.kubectl,
            "rollout",
            "status",
            "deployment/gateway-deltallm-batch-worker",
            "--timeout=180s",
        )
    await finish_batch(cluster, url, batch_id)
