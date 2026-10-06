"""Bounded PostgreSQL and Redis diagnostics for concurrency experiments."""

from __future__ import annotations

import asyncio
import json
import math
from pathlib import Path
import re
from time import perf_counter
from typing import TextIO

MAX_SNAPSHOTS = 256
MAX_EXPORT_BYTES = 16 * 1024 * 1024
SNAPSHOT_TIMEOUT_SECONDS = 3.0

POSTGRESQL_SNAPSHOT = """
WITH activity AS (
    SELECT
        count(*)::bigint AS connections,
        count(*) FILTER (WHERE state = 'active')::bigint AS active,
        count(*) FILTER (WHERE state = 'idle in transaction')::bigint
            AS idle_in_transaction,
        count(*) FILTER (WHERE wait_event_type = 'Lock')::bigint AS waiting_lock,
        count(*) FILTER (WHERE wait_event_type = 'Client')::bigint AS waiting_client,
        count(*) FILTER (WHERE wait_event_type = 'IO')::bigint AS waiting_io,
        count(*) FILTER (
            WHERE wait_event_type IS NOT NULL
              AND wait_event_type NOT IN ('Lock', 'Client', 'IO')
        )::bigint AS waiting_other
    FROM pg_stat_activity
    WHERE datname = current_database()
), locks AS (
    SELECT
        count(*) FILTER (WHERE granted)::bigint AS granted_locks,
        count(*) FILTER (WHERE NOT granted)::bigint AS waiting_locks
    FROM pg_locks
    WHERE database = (SELECT oid FROM pg_database WHERE datname = current_database())
), database_stats AS (
    SELECT
        xact_commit::bigint,
        xact_rollback::bigint,
        blks_read::bigint,
        blks_hit::bigint,
        temp_files::bigint,
        temp_bytes::bigint,
        deadlocks::bigint
    FROM pg_stat_database
    WHERE datname = current_database()
), background AS (
    SELECT
        COALESCE((to_jsonb(value)->>'checkpoints_timed')::bigint, 0) AS checkpoints_timed,
        COALESCE((to_jsonb(value)->>'checkpoints_req')::bigint, 0) AS checkpoints_requested,
        COALESCE((to_jsonb(value)->>'checkpoint_write_time')::double precision, 0)
            AS checkpoint_write_milliseconds,
        COALESCE((to_jsonb(value)->>'buffers_checkpoint')::bigint, 0)
            AS checkpoint_buffers,
        COALESCE((to_jsonb(value)->>'buffers_backend')::bigint, 0) AS backend_buffers
    FROM pg_stat_bgwriter AS value
), wal AS (
    SELECT
        wal_records::bigint,
        wal_fpi::bigint,
        wal_bytes::double precision
    FROM pg_stat_wal
), accounting AS (
    SELECT
        COALESCE(sum(calls) FILTER (
            WHERE query LIKE '%deltallm_accounting_allocate_local_permit_grants_batch%'
        ), 0)::bigint AS native_funding_calls,
        COALESCE(sum(calls) FILTER (
            WHERE query LIKE '%deltallm_accounting_append_terminal_journal%'
        ), 0)::bigint AS native_terminal_ack_calls,
        COALESCE(sum(calls) FILTER (
            WHERE query LIKE '%deltallm_accounting_materialize_terminal_journal%'
        ), 0)::bigint AS native_materialization_calls,
        COALESCE(sum(calls) FILTER (
            WHERE query LIKE '%deltallm_accounting_project_read_models%'
        ), 0)::bigint AS native_reporting_calls,
        COALESCE(sum(total_exec_time) FILTER (
            WHERE query LIKE '%deltallm_accounting_allocate_local_permit_grants_batch%'
               OR query LIKE '%deltallm_accounting_append_terminal_journal%'
               OR query LIKE '%deltallm_accounting_materialize_terminal_journal%'
               OR query LIKE '%deltallm_accounting_project_read_models%'
        ), 0)::double precision AS native_exec_milliseconds,
        COALESCE(sum(wal_bytes) FILTER (
            WHERE query LIKE '%deltallm_accounting_allocate_local_permit_grants_batch%'
               OR query LIKE '%deltallm_accounting_append_terminal_journal%'
               OR query LIKE '%deltallm_accounting_materialize_terminal_journal%'
               OR query LIKE '%deltallm_accounting_project_read_models%'
        ), 0)::double precision AS native_wal_bytes,
        COALESCE(sum(calls) FILTER (
            WHERE query LIKE '%deltallm_accounting_admit_grant_batch%'
               OR query LIKE '%deltallm_accounting_ensure_grants_batch%'
               OR query LIKE '%deltallm_accounting_reserve_grant_batch%'
               OR query LIKE '%deltallm_accounting_reserve_batch%'
        ), 0)::bigint AS reservation_calls,
        COALESCE(sum(calls) FILTER (
            WHERE query LIKE '%deltallm_accounting_finalize_grant_batch%'
               OR query LIKE '%deltallm_accounting_finalize_batch%'
        ), 0)::bigint AS finalization_calls,
        COALESCE(sum(total_exec_time) FILTER (
            WHERE query LIKE '%deltallm_accounting_admit_grant_batch%'
               OR query LIKE '%deltallm_accounting_ensure_grants_batch%'
               OR query LIKE '%deltallm_accounting_reserve_grant_batch%'
               OR query LIKE '%deltallm_accounting_reserve_batch%'
        ), 0)::double precision AS reservation_exec_milliseconds,
        COALESCE(sum(total_exec_time) FILTER (
            WHERE query LIKE '%deltallm_accounting_finalize_grant_batch%'
               OR query LIKE '%deltallm_accounting_finalize_batch%'
        ), 0)::double precision AS finalization_exec_milliseconds,
        COALESCE(sum(wal_records) FILTER (
            WHERE query LIKE '%deltallm_accounting_admit_grant_batch%'
               OR query LIKE '%deltallm_accounting_ensure_grants_batch%'
               OR query LIKE '%deltallm_accounting_reserve_grant_batch%'
               OR query LIKE '%deltallm_accounting_reserve_batch%'
               OR query LIKE '%deltallm_accounting_finalize_grant_batch%'
               OR query LIKE '%deltallm_accounting_finalize_batch%'
        ), 0)::bigint AS accounting_wal_records,
        COALESCE(sum(wal_bytes) FILTER (
            WHERE query LIKE '%deltallm_accounting_admit_grant_batch%'
               OR query LIKE '%deltallm_accounting_ensure_grants_batch%'
               OR query LIKE '%deltallm_accounting_reserve_grant_batch%'
               OR query LIKE '%deltallm_accounting_reserve_batch%'
               OR query LIKE '%deltallm_accounting_finalize_grant_batch%'
               OR query LIKE '%deltallm_accounting_finalize_batch%'
        ), 0)::double precision AS accounting_wal_bytes,
        COALESCE(sum(calls) FILTER (
            WHERE query LIKE '%deltallm_accounting_admit_grant_batch%'
               OR query LIKE '%deltallm_accounting_ensure_grants_batch%'
        ), 0)::bigint AS grant_assurance_calls,
        COALESCE(sum(calls) FILTER (
            WHERE query LIKE '%deltallm_accounting_projection_checkpoints%'
        ), 0)::bigint AS projection_checkpoint_calls,
        COALESCE(sum(calls) FILTER (
            WHERE query LIKE '%deltallm_accounting_reconcile_expired%'
               OR query LIKE '%deltallm_accounting_reconcile_expired_grants%'
               OR query LIKE '%deltallm_accounting_reconcile_grants%'
               OR query LIKE '%deltallm_accounting_roll_windows%'
        ), 0)::bigint AS accounting_maintenance_calls,
        COALESCE(sum(calls) FILTER (
            WHERE query LIKE '%deltallm_spend_ingestion_outbox%'
               OR query LIKE '%deltallm_spendlog_events%'
        ), 0)::bigint AS legacy_spend_calls,
        COALESCE(sum(calls) FILTER (
            WHERE query LIKE '%deltallm_audit_ingestion_outbox%'
               OR query LIKE '%deltallm_auditevent%'
        ), 0)::bigint AS legacy_audit_calls
    FROM pg_stat_statements
    WHERE dbid = (SELECT oid FROM pg_database WHERE datname = current_database())
      AND query NOT LIKE '%FROM pg_stat_statements%'
)
SELECT *
FROM activity, locks, database_stats, background, wal, accounting
"""

