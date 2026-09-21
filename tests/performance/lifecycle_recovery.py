"""Dependency failure and Kubernetes readiness hysteresis in the owned fixture."""

import asyncio
import json
from time import monotonic

import httpx

from tests.performance.lifecycle_cluster import LifecycleCluster


async def readiness_recovery(cluster: LifecycleCluster, urls: list[str]) -> None:
    # Pause the existing server, preserving its data and connections. TCP probes
    # stay live; application PING/config reads encounter a real bounded timeout.
    cluster.kubectl(
        "exec", "deployment/redis", "--", "redis-cli", "CLIENT", "PAUSE", "22000", "ALL"
    )
    started = monotonic()
    samples = []
    saw_unready = saw_delayed_withdrawal = saw_delayed_recovery = False
    async with httpx.AsyncClient(timeout=3, trust_env=False) as client:
        while monotonic() - started < 70:
            health = await client.get(urls[0] + "/health/readiness")
            live = await client.get(urls[0] + "/health/liveliness")
            assert live.status_code == 200
            pods = json.loads(
                (
                    await asyncio.to_thread(
                        cluster.kubectl,
                        "get",
                        "pods",
                        "-l",
                        "app.kubernetes.io/instance=gateway,app.kubernetes.io/component=api",
                        "-o",
                        "json",
                    )
                ).stdout
            )
            active_pods = [
                pod for pod in pods["items"] if not pod["metadata"].get("deletionTimestamp")
            ]
            assert len(active_pods) == len(urls), "readiness requires the expected active API pods"
            ready = all(
                any(
                    condition["type"] == "Ready" and condition["status"] == "True"
                    for condition in pod["status"].get("conditions", [])
                )
                for pod in active_pods
            )
            samples.append(
                {
                    "seconds": monotonic() - started,
                    "readiness": health.status_code,
                    "liveness": live.status_code,
                    "pods_ready": ready,
                }
            )
            saw_delayed_withdrawal |= health.status_code == 503 and ready
            saw_unready |= not ready
            saw_delayed_recovery |= saw_unready and health.status_code == 200 and not ready
            if saw_unready and health.status_code == 200 and ready:
                break
            await asyncio.sleep(1)
        else:
            raise TimeoutError("Kubernetes readiness did not recover after Redis resumed")
    assert saw_delayed_withdrawal and saw_delayed_recovery, samples
    (cluster.output / "readiness-recovery.json").write_text(json.dumps(samples, indent=2) + "\n")
    cluster.event("dependency_and_probe_hysteresis_recovered")
