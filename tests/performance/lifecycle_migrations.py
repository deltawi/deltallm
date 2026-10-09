"""Use the rendered release Job for concurrent deployment and failure evidence."""

import asyncio
import copy
import json
from pathlib import Path
from time import monotonic

import yaml

from src.db.migration_status import verify_migration_status
from tests.performance.gateway_concurrency_dependencies import local_database
from tests.performance.lifecycle_cluster import LifecycleCluster
from tests.performance.lifecycle_fixtures import CHART, release


async def concurrent_migrations(cluster: LifecycleCluster, values: Path) -> None:
    rendered = cluster.helm(
        "template",
        "gateway",
        str(CHART),
        "-f",
        str(CHART / "values-production.yaml"),
        "-f",
        str(values),
    ).stdout
    template = next(doc for doc in yaml.safe_load_all(rendered) if doc and doc["kind"] == "Job")
    jobs = []
    for suffix in ("a", "b"):
        job = copy.deepcopy(template)
        job["metadata"] = {"name": "concurrent-migration-" + suffix}
        jobs.append(job)
    cluster.apply(jobs)
    deadline = monotonic() + 120
    while monotonic() < deadline:
        payload = json.loads(
            cluster.kubectl(
                "get", "job", "concurrent-migration-a", "concurrent-migration-b", "-o", "json"
            ).stdout
        )
        if all(
            job["status"].get("succeeded") or job["status"].get("failed")
            for job in payload["items"]
        ):
            break
        await asyncio.sleep(1)
    else:
        raise TimeoutError("concurrent migration jobs did not finish")
    assert any(job["status"].get("succeeded") for job in payload["items"])
    # A lock loser may fail safely; a subsequent release must be idempotent.
    await asyncio.to_thread(release, cluster, values)
    async with local_database() as db:
        await verify_migration_status(db, timeout_seconds=2)
    (cluster.output / "concurrent-migrations.json").write_text(json.dumps(payload, indent=2) + "\n")
    for suffix in ("a", "b"):
        cluster.kubectl("logs", "job/concurrent-migration-" + suffix)
    cluster.event("concurrent_and_retried_migrations_verified")


async def failed_migrations(cluster: LifecycleCluster, values: Path) -> None:
    cluster.apply(
        [
            {
                "apiVersion": "v1",
                "kind": "Secret",
                "metadata": {"name": "unavailable-database"},
                "stringData": {
                    "database-url": "postgresql://postgres:fixture-only@postgres:1/deltallm_concurrency"
                },
            }
        ]
    )
    for reason, overrides in (
        (
            "invalid_schema",
            ["--set", "migrationJob.args[0]=--schema,migrationJob.args[1]=/missing/schema.prisma"],
        ),
        (
            "wall_time",
            [
                "--set",
                "runtime.database.existingSecret.name=unavailable-database",
                "--set",
                "migrationJob.timeoutSeconds=2",
            ],
        ),
    ):
        before = json.loads(
            cluster.kubectl(
                "get", "deployments", "-l", "app.kubernetes.io/instance=gateway", "-o", "json"
            ).stdout
        )
        failed = await asyncio.to_thread(release, cluster, values, *overrides, check=False)
        assert failed.returncode != 0, reason
        after = json.loads(
            cluster.kubectl(
                "get", "deployments", "-l", "app.kubernetes.io/instance=gateway", "-o", "json"
            ).stdout
        )

        def snapshots(payload):
            return {item["metadata"]["name"]: item["spec"]["template"] for item in payload["items"]}

        assert snapshots(before) == snapshots(after), reason
        cluster.kubectl(
            "logs", "-l", "app.kubernetes.io/name=deltallm-migration", "--tail=100", check=False
        )
        cluster.event("failed_migration_blocked_rollout", reason=reason)
