from __future__ import annotations

import asyncio
import logging

import pytest

from src.services.audit.audit_retention import AuditRetentionConfig, AuditRetentionWorker


class FakeAuditRepository:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int, int]] = []
        self.failures: list[Exception] = []

    async def delete_expired_payloads(self, *, default_retention_days: int, limit: int) -> int:
        self.calls.append(("payloads", default_retention_days, limit))
        if self.failures:
            raise self.failures.pop(0)
        return 2

    async def delete_expired_events(self, *, default_retention_days: int, limit: int) -> int:
        self.calls.append(("events", default_retention_days, limit))
        return 1


def _worker(repository: FakeAuditRepository, *, interval_seconds: float = 60.0):
    return AuditRetentionWorker(
        repository=repository,  # type: ignore[arg-type]
        config=AuditRetentionConfig(
            interval_seconds=interval_seconds,
            scan_limit=500,
            metadata_retention_days=365,
            payload_retention_days=90,
        ),
    )


@pytest.mark.asyncio
async def test_audit_retention_worker_deletes_expired_payloads_and_events() -> None:
    repository = FakeAuditRepository()

    deleted_payloads, deleted_events = await _worker(repository).process_once()

    assert (deleted_payloads, deleted_events) == (2, 1)
    assert repository.calls == [("payloads", 90, 500), ("events", 365, 500)]


@pytest.mark.asyncio
async def test_audit_retention_worker_keeps_running_after_a_failed_pass(caplog) -> None:
    repository = FakeAuditRepository()
    repository.failures.append(RuntimeError("operator does not exist: text = uuid"))
    worker = _worker(repository, interval_seconds=0.001)
    caplog.set_level(logging.ERROR)

    task = asyncio.create_task(worker.run())
    try:
        for _ in range(200):
            if ("events", 365, 500) in repository.calls:
                break
            await asyncio.sleep(0.005)
    finally:
        worker.stop()
        await asyncio.wait_for(task, timeout=1)

    assert repository.calls[:2] == [("payloads", 90, 500), ("payloads", 90, 500)]
    assert ("events", 365, 500) in repository.calls
    assert "audit retention pass failed" in caplog.text
