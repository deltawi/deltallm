"""Functional saturation scaling, missing metrics and stabilization evidence."""

import asyncio
from decimal import Decimal
import json
from pathlib import Path
from time import monotonic

import httpx

from tests.performance.capacity_fixture import capacity_release
from tests.performance.capacity_load import HeldStreams
from tests.performance.capacity_monitoring import custom_metrics
from tests.performance.capacity_samples import API_DEPLOYMENT, api_pods, sample, wait_pods
from tests.performance.lifecycle_cluster import LifecycleCluster
from tests.performance.lifecycle_cluster import LOAD_KEY


def metric_value(raw: str) -> float:
    return float(Decimal(raw[:-1]) / 1000 if raw.endswith("m") else Decimal(raw))


async def wait_metrics(cluster: LifecycleCluster, count: int, *, timeout: float = 120) -> dict:
    async with asyncio.timeout(timeout):
        while True:
            try:
                result = await asyncio.to_thread(custom_metrics, cluster)
            except RuntimeError:
                await asyncio.sleep(3)
                continue
            if len(result["items"]) == count:
                return result
            await asyncio.sleep(3)


async def exercise_scaling(cluster: LifecycleCluster, values: Path, url: str) -> None:
    await asyncio.to_thread(capacity_release, cluster, values, "--set", "autoscaling.enabled=true")
    # Empty fixture setup: start at the declared warm minimum before offered load.
    await asyncio.to_thread(
        cluster.kubectl, "scale", "deployment/" + API_DEPLOYMENT, "--replicas=2"
    )
    await wait_pods(cluster, 2)
    idle = await wait_metrics(cluster, 2)
    assert all(metric_value(item["value"]) == 0 for item in idle["items"])
    streams = HeldStreams(url, cluster.output / "held-streams.json")
    try:
        await streams.launch(30)
        async with asyncio.timeout(180):
            while len(await asyncio.to_thread(api_pods, cluster)) < 3:
                await asyncio.sleep(3)
        cluster.event("saturation_scaled_to_three", admitted=streams.active)
        await streams.launch(20)
        await wait_pods(cluster, 4)
        saturation = await wait_metrics(cluster, 4)
        assert sum(metric_value(item["value"]) for item in saturation["items"]) > 35
        await sample(cluster, "saturated-four")
        assert len(await asyncio.to_thread(api_pods, cluster, ready_only=False)) <= 4
        hpa = json.loads(
            (
                await asyncio.to_thread(cluster.kubectl, "get", "hpa", API_DEPLOYMENT, "-o", "json")
            ).stdout
        )
        (cluster.output / "hpa-saturated.json").write_text(json.dumps(hpa, indent=2) + "\n")
        current = hpa.get("status", {}).get("currentMetrics", [])
        assert any(
            metric["type"] == "Pods" and metric_value(metric["pods"]["current"]["averageValue"]) > 0
            for metric in current
        )
        cpu = next(
            metric["resource"]["current"]["averageUtilization"]
            for metric in current
            if metric["type"] == "Resource" and metric["resource"]["name"] == "cpu"
        )
        assert cpu < 65, "Scaling signal is confounded by CPU above its HPA target"
        cluster.event("saturation_scaled_to_four", admitted=streams.active, metrics=saturation)
        await overload_probe(cluster, url)
        scaled_at = monotonic()
    finally:
        await streams.close()
    await missing_metrics(cluster, scaled_at)
    async with asyncio.timeout(420):
        while len(await asyncio.to_thread(api_pods, cluster)) != 2:
            await asyncio.sleep(5)
    await sample(cluster, "scaled-down-two")
    hpa = json.loads(
        (
            await asyncio.to_thread(cluster.kubectl, "get", "hpa", API_DEPLOYMENT, "-o", "json")
        ).stdout
    )
    (cluster.output / "hpa-scaled-down.json").write_text(json.dumps(hpa, indent=2) + "\n")
    cluster.event("saturation_downscale_completed")


def assert_bounded_overload(results: list[dict]) -> tuple[int, int]:
    """Validate prompt edge shedding without timing admitted held streams."""
    rejected = [result for result in results if result.get("status") in {429, 503}]
    assert rejected
    assert max(result["seconds"] for result in rejected) < 2
    # Requests admitted before the edge gate fills remain deliberately held by
    # the streaming provider. Their client-side read timeout can exceed five
    # wall-clock seconds on a busy runner; it is not a local rejection latency.
    assert all(
        result.get("status") in {429, 503} or result.get("error") == "ReadTimeout"
        for result in results
    )
    return len(rejected), sum(result.get("error") == "ReadTimeout" for result in results)


