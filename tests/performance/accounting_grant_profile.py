"""Measure direct, assigned, and pre-issued accounting on one hot budget.

This probe requires a disposable, fully migrated database with no active accounting
generation. It exercises the production repository and microbatch service; it does
not include HTTP, Redis, provider latency, or compatibility projection.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import hashlib
import json
import math
from multiprocessing import get_context
from pathlib import Path
import subprocess
import sys
from time import perf_counter, time
from uuid import UUID, uuid4

from prisma import Prisma

from scripts.measure_gateway_load import (
    RequestResult,
    RunResult,
    run_constant_arrival,
    summarize,
    write_results,
)
from src.billing.accounting.accounting_protocol import (
    AccountingAttribution,
    AccountingFinalization,
    AccountingOutcome,
    AccountingReservation,
    AccountingScope,
    BudgetWindowRef,
    ReserveDecision,
    request_fingerprint,
)
from src.billing.accounting.accounting_service import AccountingProtocolService
from src.billing.accounting.permits.preissued_permits import PreissuedPermitBank
from src.config import DatabaseConnectionSettings
from src.db.runtime.accounting_pool import AccountingPostgresManager
from src.db.accounting.accounting_protocol import AccountingProtocolRepository
from src.db.accounting.accounting_calls import AccountingQueryClient
from tests.accounting_adapters.permit_repository import AccountingPermitRepository
from tests.performance.gateway_concurrency_dependencies import fixture_database_url

_PROFILE_TABLES = (
    "deltallm_billing_operations",
    "deltallm_accounting_events",
    "deltallm_accounting_reservations",
    "deltallm_accounting_grant_windows",
    "deltallm_accounting_grants",
    "deltallm_accounting_budget_windows",
    "deltallm_accounting_partitions",
    "deltallm_accounting_protocols",
)


@dataclass(frozen=True, slots=True)
class _WorkerSpec:
    database_url: str
    mode: str
    generation: int
    window_id: str
    index: int
    start_at_epoch: float
    rate: float
    duration: float
    batch_size: int
    dwell_ms: float
    max_in_flight: int
    statement_timeout: float
    finalization_timeout: float
    grant_operations: int
    grant_ttl: int


def _percentiles(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"mean": None, "p50": None, "p95": None, "p99": None, "max": None}
    ordered = sorted(values)

    def value(percentile: float) -> float:
        return ordered[max(0, math.ceil(len(ordered) * percentile) - 1)]

    return {
        "mean": sum(ordered) / len(ordered),
        "p50": value(0.50),
        "p95": value(0.95),
        "p99": value(0.99),
        "max": ordered[-1],
    }


def _source_manifest() -> dict[str, object]:
    paths = (
        Path("src/billing/accounting/accounting_service.py"),
        Path("src/billing/accounting/durable_microbatch.py"),
        Path("src/db/accounting/accounting_protocol.py"),
        Path("src/db/runtime/accounting_pool.py"),
        Path("src/db/accounting/accounting_calls.py"),
        Path("tests/accounting_adapters/permit_repository.py"),
        Path("src/billing/accounting/accounting_protocol.py"),
        Path("src/billing/accounting/permits/preissued_permits.py"),
        Path("prisma/migrations/20260926120000_accounting_protocol_v2/migration.sql"),
        Path("prisma/migrations/20260926180000_accounting_budget_grants/migration.sql"),
        Path("prisma/migrations/20260927150000_accounting_atomic_grant_admission/migration.sql"),
        Path("prisma/migrations/20260929100000_accounting_preissued_permits/migration.sql"),
        Path("prisma/migrations/20261004130000_accounting_permit_batches/migration.sql"),
        Path("prisma/migrations/20261004140000_accounting_zero_allowance_permits/migration.sql"),
        Path("prisma/migrations/20261004150000_accounting_allocator_scope_bounds/migration.sql"),
        Path("prisma/migrations/20261004160000_accounting_operation_key_probes/migration.sql"),
        Path("prisma/migrations/20261004170000_accounting_permit_key_probes/migration.sql"),
        Path("prisma/migrations/20261004180000_accounting_claim_key_probes/migration.sql"),
        Path("tests/performance/accounting_grant_profile.py"),
    )
    return {
        "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "python": sys.version.split()[0],
        "working_tree_dirty": bool(
            subprocess.check_output(["git", "status", "--porcelain"], text=True).strip()
        ),
        "sha256": {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths},
    }


async def _prepare(db: Prisma, *, partitions: int) -> tuple[int, str]:
    active = await db.query_raw(
        "SELECT generation FROM deltallm_accounting_protocols "
        "WHERE protocol_name='primary' AND state='active'"
    )
    if active:
        raise RuntimeError(
            "accounting profile requires a disposable database with no active generation"
        )
    generation = int(uuid4().int % 1_000_000_000) + 1
    window_id = str(uuid4())
    await db.execute_raw(
        "INSERT INTO deltallm_accounting_protocols "
        "(protocol_name,generation,writer_version,state,partition_count,"
        "max_outstanding_per_partition) VALUES ('primary',$1,2,'prepared',$2,100000)",
        generation,
        partitions,
    )
    await db.execute_raw(
        "INSERT INTO deltallm_accounting_partitions "
        "(protocol_name,generation,partition_id,max_outstanding) "
        "SELECT 'primary',$1,value,100000 FROM generate_series(0,$2::integer-1) value",
        generation,
        partitions,
    )
    await db.execute_raw(
        "INSERT INTO deltallm_accounting_budget_windows "
        "(window_id,protocol_name,generation,scope_type,scope_id,period_key,"
        "policy_generation,limit_exact,window_starts_at,window_ends_at) "
        "VALUES ($1,'primary',$2,'organization','profile-hot-org',$3,1,"
        "1000000000::numeric,NOW()-INTERVAL '1 minute',NOW()+INTERVAL '1 hour')",
        window_id,
        generation,
        "profile-" + window_id,
    )
    await db.execute_raw("SELECT deltallm_activate_accounting_protocol($1)", generation)
    return generation, window_id


async def _vacuum_profile_tables(db: Prisma) -> None:
    # Repeated disposable profiles delete tens of thousands of wide rows. Make
    # the next run independent of that benchmark-only churn instead of waiting
    # for autovacuum to happen to run between qualifications.
    for table in _PROFILE_TABLES:
        await db.execute_raw(f"VACUUM (ANALYZE) {table}")


async def _cleanup(db: Prisma, generation: int) -> None:
    await db.execute_raw("DELETE FROM deltallm_accounting_events WHERE generation=$1", generation)
    await db.execute_raw(
        "DELETE FROM deltallm_accounting_reservations WHERE operation_id IN "
        "(SELECT operation_id FROM deltallm_billing_operations "
        "WHERE accounting_generation=$1)",
        generation,
    )
    await db.execute_raw(
        "DELETE FROM deltallm_billing_operations WHERE accounting_generation=$1", generation
    )
    await db.execute_raw(
        "DELETE FROM deltallm_accounting_grant_windows WHERE grant_id IN "
        "(SELECT grant_id FROM deltallm_accounting_grants WHERE generation=$1)",
        generation,
    )
    await db.execute_raw("DELETE FROM deltallm_accounting_grants WHERE generation=$1", generation)
    await db.execute_raw(
        "DELETE FROM deltallm_accounting_budget_windows WHERE generation=$1", generation
    )
    await db.execute_raw(
        "DELETE FROM deltallm_accounting_partitions WHERE generation=$1", generation
    )
    await db.execute_raw(
        "DELETE FROM deltallm_accounting_protocols WHERE generation=$1", generation
    )
    await _vacuum_profile_tables(db)


def _reservation(generation: int, window_id: str) -> AccountingReservation:
    operation_id = uuid4()
    return AccountingReservation(
        protocol_generation=generation,
        operation_id=operation_id,
        owner_token=uuid4(),
        request_fingerprint=request_fingerprint(
            operation_kind="profile", payload={"operation_id": operation_id}
        ),
        attribution=AccountingAttribution(
            api_key="profile-key",
            user_id="profile-user",
            team_id="profile-team",
            organization_id="profile-hot-org",
            model="profile-model",
            deployment_id="profile-deployment",
            provider="profile-provider",
            call_type="chat",
        ),
        allowance=Decimal("1"),
        windows=(
            BudgetWindowRef(
                window_id=UUID(window_id),
                scope_type=AccountingScope.ORGANIZATION,
                scope_id="profile-hot-org",
                policy_generation=1,
            ),
        ),
        pricing_snapshot={"version": "profile-v1"},
        audit_envelope={"action": "accounting.profile.reserve", "redacted": True},
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )


def _finalization(item: AccountingReservation) -> AccountingFinalization:
    return AccountingFinalization(
        protocol_generation=item.protocol_generation,
        operation_id=item.operation_id,
        owner_token=item.owner_token,
        request_fingerprint=item.request_fingerprint,
        component_id="profile-provider-attempt",
        event_id=uuid4(),
        outcome=AccountingOutcome.COMPLETED,
        exact_charge=Decimal("0.6"),
        spend_payload={"request_id": str(item.operation_id), "cost_exact": "0.6"},
        audit_envelope={"action": "accounting.profile.finalize", "redacted": True},
        occurred_at=datetime.now(UTC),
    )


async def _close_grants(db: Prisma, generation: int) -> int:
    await db.execute_raw(
        "UPDATE deltallm_accounting_grants SET expires_at=NOW()-INTERVAL '1 second' "
        "WHERE generation=$1 AND state='active'",
        generation,
    )
    total = 0
    while True:
        rows = await db.query_raw(
            "SELECT deltallm_accounting_reconcile_grants($1,256) AS count", generation
        )
        count = int(rows[0]["count"])
        total += count
        if count == 0:
            return total


async def _run_profile_worker(spec: _WorkerSpec) -> dict[str, object]:
    manager = AccountingPostgresManager()
    reserve_seconds: list[float] = []
    finalize_seconds: list[float] = []
    reserve_db_seconds: list[float] = []
    finalize_db_seconds: list[float] = []
    reserve_batch_sizes: list[float] = []
    finalize_batch_sizes: list[float] = []
    database_calls: Counter[str] = Counter()
    decisions: Counter[str] = Counter()
    service: AccountingProtocolService | None = None
    permit_bank: PreissuedPermitBank | None = None
    try:
        await manager.connect(
            DatabaseConnectionSettings(url=spec.database_url, pool_size=3, pool_timeout=1),
            pool_size=2,
            acquisition_seconds=0.2,
            statement_seconds=spec.statement_timeout,
            lock_seconds=min(0.2, spec.statement_timeout),
        )
        if manager.client is None:
            raise RuntimeError("profile accounting pool did not start")
        repository_type = (
            _ObservedPermitRepository if spec.mode == "permits" else _ObservedAccountingRepository
        )
        repository = repository_type(
            manager.client,
            reserve_seconds=reserve_db_seconds,
            finalize_seconds=finalize_db_seconds,
            reserve_batch_sizes=reserve_batch_sizes,
            finalize_batch_sizes=finalize_batch_sizes,
            database_calls=database_calls,
            statement_budget_seconds=spec.statement_timeout,
            grants_enabled=spec.mode in {"grants", "permits"},
            grantee_id=f"accounting-profile-{spec.index}",
            grant_target_operations=spec.grant_operations,
            grant_ttl_seconds=spec.grant_ttl,
        )
        if isinstance(repository, _ObservedPermitRepository):
            permit_bank = repository.permit_bank
        worker_pending = max(spec.batch_size, spec.max_in_flight)
        service = AccountingProtocolService(
            repository,
            generation=spec.generation,
            max_batch_size=spec.batch_size,
            dwell_seconds=spec.dwell_ms / 1000,
            max_pending_reservations=worker_pending,
            max_pending_finalizations=worker_pending,
            statement_budget_seconds=spec.statement_timeout,
            finalization_ack_budget_seconds=spec.finalization_timeout,
        )
        service.start()

        delay = spec.start_at_epoch - time()
        if delay > 0:
            await asyncio.sleep(delay)

        async def request(_index: int, _request_id: str) -> RequestResult:
            item = _reservation(spec.generation, spec.window_id)
            started = perf_counter()
            permit = await service.reserve(item)
            reserve_seconds.append(perf_counter() - started)
            decisions[permit.decision.value] += 1
            if permit.decision is not ReserveDecision.DISPATCH:
                return RequestResult(status_code=429, error=permit.decision.value)
            started = perf_counter()
            await service.finalize(_finalization(item))
            finalize_seconds.append(perf_counter() - started)
            return RequestResult(status_code=200)

        run = await run_constant_arrival(
            rate=spec.rate,
            duration_seconds=spec.duration,
            max_in_flight=spec.max_in_flight,
            request=request,
        )
        await service.close(timeout_seconds=spec.finalization_timeout * 2)
        service = None
        return {
            "run": run,
            "reserve_seconds": reserve_seconds,
            "finalize_seconds": finalize_seconds,
            "reserve_db_seconds": reserve_db_seconds,
            "finalize_db_seconds": finalize_db_seconds,
            "reserve_batch_sizes": reserve_batch_sizes,
            "finalize_batch_sizes": finalize_batch_sizes,
            "database_calls": dict(database_calls),
            "decisions": dict(decisions),
        }
    finally:
        if service is not None:
            await service.close(timeout_seconds=spec.finalization_timeout * 2)
        if permit_bank is not None:
            await permit_bank.close()
        await manager.disconnect()


def _profile_worker(spec: _WorkerSpec) -> dict[str, object]:
    return asyncio.run(_run_profile_worker(spec))


def _combine_worker_runs(results: list[dict[str, object]], duration: float) -> RunResult:
    runs = [result["run"] for result in results]
    if not all(isinstance(run, RunResult) for run in runs):
        raise TypeError("accounting profile worker returned an invalid run")
    combined_samples = []
    offset = 0
    for run in runs:
        combined_samples.extend(
            replace(sample, index=offset + sample.index) for sample in run.samples
        )
        offset += run.target_count
    return RunResult(
        run_id=uuid4().hex,
        started_at=min(run.started_at for run in runs),
        target_count=sum(run.target_count for run in runs),
        scheduled_count=sum(run.scheduled_count for run in runs),
        generator_dropped_count=sum(run.generator_dropped_count for run in runs),
        arrival_window_seconds=duration,
        drain_window_seconds=max(run.drain_window_seconds for run in runs),
        max_in_flight_observed=sum(run.max_in_flight_observed for run in runs),
        samples=tuple(sorted(combined_samples, key=lambda sample: sample.index)),
    )


async def _measure_mode(args: argparse.Namespace, mode: str, url: str) -> dict[str, object]:
    observer = Prisma(datasource={"url": url})
    await observer.connect()
    generation = 0
    try:
        await _vacuum_profile_tables(observer)
        generation, window_id = await _prepare(observer, partitions=args.partitions)
        start_at_epoch = time() + 5.0
        worker_rate = args.rate / args.processes
        worker_in_flight = max(1, math.ceil(args.max_in_flight / args.processes))
        specs = [
            _WorkerSpec(
                database_url=url,
                mode=mode,
                generation=generation,
                window_id=window_id,
                index=index,
                start_at_epoch=start_at_epoch,
                rate=worker_rate,
                duration=args.duration,
                batch_size=args.batch_size,
                dwell_ms=args.dwell_ms,
                max_in_flight=worker_in_flight,
                statement_timeout=args.statement_timeout,
                finalization_timeout=args.finalization_timeout,
                grant_operations=args.grant_operations,
                grant_ttl=args.grant_ttl,
            )
            for index in range(args.processes)
        ]
        loop = asyncio.get_running_loop()
        with ProcessPoolExecutor(
            max_workers=args.processes,
            mp_context=get_context("spawn"),
        ) as executor:
            results = await asyncio.gather(
                *(loop.run_in_executor(executor, _profile_worker, spec) for spec in specs)
            )
        run = _combine_worker_runs(results, args.duration)
        reserve_seconds = [value for result in results for value in result["reserve_seconds"]]
        finalize_seconds = [value for result in results for value in result["finalize_seconds"]]
        reserve_db_seconds = [value for result in results for value in result["reserve_db_seconds"]]
        finalize_db_seconds = [
            value for result in results for value in result["finalize_db_seconds"]
        ]
        reserve_batch_sizes = [
            value for result in results for value in result["reserve_batch_sizes"]
        ]
        finalize_batch_sizes = [
            value for result in results for value in result["finalize_batch_sizes"]
        ]
        decisions: Counter[str] = Counter()
        database_calls: Counter[str] = Counter()
        for result in results:
            decisions.update(result["decisions"])
            database_calls.update(result["database_calls"])
        reconciled_grants = (
            await _close_grants(observer, generation) if mode in {"grants", "permits"} else 0
        )
        window_rows = await observer.query_raw(
            "SELECT committed_exact::text AS committed,reserved_exact::text AS reserved,"
            "provisional_exact::text AS provisional FROM deltallm_accounting_budget_windows "
            "WHERE window_id=$1",
            window_id,
        )
        grant_rows = await observer.query_raw(
            "SELECT state,count(*)::integer AS count FROM deltallm_accounting_grants "
            "WHERE generation=$1 GROUP BY state ORDER BY state",
            generation,
        )
        report = summarize(run, target_rate=args.rate)
        report.update(
            {
                "scope": "accounting database path only; no HTTP, Redis, provider, or projection",
                "database_driver": "asyncpg",
                "database_connections_per_process": 2,
                "mode": mode,
                "processes": args.processes,
                "partitions": args.partitions,
                "batch_size": args.batch_size,
                "dwell_ms": args.dwell_ms,
                "grant_target_operations": args.grant_operations
                if mode in {"grants", "permits"}
                else None,
                "grant_ttl_seconds": args.grant_ttl if mode in {"grants", "permits"} else None,
                "reservation_decisions": dict(decisions),
                "database_call_counts": dict(database_calls),
                "reservation_latency_seconds": _percentiles(reserve_seconds),
                "finalization_latency_seconds": _percentiles(finalize_seconds),
                "reservation_database_sequence_seconds": _percentiles(reserve_db_seconds),
                "finalization_database_call_seconds": _percentiles(finalize_db_seconds),
                "reservation_batch_size": _percentiles(reserve_batch_sizes),
                "finalization_batch_size": _percentiles(finalize_batch_sizes),
                "error_counts": dict(
                    Counter(sample.error for sample in run.samples if sample.error)
                ),
                "window": window_rows[0],
                "grants_by_state": grant_rows,
                "reconciled_grants": reconciled_grants,
            }
        )
        expected = Decimal("0.6") * report["success_count"]
        report["economic_invariants_passed"] = (
            Decimal(window_rows[0]["committed"]) == expected
            and Decimal(window_rows[0]["reserved"]) == 0
            and Decimal(window_rows[0]["provisional"]) == 0
        )
        report["qualification_passed"] = (
            report["success_count"] == report["target_count"]
            and report["generator_dropped_count"] == 0
            and report["economic_invariants_passed"]
        )
        _, summary_path = write_results(run, report, args.output / mode)
        report["summary_file"] = str(summary_path)
        return report
    finally:
        if generation:
            await _cleanup(observer, generation)
        await observer.disconnect()


async def run(args: argparse.Namespace) -> dict[str, object]:
    url = args.database_url or fixture_database_url()
    args.output.mkdir(parents=True, exist_ok=True)
    modes = (
        ("direct", "grants", "permits")
        if args.mode == "all"
        else ("direct", "grants")
        if args.mode == "both"
        else (args.mode,)
    )
    report: dict[str, object] = {
        "source": _source_manifest(),
        "configuration": {
            name: getattr(args, name)
            for name in (
                "rate",
                "duration",
                "processes",
                "partitions",
                "batch_size",
                "dwell_ms",
                "max_in_flight",
                "statement_timeout",
                "finalization_timeout",
                "grant_operations",
                "grant_ttl",
            )
        },
    }
    for mode in modes:
        report[mode] = await _measure_mode(args, mode, url)
    path = args.output / "accounting-grant-profile.json"
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


class _ObservedAccountingRepository(AccountingProtocolRepository):
    def __init__(
        self,
        *args,
        reserve_seconds: list[float],
        finalize_seconds: list[float],
        reserve_batch_sizes: list[float],
        finalize_batch_sizes: list[float],
        database_calls: Counter[str],
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._profile_reserve_seconds = reserve_seconds
        self._profile_finalize_seconds = finalize_seconds
        self._profile_reserve_batch_sizes = reserve_batch_sizes
        self._profile_finalize_batch_sizes = finalize_batch_sizes
        self._profile_database_calls = database_calls

    async def _call(self, operation, query, *parameters, expires_at):
        self._profile_database_calls[operation] += 1
        return await super()._call(operation, query, *parameters, expires_at=expires_at)

    async def reserve_batch(self, reservations, *, expires_at):
        started = perf_counter()
        try:
            return await super().reserve_batch(reservations, expires_at=expires_at)
        finally:
            self._profile_reserve_batch_sizes.append(float(len(reservations)))
            self._profile_reserve_seconds.append(perf_counter() - started)

    async def finalize_batch(self, finalizations, *, expires_at):
        started = perf_counter()
        try:
            return await super().finalize_batch(finalizations, expires_at=expires_at)
        finally:
            self._profile_finalize_batch_sizes.append(float(len(finalizations)))
            self._profile_finalize_seconds.append(perf_counter() - started)


class _ObservedPermitClient:
    """Count the exact calls made through the same production accounting pool."""

    def __init__(self, client: AccountingQueryClient, counts: Counter[str]) -> None:
        self._client = client
        self._counts = counts

    async def query_raw(self, query: str, *parameters: object):
        if "deltallm_accounting_allocate_permit_grants_batch" in query:
            operation = "allocate_permit_grants"
        elif "deltallm_accounting_claim_permits_batch" in query:
            operation = "claim_permits"
        elif "deltallm_accounting_grants" in query:
            operation = "recover_permit_grants"
        elif "deltallm_billing_operations" in query:
            operation = "recover_permit_claims"
        else:
            raise ValueError("profile saw an unknown permit query class")
        self._counts[operation] += 1
        return await self._client.query_raw(query, *parameters)


class _ObservedPermitRepository(_ObservedAccountingRepository):
    """Use the production bank for admission and the existing terminal owner."""

    def __init__(self, client, *, grantee_id, grant_target_operations, grant_ttl_seconds, **kwargs):
        super().__init__(
            client,
            grantee_id=grantee_id,
            grant_target_operations=grant_target_operations,
            grant_ttl_seconds=grant_ttl_seconds,
            **kwargs,
        )
        self.permit_bank = PreissuedPermitBank(
            AccountingPermitRepository(
                _ObservedPermitClient(client, self._profile_database_calls),
                owner_id=grantee_id,
                statement_budget_seconds=kwargs["statement_budget_seconds"],
                grant_ttl_seconds=grant_ttl_seconds,
            ),
            target_operations=grant_target_operations,
            max_operations=max(256, grant_target_operations),
            max_subjects=1,
        )

    async def reserve_batch(self, reservations, *, expires_at):
        started = perf_counter()
        try:
            return await self.permit_bank.reserve_batch(reservations, expires_at=expires_at)
        finally:
            self._profile_reserve_batch_sizes.append(float(len(reservations)))
            self._profile_reserve_seconds.append(perf_counter() - started)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url")
    parser.add_argument(
        "--mode", choices=("direct", "grants", "permits", "both", "all"), default="both"
    )
    parser.add_argument("--rate", type=float, default=1000)
    parser.add_argument("--duration", type=float, default=10)
    parser.add_argument("--processes", type=int, default=8)
    parser.add_argument("--partitions", type=int, default=64)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--dwell-ms", type=float, default=2)
    parser.add_argument("--max-in-flight", type=int, default=8192)
    parser.add_argument("--statement-timeout", type=float, default=0.25)
    parser.add_argument("--finalization-timeout", type=float, default=1.0)
    parser.add_argument("--grant-operations", type=int, default=32)
    parser.add_argument("--grant-ttl", type=int, default=30)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    valid = (
        1 <= args.rate <= 2000
        and 5 <= args.duration <= 120
        and 1 <= args.processes <= 16
        and 1 <= args.partitions <= 64
        and 1 <= args.batch_size <= 256
        and 0 <= args.dwell_ms <= 50
        and args.batch_size <= args.max_in_flight <= 100_000
        and 0.01 <= args.statement_timeout <= 2
        and args.statement_timeout <= args.finalization_timeout <= 5
        and 1 <= args.grant_operations <= 1024
        and 1 <= args.grant_ttl <= 300
    )
    if not valid:
        parser.error("profile arguments are outside their bounded production ranges")
    print(json.dumps(asyncio.run(run(args)), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