POSTGRESQL_FIELDS = {
    "connections",
    "active",
    "idle_in_transaction",
    "waiting_lock",
    "waiting_client",
    "waiting_io",
    "waiting_other",
    "granted_locks",
    "waiting_locks",
    "xact_commit",
    "xact_rollback",
    "blks_read",
    "blks_hit",
    "temp_files",
    "temp_bytes",
    "deadlocks",
    "checkpoints_timed",
    "checkpoints_requested",
    "checkpoint_write_milliseconds",
    "checkpoint_buffers",
    "backend_buffers",
    "wal_records",
    "wal_fpi",
    "wal_bytes",
    "reservation_calls",
    "finalization_calls",
    "reservation_exec_milliseconds",
    "finalization_exec_milliseconds",
    "accounting_wal_records",
    "accounting_wal_bytes",
    "grant_assurance_calls",
    "projection_checkpoint_calls",
    "accounting_maintenance_calls",
    "legacy_spend_calls",
    "legacy_audit_calls",
    "native_funding_calls",
    "native_terminal_ack_calls",
    "native_materialization_calls",
    "native_reporting_calls",
    "native_exec_milliseconds",
    "native_wal_bytes",
}

REDIS_FIELDS = {
    "connected_clients",
    "blocked_clients",
    "used_memory",
    "used_memory_rss",
    "total_connections_received",
    "total_commands_processed",
    "instantaneous_ops_per_sec",
    "rejected_connections",
    "expired_keys",
    "evicted_keys",
    "keyspace_hits",
    "keyspace_misses",
}

