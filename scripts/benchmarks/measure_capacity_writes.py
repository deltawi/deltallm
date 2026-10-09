"""Measure physical capacity-row rewrites without adding production triggers."""

import argparse
import asyncio
import json
import os
from pathlib import Path
from time import perf_counter

from scripts.benchmarks.ingestion_database import CAPACITY, ingestion_database
from scripts.benchmarks.measure_admission import distribution
from src.db.audit_ingestion import AuditIngestionRepository


async def run(output, revision):
    result = {"revision": revision, "cases": {}}
    async with ingestion_database(os.environ["DATABASE_URL"], connections=1) as db:
        repo = AuditIngestionRepository(db.worker)

        async def identity():
            (row,) = await db.observer.query_raw(
                f"SELECT ctid::text AS id, pending_count FROM {CAPACITY} WHERE queue_name='audit'"
            )
            return row

        async def submit(event):
            return await repo.enqueue(
                event_id=event,
                record_type="audit_event",
                organization_id=None,
                delivery_class="required",
                payload={},
                redacted_payload={},
                max_attempts=1,
                max_pending_events=1,
                required_reserve=0,
            )

        for case in ("empty-poll", "duplicate", "full"):
            if case == "duplicate":
                assert (await submit("existing")).status == "accepted"
            previous = await identity()
            writes = 0
            samples = []
            for _ in range(200):
                start = perf_counter()
                if case == "empty-poll":
                    assert (
                        await repo.claim_batch(
                            limit=100,
                            worker_id="probe",
                            claim_token="probe",
                            lease_seconds=30,
                        )
                        == []
                    )
                else:
                    outcome = await submit("existing" if case == "duplicate" else "new")
                    assert outcome.status == case
                samples.append(perf_counter() - start)
                current = await identity()
                writes += int(current["id"] != previous["id"])
                assert current["pending_count"] == previous["pending_count"]
                previous = current
            result["cases"][case] = {
                "operations": len(samples),
                "capacity_row_rewrites": writes,
                "latency_seconds": distribution(samples),
                "samples_seconds": samples,
            }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + "\n")
    print(
        json.dumps({key: value["capacity_row_rewrites"] for key, value in result["cases"].items()})
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    asyncio.run(run(args.output, args.revision))
