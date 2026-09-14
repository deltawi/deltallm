"""Constant-arrival PostgreSQL admission probe; excludes HTTP/provider latency.

Run with PYTHONPATH=. and DATABASE_URL pointing at an isolated migrated test DB.
All retained samples, submitted tasks, offered rate, duration and bundles are bounded.
"""

import argparse
import asyncio
from collections import Counter
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
import gzip
import hashlib
import inspect
import json
import logging
import math
import os
from pathlib import Path
import platform
from time import perf_counter

from scripts.benchmarks.ingestion_database import (
    CAPACITY,
    ORGANIZATION,
    ingestion_database,
)
from src.db.audit_ingestion import AuditIngestionRepository, AuditOutboxEnvelope
from src.db.spend_ingestion import SpendIngestionRepository


@dataclass
class Sample:
    index: int
    tenant: int
    scheduled: float
    started: float = 0
    finished: float = 0
    status: str = "unavailable"
    accepted: int = 0
    calls: int = 0
    acquisition: float = 0
    lock_rpc: float = 0
    queue_wait: float = 0
    policy_wait: float = 0
    sql: float = 0
    commit: float = 0
    lock_occupancy_estimate: float = 0


# Equivalent lock order and keys; server timestamps add no database round trip.
# The admission decision remains the unmodified repository SQL in a later RPC.
LOCK_PROBE = """
WITH began AS MATERIALIZED (SELECT clock_timestamp() AS t),
queue_lock AS MATERIALIZED (
    SELECT pg_advisory_xact_lock(hashtextextended($1, 0)) FROM began
), acquired AS MATERIALIZED (SELECT clock_timestamp() AS t FROM queue_lock),
policy_lock AS MATERIALIZED (
    SELECT pg_advisory_xact_lock(hashtextextended('deltallm:audit-content-policy:' || $2, 0))
    FROM acquired WHERE $2::text IS NOT NULL
), done AS MATERIALIZED (
    SELECT clock_timestamp() AS t FROM (SELECT COUNT(*) FROM policy_lock) AS barrier
)
SELECT EXTRACT(EPOCH FROM acquired.t-began.t)::double precision AS queue_wait,
       EXTRACT(EPOCH FROM done.t-acquired.t)::double precision AS policy_wait
FROM began, acquired, done
"""


class ProbeClient:
    def __init__(self, client, sample):
        self.client, self.sample = client, sample
        self.lock_returned = None

    def is_transaction(self):
        return self.client.is_transaction()

    @asynccontextmanager
    async def tx(self):
        started = perf_counter()
        async with self.client.tx() as tx:
            self.sample.acquisition = perf_counter() - started
            child = ProbeClient(tx, self.sample)
            yield child
            committing = perf_counter()
        ended = perf_counter()
        self.sample.commit = ended - committing
        if child.lock_returned is not None:
            # Includes the server policy wait and client-to-commit round trip;
            # this is an occupancy estimate, not pure server execution time.
            self.sample.lock_occupancy_estimate = (
                self.sample.policy_wait + ended - child.lock_returned
            )

    async def query_raw(self, query, *args):
        self.sample.calls += 1
        started = perf_counter()
        if "pg_advisory_xact_lock" in query:
            audit = "deltallm:audit-ingestion-capacity" in query
            if not audit and "deltallm:spend-ingestion-capacity" not in query:
                raise ValueError("unexpected advisory-lock query in admission probe")
            queue = "audit" if audit else "spend"
            (row,) = await self.client.query_raw(
                LOCK_PROBE,
                f"deltallm:{queue}-ingestion-capacity",
                args[0] if audit else None,
            )
            self.lock_returned = perf_counter()
            self.sample.lock_rpc = self.lock_returned - started
            self.sample.queue_wait = float(row["queue_wait"])
            self.sample.policy_wait = float(row["policy_wait"])
            return [row]
        try:
            return await self.client.query_raw(query, *args)
        finally:
            self.sample.sql += perf_counter() - started