RESOURCE_ROLES = {
    "api",
    "accounting_worker",
    "accounting_request",
    "load_generator",
    "provider",
    "postgresql",
    "redis",
}
_QUANTITY = re.compile(r"^(?P<number>[0-9]+(?:\.[0-9]+)?)(?P<suffix>n|u|m|Ki|Mi|Gi|Ti)?$")


def _bounded_numbers(values: dict[str, object], allowlist: set[str]) -> dict[str, float | int]:
    result: dict[str, float | int] = {}
    for key in sorted(allowlist):
        value = values.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        if math.isfinite(value):
            result[key] = value
    return result


class DependencyDiagnosticsRecorder:
    def __init__(
        self, database: object, redis: object, output: Path, *, interval: float = 5
    ) -> None:
        if not 1 <= interval <= 60:
            raise ValueError("diagnostic interval must be between one and sixty seconds")
        self.database = database
        self.redis = redis
        self.output = output
        self.interval = interval
        self.started = 0.0
        self.snapshots = 0
        self.errors = 0
        self._bytes_written = 0
        self._file: TextIO | None = None
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    async def __aenter__(self) -> DependencyDiagnosticsRecorder:
        self.output.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.output.open("x", encoding="utf-8")
        try:
            self.started = perf_counter()
            await self.snapshot()
            self._task = asyncio.create_task(self._run(), name="dependency-diagnostics")
        except BaseException:
            self._file.close()
            raise
        return self

    async def _collect(self) -> dict[str, object]:
        async with asyncio.timeout(SNAPSHOT_TIMEOUT_SECONDS):
            rows = await self.database.query_raw(POSTGRESQL_SNAPSHOT)
            redis = await self.redis.info()
        if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
            raise ValueError("invalid PostgreSQL diagnostic result")
        if not isinstance(redis, dict):
            raise ValueError("invalid Redis diagnostic result")
        return {
            "postgresql": _bounded_numbers(rows[0], POSTGRESQL_FIELDS),
            "redis": _bounded_numbers(redis, REDIS_FIELDS),
        }

    async def snapshot(self) -> None:
        assert self._file is not None
        if self.snapshots >= MAX_SNAPSHOTS:
            raise ValueError("dependency diagnostic snapshot budget exceeded")
        offset = perf_counter() - self.started
        try:
            values = await self._collect()
            record = {"offset_seconds": offset, **values}
        # This is the artifact trust boundary: client/driver/database errors are
        # intentionally collapsed so server text and query values never enter results.
        except Exception:
            self.errors += 1
            record = {"offset_seconds": offset, "error": "snapshot_failed"}
        line = json.dumps(record, sort_keys=True) + "\n"
        self._bytes_written += len(line.encode("utf-8"))
        if self._bytes_written > MAX_EXPORT_BYTES:
            raise ValueError("dependency diagnostic export byte budget exceeded")
        self._file.write(line)
        self._file.flush()
        self.snapshots += 1

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval)
            except TimeoutError:
                await self.snapshot()

    async def __aexit__(self, *exc: object) -> None:
        self._stop.set()
        try:
            if self._task is not None:
                await self._task
            await self.snapshot()
        finally:
            if self._file is not None:
                self._file.close()


def _quantity(value: str, *, cpu: bool) -> float:
    match = _QUANTITY.fullmatch(value)
    if match is None:
        raise ValueError("unsupported Kubernetes resource quantity")
    number = float(match.group("number"))
    suffix = match.group("suffix") or ""
    factors = (
        {"": 1000.0, "n": 0.000001, "u": 0.001, "m": 1.0}
        if cpu
        else {"": 1.0, "Ki": 1024.0, "Mi": 1024.0**2, "Gi": 1024.0**3, "Ti": 1024.0**4}
    )
    if suffix not in factors:
        raise ValueError("resource quantity uses the wrong unit")
    result = number * factors[suffix]
    if not math.isfinite(result) or result < 0:
        raise ValueError("invalid Kubernetes resource quantity")
    return result


