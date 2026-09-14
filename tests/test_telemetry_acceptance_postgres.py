from __future__ import annotations

import asyncio
import os

from prisma import Prisma
from prisma.errors import RawQueryError
import pytest

from src.db.audit_ingestion import AuditIngestionRepository, AuditOutboxEnvelope
from src.db.spend_ingestion import SpendIngestionRepository
from src.metrics.prometheus import get_prometheus_registry

pytestmark = pytest.mark.postgres


@pytest.mark.asyncio
@pytest.mark.parametrize("queue", ["audit", "spend"])
async def test_real_advisory_lock_timeout_is_attributed_and_releases_observation(
    queue: str,
) -> None:
    url = os.getenv("DATABASE_URL")
    if not url:
        if os.getenv("CI"):
            pytest.fail("CI must provide a migrated test PostgreSQL")
        pytest.skip("Requires migrated test PostgreSQL")
    locker = Prisma(datasource={"url": url})
    writer = Prisma(datasource={"url": url})
    await locker.connect()
    await writer.connect()
    registry = get_prometheus_registry()
    labels = {"queue": queue, "phase": "lock", "reason": "lock_timeout"}
    before = registry.get_sample_value("deltallm_telemetry_acceptance_failures_total", labels) or 0
    try:
        async with locker.tx() as held:
            await held.query_raw(
                "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))::text AS locked",
                f"deltallm:{queue}-ingestion-capacity",
            )
            with pytest.raises(RawQueryError):
                async with writer.tx() as tx:
                    await tx.execute_raw("SET LOCAL lock_timeout = '100ms'")
                    if queue == "spend":
                        operation = SpendIngestionRepository(tx).enqueue(
                            event_id="must-not-be-inserted",
                            event_type="spend",
                            payload={},
                            max_attempts=1,
                            max_pending_events=100,
                        )
                    else:
                        operation = AuditIngestionRepository(tx).enqueue_bundle(
                            envelopes=[
                                AuditOutboxEnvelope(
                                    event_id="must-not-be-inserted",
                                    record_type="audit_event",
                                    organization_id=None,
                                    delivery_class="required",
                                    payload={},
                                    redacted_payload={},
                                    max_attempts=1,
                                )
                            ],
                            max_pending_events=100,
                            required_reserve=1,
                        )
                    await asyncio.wait_for(operation, 3)
        assert (
            registry.get_sample_value("deltallm_telemetry_acceptance_failures_total", labels)
            == before + 1
        )
        assert (
            registry.get_sample_value(
                "deltallm_telemetry_acceptance_in_flight", {"queue": queue, "phase": "total"}
            )
            == 0
        )
        assert (
            registry.get_sample_value(
                "deltallm_telemetry_acceptance_serialized_bytes", {"queue": queue}
            )
            == 0
        )
    finally:
        await writer.disconnect()
        await locker.disconnect()