def distribution(values):
    values = sorted(values)
    if not values:
        return {name: None for name in ("mean", "p50", "p95", "p99")}
    return {
        "mean": sum(values) / len(values),
        **{f"p{p}": values[max(0, math.ceil(len(values) * p / 100) - 1)] for p in (50, 95, 99)},
    }


def coalescing_opportunity(samples, *, window=0.002, maximum=32, events_per_call=1):
    # A lower-complexity decision experiment, not a runtime coalescer. Group the
    # actual offered stream by tenant. This optimistic bound ignores bytes,
    # cancelled callers, pool pressure and fairness, all of which reduce benefit.
    open_batches = {}
    batches = []
    for sample in samples:
        tenant, arrival = sample.tenant, sample.scheduled
        opened, count = open_batches.get(tenant, (arrival, 0))
        if count and (arrival >= opened + window or count + events_per_call > maximum):
            batches.append(count)
            opened, count = arrival, 0
        open_batches[tenant] = (opened, count + events_per_call)
    batches.extend(count for _, count in open_batches.values())
    return {
        "window_seconds": window,
        "maximum_events": maximum,
        "optimistic_events_per_commit": sum(batches) / len(batches) if batches else 0,
        "commits_saved": len(samples) - len(batches),
    }


async def run(args):
    samples = []
    occupancy = []
    async with ingestion_database(os.environ["DATABASE_URL"], connections=args.connections) as db:
        await db.observer.execute_raw(
            f"INSERT INTO {ORGANIZATION} (organization_id, audit_content_storage_enabled) "
            "SELECT 'org-' || n, TRUE FROM generate_series(0, $1::int-1) n",
            args.organizations,
        )
        (server,) = await db.observer.query_raw("""
            SELECT version() AS version, current_setting('TimeZone') AS timezone,
                   current_setting('max_connections') AS max_connections,
                   current_setting('shared_buffers') AS shared_buffers,
                   current_setting('fsync') AS fsync,
                   current_setting('synchronous_commit') AS synchronous_commit
        """)
        started = perf_counter()

        async def submit(sample):
            sample.started = perf_counter() - started
            probe = ProbeClient(db.acceptance, sample)
            try:
                if args.queue == "audit":
                    result = await AuditIngestionRepository(probe).enqueue_bundle(
                        envelopes=[
                            AuditOutboxEnvelope(
                                f"event-{sample.index}-{i}",
                                "audit_event",
                                f"org-{sample.tenant}",
                                "required",
                                {"synthetic": "x" * 2048},
                                {"redacted": True},
                                10,
                            )
                            for i in range(args.batch_size)
                        ],
                        max_pending_events=1_000_000,
                        required_reserve=100,
                    )
                    sample.accepted = list(result.statuses.values()).count("accepted")
                    sample.status = "accepted" if sample.accepted == args.batch_size else "full"
                else:
                    result = await SpendIngestionRepository(probe).enqueue(
                        event_id=f"event-{sample.index}",
                        event_type="spend",
                        payload={
                            "organization_id": f"org-{sample.tenant}",
                            "synthetic": "x" * 2048,
                        },
                        max_attempts=10,
                        max_pending_events=1_000_000,
                    )
                    sample.status = result.status
                    sample.accepted = int(result.status == "accepted")
            except Exception as exc:
                sample.status = type(exc).__name__  # class only; never driver text/URLs/payloads
            finally:
                sample.finished = perf_counter() - started

        pending = set()
        for index in range(round(args.rate * args.seconds)):
            scheduled = index / args.rate
            await asyncio.sleep(max(0, started + scheduled - perf_counter()))
            pending = {task for task in pending if not task.done()}
            sample = Sample(index, index % args.organizations, scheduled)
            samples.append(sample)
            occupancy.append({"at": perf_counter() - started, "inflight": len(pending)})
            if len(pending) >= 128:
                sample.status = "generator_drop"
                sample.started = sample.finished = perf_counter() - started
                continue
            pending.add(asyncio.create_task(submit(sample)))
        await asyncio.gather(*pending)
        elapsed = perf_counter() - started
        (row,) = await db.observer.query_raw(
            f"SELECT pending_count FROM {CAPACITY} WHERE queue_name=$1",
            args.queue,
        )
        accepted = [s for s in samples if s.status == "accepted"]
        committed = sum(s.accepted for s in accepted)
        if committed != int(row["pending_count"]):
            raise RuntimeError("durable count does not match acknowledged accepted events")
        summary = {
            "boundary": "allocated PostgreSQL admission; excludes HTTP, auth, Redis and provider",
            "revision": args.revision,
            "server": server["version"],
            "database_settings": server,
            "allocation": asdict(db.acceptance.allocation.policy),
            "source_sha256": {
                name: hashlib.sha256(Path(inspect.getfile(cls)).read_bytes()).hexdigest()
                for name, cls in (
                    ("audit_ingestion.py", AuditIngestionRepository),
                    ("spend_ingestion.py", SpendIngestionRepository),
                    ("allocated_client.py", type(db.acceptance)),
                )
            },
            "probe_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "python": platform.python_version(),
            "platform": platform.platform(),
            "profile": vars(args) | {"output": str(args.output)},
            "offered": len(samples),
            "accepted_calls": len(accepted),
            "accepted_events": committed,
            "offered_rps": args.rate,
            "completed_rps_in_window": sum(s.finished <= args.seconds for s in accepted)
            / args.seconds,
            "completed_rps_including_drain": len(accepted) / elapsed,
            "errors": dict(Counter(s.status for s in samples if s.status != "accepted")),
            "latency_seconds_all": distribution([s.finished - s.started for s in samples]),
            "latency_seconds_accepted": distribution([s.finished - s.started for s in accepted]),
            "scheduler_lag_seconds": distribution([s.started - s.scheduled for s in samples]),
            "events_per_commit": committed / len(accepted) if accepted else 0,
            "sql_calls_per_accepted": distribution([s.calls for s in accepted]),
            "inflight_first_quarter_mean": sum(
                v["inflight"] for v in occupancy[: len(occupancy) // 4]
            )
            / max(1, len(occupancy) // 4),
            "inflight_last_quarter_mean": sum(
                v["inflight"] for v in occupancy[-len(occupancy) // 4 :]
            )
            / max(1, len(occupancy) // 4),
            "phases_seconds": {
                name: distribution([getattr(s, name) for s in accepted])
                for name in (
                    "acquisition",
                    "queue_wait",
                    "policy_wait",
                    "lock_rpc",
                    "sql",
                    "commit",
                    "lock_occupancy_estimate",
                )
            },
            "coalescing_opportunity": coalescing_opportunity(
                samples, events_per_call=args.batch_size
            ),
            "consumer_note": "Consumers disabled intentionally; durable backlog grows by accepted events. Inflight measures admission work, not durable backlog.",
        }
        args.output.mkdir(parents=True, exist_ok=True)
        (args.output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        (args.output / "samples.jsonl.gz").write_bytes(
            gzip.compress("".join(json.dumps(asdict(s)) + "\n" for s in samples).encode(), mtime=0)
        )
        (args.output / "inflight.jsonl.gz").write_bytes(
            gzip.compress("".join(json.dumps(v) + "\n" for v in occupancy).encode(), mtime=0)
        )
        print(
            json.dumps(
                {
                    key: summary[key]
                    for key in (
                        "accepted_calls",
                        "errors",
                        "latency_seconds_accepted",
                        "coalescing_opportunity",
                    )
                }
            )
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue", choices=("audit", "spend"), required=True)
    parser.add_argument("--organizations", type=int, choices=(1, 13), required=True)
    parser.add_argument("--rate", type=int, default=50)
    parser.add_argument("--seconds", type=int, default=10)
    parser.add_argument("--connections", type=int, choices=(1, 5), default=5)
    parser.add_argument("--batch-size", type=int, choices=(1, 2, 32), default=1)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.rate <= 1000 or not 1 <= args.seconds <= 30:
        parser.error("rate must be 1..1000 and seconds 1..30")
    if args.queue == "spend" and args.batch_size != 1:
        parser.error("spend admission accepts one event per call")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
