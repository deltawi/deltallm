"""Capture the real repository admission plans over representative history."""

import argparse
import asyncio
import json
import os
from pathlib import Path

from scripts.benchmarks.ingestion_database import (
    AUDIT,
    ORGANIZATION,
    SPEND,
    ingestion_database,
)
from src.db.audit_ingestion import AuditIngestionRepository
from src.db.spend_ingestion import SpendIngestionRepository


class CaptureQuery:
    def __init__(self):
        self.query = None
        self.parameters = ()

    async def query_raw(self, query, *parameters):
        self.query, self.parameters = query, parameters
        return []


async def run(output):
    async with ingestion_database(os.environ["DATABASE_URL"]) as db:
        await db.observer.execute_raw(
            f"INSERT INTO {ORGANIZATION} (organization_id) SELECT 'org-' || n FROM generate_series(1, 10000) n"
        )
        await db.observer.execute_raw(
            f"INSERT INTO {AUDIT} (event_id, record_type, delivery_class, payload_json, redacted_payload_json, status) "
            "SELECT 'history-' || n, 'audit_event', 'required', '{}'::jsonb, '{}'::jsonb, 'completed' "
            "FROM generate_series(1, 50000) n"
        )
        await db.observer.execute_raw(
            f"INSERT INTO {SPEND} (event_id, event_type, payload_json, status) "
            "SELECT 'history-' || n, 'spend', '{}'::jsonb, 'completed' FROM generate_series(1, 50000) n"
        )
        for table in (AUDIT, SPEND, ORGANIZATION):
            await db.observer.execute_raw(f"ANALYZE {table}")
        plans = {}
        for queue in ("audit", "spend"):
            capture = CaptureQuery()
            if queue == "audit":
                await AuditIngestionRepository(capture)._enqueue_bundle_under_lock(
                    serialized_envelopes=json.dumps(
                        [
                            {
                                "event_id": "new",
                                "record_type": "audit_event",
                                "organization_id": "org-1",
                                "delivery_class": "required",
                                "payload": {},
                                "redacted_payload": {},
                                "max_attempts": 10,
                            }
                        ]
                    ),
                    organization_id="org-1",
                    max_pending_events=10000,
                    required_reserve=100,
                )
            else:
                await SpendIngestionRepository(capture)._enqueue_under_lock(
                    event_id="new",
                    event_type="spend",
                    payload={},
                    max_attempts=10,
                    max_pending_events=10000,
                )
            async with db.observer.tx() as tx:
                repository = (
                    AuditIngestionRepository(tx)
                    if queue == "audit"
                    else SpendIngestionRepository(tx)
                )
                if queue == "audit":
                    await repository._lock_bundle_admission("org-1")
                else:
                    await repository._lock_enqueue_admission()
                plans[queue] = await tx.query_raw(
                    "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + capture.query,
                    *capture.parameters,
                )
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(
                {
                    "history_per_queue": 50000,
                    "organizations": 10000,
                    "plans": plans,
                },
                indent=2,
            )
            + "\n"
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    asyncio.run(run(parser.parse_args().output))
