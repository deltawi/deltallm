from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.billing.spend import SpendTrackingService
from src.billing.spend_ingestion import SpendIngestionConfig, SpendIngestionService
from src.db.repositories import AuditRepository
from src.services.audit_service import AuditEventInput, AuditIngestionConfig, AuditService

pytestmark = pytest.mark.hermetic


async def test_required_audit_and_optional_enqueue_use_distinct_repositories():
    foreground, background = object(), object()
    service = AuditService(
        AuditRepository(foreground),
        db_client=foreground,
        worker_db_client=background,
        ingestion_config=AuditIngestionConfig(enabled=True, worker_enabled=False),
    )
    accepted = SimpleNamespace(status="accepted", pending_count=1)
    service.ingestion_repository.enqueue = AsyncMock(return_value=accepted)
    service.worker_ingestion_repository.enqueue = AsyncMock(return_value=accepted)
    assert service.ingestion_repository.prisma is foreground
    assert service.worker_ingestion_repository.prisma is background
    assert service.worker_repository.prisma is background

    await service.enqueue_event(AuditEventInput(action="required-test"), delivery_class="required")
    service.ingestion_repository.enqueue.assert_awaited_once()
    service.worker_ingestion_repository.enqueue.assert_not_awaited()
    await service.enqueue_event(
        AuditEventInput(action="optional-test"), delivery_class="best_effort"
    )
    service.ingestion_repository.enqueue.assert_awaited_once()
    service.worker_ingestion_repository.enqueue.assert_awaited_once()


async def test_audit_claims_and_backlog_never_use_acceptance_repository():
    foreground, background = object(), object()
    service = AuditService(
        AuditRepository(foreground), db_client=foreground, worker_db_client=background
    )
    record = object()
    service.worker_ingestion_repository.claim_batch = AsyncMock(return_value=[record])
    service.ingestion_repository.claim_batch = AsyncMock(
        side_effect=AssertionError("foreground claim")
    )
    service.worker_ingestion_repository.pending_stats = AsyncMock(return_value=(0, 0))
    service.ingestion_repository.pending_stats = AsyncMock(
        side_effect=AssertionError("foreground stats")
    )
    service._process_durable_batch = AsyncMock()
    await service._durable_worker_iteration()
    service.worker_ingestion_repository.claim_batch.assert_awaited_once()
    service._process_durable_batch.assert_awaited_once_with([record])


async def test_spend_enqueue_and_claims_use_distinct_allocations():
    foreground, background = object(), object()
    service = SpendIngestionService(
        db_client=foreground,
        worker_db_client=background,
        writer=SpendTrackingService(foreground),
        config=SpendIngestionConfig(enabled=True, worker_enabled=False),
    )
    service.repository.enqueue = AsyncMock(
        return_value=SimpleNamespace(status="accepted", pending_count=1)
    )
    service.worker_repository.enqueue = AsyncMock(
        side_effect=AssertionError("background acceptance")
    )
    service.repository.claim_batch = AsyncMock(side_effect=AssertionError("foreground claim"))
    service.worker_repository.claim_batch = AsyncMock(return_value=[])
    await service._enqueue("spend", {"cost": 0}, event_id="allocation-spend")
    assert await service._claim_batch() == []
    service.repository.enqueue.assert_awaited_once()
    service.worker_repository.claim_batch.assert_awaited_once()


async def test_worker_transaction_and_foreground_fallback_keep_their_client_ownership():
    from contextlib import asynccontextmanager

    opened = []

    class Client:
        @asynccontextmanager
        async def tx(self):
            opened.append(self)
            yield self

    foreground, background = Client(), Client()
    service = SpendIngestionService(
        db_client=foreground,
        worker_db_client=background,
        writer=SpendTrackingService(foreground),
        config=SpendIngestionConfig(enabled=True),
    )
    async with service._transaction() as fallback:
        assert fallback is foreground
    async with service._transaction(service.worker_db) as consumer:
        assert consumer is background
    assert opened == [foreground, background]


async def test_auth_invalidation_discovery_cannot_consume_lookup_database():
    from types import SimpleNamespace
    from src.db.repositories import KeyRepository
    from src.services.key_service import KeyService

    foreground = SimpleNamespace(query_raw=AsyncMock(side_effect=AssertionError("auth pool used")))
    control = SimpleNamespace(query_raw=AsyncMock(return_value=[{"token": "key-hash"}]))
    redis = SimpleNamespace(delete=AsyncMock(return_value=1))
    service = KeyService(
        KeyRepository(foreground),
        redis_client=redis,
        invalidation_repository=KeyRepository(control),
    )
    assert await service.invalidate_keys_for_org("org-1") == 1
    control.query_raw.assert_awaited_once()
    foreground.query_raw.assert_not_awaited()
    redis.delete.assert_awaited_once()