async def overload_probe(cluster: LifecycleCluster, url: str) -> None:
    """Check a bounded edge burst and retain every response and elapsed time."""
    async with httpx.AsyncClient(timeout=5, trust_env=False) as client:

        async def request() -> dict:
            started = monotonic()
            try:
                response = await client.post(
                    url + "/v1/chat/completions",
                    headers={"Authorization": "Bearer " + LOAD_KEY},
                    json={
                        "model": "concurrency-fixture",
                        "stream": True,
                        "max_tokens": 1,
                        "messages": [{"role": "user", "content": "Reply with OK."}],
                    },
                )
                return {"status": response.status_code, "seconds": monotonic() - started}
            except httpx.HTTPError as error:
                return {"error": type(error).__name__, "seconds": monotonic() - started}

        results = await asyncio.gather(*(request() for _ in range(80)))
    (cluster.output / "overload-responses.json").write_text(json.dumps(results, indent=2) + "\n")
    rejected, admitted_timeouts = assert_bounded_overload(results)
    cluster.event(
        "bounded_overload_observed",
        rejected=rejected,
        admitted_timeouts=admitted_timeouts,
    )


async def missing_metrics(cluster: LifecycleCluster, scaled_at: float) -> None:
    pods = await asyncio.to_thread(api_pods, cluster)
    pod = pods[0]["metadata"]["name"]
    await asyncio.to_thread(
        cluster.kubectl, "annotate", "pod/" + pod, "capacity-test-drop-metrics=true"
    )
    async with asyncio.timeout(90):
        while True:
            metrics = await asyncio.to_thread(custom_metrics, cluster)
            names = {item["describedObject"]["name"] for item in metrics["items"]}
            if pod not in names:
                break
            await asyncio.sleep(3)
    assert len(await asyncio.to_thread(api_pods, cluster)) == 4
    cluster.event("missing_pod_metric_not_zero", pod=pod, remaining=len(names))
    await asyncio.to_thread(
        cluster.kubectl, "annotate", "pod/" + pod, "capacity-test-drop-metrics-"
    )
    await wait_metrics(cluster, 4)
    await asyncio.to_thread(cluster.kubectl, "scale", "deployment/capacity-adapter", "--replicas=0")
    started = monotonic()
    async with asyncio.timeout(90):
        while True:
            raw = await asyncio.to_thread(
                cluster.kubectl, "get", "hpa", API_DEPLOYMENT, "-o", "json"
            )
            hpa = json.loads(raw.stdout)
            conditions = hpa.get("status", {}).get("conditions", [])
            if any(c["type"] == "ScalingActive" and c["status"] == "False" for c in conditions):
                break
            await asyncio.sleep(3)
    assert len(await asyncio.to_thread(api_pods, cluster)) == 4
    cluster.event(
        "adapter_outage_visible",
        detection_seconds=monotonic() - started,
        conditions=conditions,
    )
    # Hold the adapter outage beyond the 300 s HPA scale-down window. A short
    # outage inside the window would be hidden by stabilization, not tested.
    while monotonic() - scaled_at < 330:
        assert len(await asyncio.to_thread(api_pods, cluster)) == 4
        await asyncio.sleep(10)
    hpa = json.loads(
        (
            await asyncio.to_thread(cluster.kubectl, "get", "hpa", API_DEPLOYMENT, "-o", "json")
        ).stdout
    )
    assert hpa["status"]["currentReplicas"] == 4
    (cluster.output / "hpa-metric-outage.json").write_text(json.dumps(hpa, indent=2) + "\n")
    assert any(
        c["type"] == "ScalingActive" and c["status"] == "False" for c in hpa["status"]["conditions"]
    )
    cluster.event(
        "adapter_outage_blocked_downscale_after_window",
        outage_seconds=monotonic() - scaled_at,
    )
    await asyncio.to_thread(cluster.kubectl, "scale", "deployment/capacity-adapter", "--replicas=1")
    await asyncio.to_thread(
        cluster.kubectl, "rollout", "status", "deployment/capacity-adapter", "--timeout=120s"
    )
    await wait_metrics(cluster, 4)