def _resource_role(labels: dict[str, object]) -> str | None:
    component = labels.get("app.kubernetes.io/component")
    name = labels.get("app.kubernetes.io/name")
    fixture = labels.get("fixture")
    if component == "api":
        return "api"
    if component == "accounting-worker":
        return "accounting_worker"
    if component == "accounting-request":
        return "accounting_request"
    if component == "load-generator":
        return "load_generator"
    if fixture == "provider":
        return "provider"
    if fixture == "postgres" or name == "postgresql":
        return "postgresql"
    if fixture == "redis" or name == "redis":
        return "redis"
    return None


class KubernetesResourceRecorder:
    """Continuously record bounded pod CPU/memory from an owned test cluster."""

    def __init__(
        self,
        cluster: object,
        output: Path,
        *,
        required_roles: set[str],
        interval: float = 5,
    ) -> None:
        if not required_roles or not required_roles <= RESOURCE_ROLES:
            raise ValueError("resource roles must use the bounded qualification allowlist")
        if not 1 <= interval <= 60:
            raise ValueError("resource interval must be between one and sixty seconds")
        self.cluster = cluster
        self.output = output
        self.required_roles = required_roles
        self.interval = interval
        self.started = 0.0
        self.snapshots = 0
        self.errors = 0
        self.observed_roles: set[str] = set()
        self._bytes_written = 0
        self._file: TextIO | None = None
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    async def __aenter__(self) -> KubernetesResourceRecorder:
        self.output.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.output.open("x", encoding="utf-8")
        try:
            self.started = perf_counter()
            await self.snapshot()
            self._task = asyncio.create_task(self._run(), name="kubernetes-resources")
        except BaseException:
            self._file.close()
            raise
        return self

    def _collect_sync(self) -> list[dict[str, object]]:
        pods_result = self.cluster.kubectl("get", "pods", "-o", "json", timeout=2)
        usage_result = self.cluster.kubectl(
            "get", "--raw", "/apis/metrics.k8s.io/v1beta1/namespaces/lifecycle/pods", timeout=2
        )
        pods = json.loads(pods_result.stdout)
        usage = json.loads(usage_result.stdout)
        roles: dict[str, str] = {}
        for pod in pods.get("items", []):
            metadata = pod.get("metadata", {})
            name = metadata.get("name")
            labels = metadata.get("labels", {})
            if isinstance(name, str) and isinstance(labels, dict):
                role = _resource_role(labels)
                if role is not None:
                    roles[name] = role
        by_role: dict[str, list[tuple[str, float, float]]] = {}
        for pod in usage.get("items", []):
            metadata = pod.get("metadata", {})
            name = metadata.get("name")
            if not isinstance(name, str) or name not in roles:
                continue
            cpu = memory = 0.0
            containers = pod.get("containers", [])
            if not isinstance(containers, list) or len(containers) > 8:
                raise ValueError("invalid resource container list")
            for container in containers:
                values = container.get("usage", {})
                cpu += _quantity(values.get("cpu", ""), cpu=True)
                memory += _quantity(values.get("memory", ""), cpu=False)
            by_role.setdefault(roles[name], []).append((name, cpu, memory))
        result: list[dict[str, object]] = []
        for role, values in sorted(by_role.items()):
            for process, (_, cpu, memory) in enumerate(sorted(values)):
                result.append(
                    {
                        "source_role": role,
                        "source_process": process,
                        "cpu_millicores": cpu,
                        "memory_bytes": memory,
                    }
                )
        return result

    async def snapshot(self) -> None:
        assert self._file is not None
        if self.snapshots >= MAX_SNAPSHOTS:
            raise ValueError("resource snapshot budget exceeded")
        offset = perf_counter() - self.started
        try:
            async with asyncio.timeout(SNAPSHOT_TIMEOUT_SECONDS):
                sources = await asyncio.to_thread(self._collect_sync)
            self.observed_roles.update(source["source_role"] for source in sources)
            record: dict[str, object] = {"offset_seconds": offset, "sources": sources}
        except Exception:
            self.errors += 1
            record = {"offset_seconds": offset, "error": "snapshot_failed"}
        line = json.dumps(record, sort_keys=True) + "\n"
        self._bytes_written += len(line.encode("utf-8"))
        if self._bytes_written > MAX_EXPORT_BYTES:
            raise ValueError("resource export byte budget exceeded")
        self._file.write(line)
        self._file.flush()
        self.snapshots += 1

    def evidence(self) -> dict[str, object]:
        return {
            "snapshots": self.snapshots,
            "errors": self.errors,
            "observed_roles": sorted(self.observed_roles),
            "missing_required_roles": sorted(self.required_roles - self.observed_roles),
        }

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval)
            except TimeoutError:
                await self.snapshot()

    async def __aexit__(self, *exc: object) -> None:
        self._stop.set()
        try:
            if self._task is not None:
                await self._task
            await self.snapshot()
        finally:
            if self._file is not None:
                self._file.close()
