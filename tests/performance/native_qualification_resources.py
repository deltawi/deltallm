"""Read fixed cgroup counters outside the qualification arrival window."""

from __future__ import annotations

import json
from time import perf_counter

from tests.performance.gateway_concurrency_diagnostics import _resource_role
from tests.performance.lifecycle_cluster import LifecycleCluster

CPU_FIELDS = frozenset(
    {"usage_usec", "user_usec", "system_usec", "nr_periods", "nr_throttled", "throttled_usec"}
)


def parse_cpu_stat(payload: str) -> dict[str, int]:
    if len(payload.encode()) > 4096:
        raise ValueError("CPU counter payload exceeds its byte limit")
    counters = {}
    for line in payload.splitlines():
        fields = line.split()
        if len(fields) != 2 or fields[0] not in CPU_FIELDS:
            continue
        if fields[0] in counters or not fields[1].isascii() or not fields[1].isdecimal():
            raise ValueError("Invalid CPU counter")
        value = int(fields[1])
        if value > 2**63 - 1:
            raise ValueError("CPU counter exceeds its integer limit")
        counters[fields[0]] = value
    if set(counters) != CPU_FIELDS:
        raise ValueError("CPU counter payload is incomplete")
    return counters


def pod_cpu_counters(cluster: LifecycleCluster, pod: str) -> dict[str, int]:
    return parse_cpu_stat(
        cluster.kubectl("exec", pod, "--", "cat", "/sys/fs/cgroup/cpu.stat", timeout=2).stdout
    )


def capture_cpu_counters(cluster: LifecycleCluster) -> dict[str, object]:
    started = perf_counter()
    document = cluster.kubectl("get", "pods", "-o", "json", timeout=2).stdout
    if len(document.encode()) > 1024 * 1024:
        raise ValueError("CPU source metadata exceeds its byte limit")
    pods = json.loads(document)["items"]
    if len(pods) > 32:
        raise ValueError("CPU source count exceeds its limit")
    sources = []
    for pod in sorted(pods, key=lambda value: value["metadata"]["name"]):
        metadata = pod["metadata"]
        role = _resource_role(metadata.get("labels", {}))
        if role is None or pod["status"]["phase"] != "Running":
            continue
        source = {"source_role": role, "source_identity": metadata["uid"]}
        try:
            source["counters"] = pod_cpu_counters(cluster, metadata["name"])
        except (RuntimeError, ValueError):
            source["error"] = "cpu_counters_unavailable"
        sources.append(source)
    return {"collection_seconds": perf_counter() - started, "sources": sources}


def cpu_counter_deltas(before: dict, after: dict) -> list[dict[str, object]]:
    previous = {source["source_identity"]: source for source in before["sources"]}
    result = []
    for source in after["sources"]:
        old = previous.get(source["source_identity"])
        record = {
            "source_role": source["source_role"],
            "source_identity": source["source_identity"],
        }
        if old is None or "counters" not in old or "counters" not in source:
            record["error"] = "cpu_counter_baseline_unavailable"
        else:
            delta = {name: source["counters"][name] - old["counters"][name] for name in CPU_FIELDS}
            if any(value < 0 for value in delta.values()):
                record["error"] = "cpu_counter_reset"
            else:
                record["counters"] = delta
        result.append(record)
    return result
