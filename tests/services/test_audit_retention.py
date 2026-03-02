from __future__ import annotations

import pytest

from src.services.audit_retention import AuditRetentionConfig, AuditRetentionWorker


class FakeAuditRepository:
    def __init__(self) -> None:
        self.deleted_payloads: list[str] = []
        self.deleted_events: list[str] = []

    async def list_expired_payload_ids(self, *, default_retention_days: int, limit: int) -> list[str]:
        assert default_retention_days == 90
        assert limit == 500
        return ["pl-1", "pl-2"]

    async def delete_payloads_by_ids(self, payload_ids: list[str]) -> int:
        self.deleted_payloads.extend(payload_ids)
        return len(payload_ids)

    async def list_expired_event_ids(self, *, default_retention_days: int, limit: int) -> list[str]:
        assert default_retention_days == 365
        assert limit == 500
        return ["evt-1"]

    async def delete_events_by_ids(self, event_ids: list[str]) -> int:
        self.deleted_events.extend(event_ids)
        return len(event_ids)


@pytest.mark.asyncio
async def test_audit_retention_worker_deletes_expired_payloads_and_events() -> None:
    repository = FakeAuditRepository()
    worker = AuditRetentionWorker(
        repository=repository,  # type: ignore[arg-type]
        config=AuditRetentionConfig(
            interval_seconds=60.0,
            scan_limit=500,
            metadata_retention_days=365,
            payload_retention_days=90,
        ),
    )

    deleted_payloads, deleted_events = await worker.process_once()

    assert deleted_payloads == 2
    assert deleted_events == 1
    assert repository.deleted_payloads == ["pl-1", "pl-2"]
    assert repository.deleted_events == ["evt-1"]
