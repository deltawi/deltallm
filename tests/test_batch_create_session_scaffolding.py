from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.batch.create.cleanup import BatchCreateSessionCleanupConfig
from src.batch.create.defaults import (
    DEFAULT_CREATE_SESSION_CLEANUP_INTERVAL_SECONDS,
    DEFAULT_CREATE_SESSION_COMPLETED_RETENTION_SECONDS,
    DEFAULT_CREATE_SESSION_FAILED_RETENTION_SECONDS,
    DEFAULT_CREATE_SESSION_RETRYABLE_RETENTION_SECONDS,
)
from src.batch.create import (
    BatchCreatePromotionResult,
    BatchCreateSessionCreate,
    BatchCreateSessionRepository,
    BatchCreateSessionStatus,
    StagedBatchCreateArtifact,
)
from src.batch.repository import BatchRepository
from src.config import GeneralSettings


class _PrismaSpy:
    def __init__(self, rows: list[dict] | None = None) -> None:
        self.rows = rows or []
        self.sql = ""
        self.params: tuple[object, ...] = ()
        self.deleted_session_id: str | None = None

    async def query_raw(self, sql: str, *params):
        self.sql = sql
        self.params = params
        return self.rows

    async def execute_raw(self, sql: str, *params):
        self.sql = sql
        self.params = params
        self.deleted_session_id = str(params[0]) if params else None
        return 1


def test_batch_create_session_scaffold_exports_are_constructible() -> None:
    artifact = StagedBatchCreateArtifact(
        storage_backend="local",
        storage_key="batch-create/session-1.jsonl",
        bytes_size=128,
        checksum="abc123",
    )
    result = BatchCreatePromotionResult(session_id="session-1", batch_id="batch-1", promoted=False)

    assert artifact.storage_backend == "local"
    assert artifact.storage_key.endswith(".jsonl")
    assert result.batch_id == "batch-1"


def test_batch_repository_exposes_create_session_repository() -> None:
    prisma = _PrismaSpy()
    repository = BatchRepository(prisma_client=prisma)

    assert isinstance(repository.create_sessions, BatchCreateSessionRepository)
    assert repository.create_sessions.prisma is prisma


def test_batch_create_session_cleanup_defaults_match_general_settings() -> None:
    cleanup = BatchCreateSessionCleanupConfig()
    settings = GeneralSettings()

    assert cleanup.interval_seconds == DEFAULT_CREATE_SESSION_CLEANUP_INTERVAL_SECONDS
    assert cleanup.completed_retention_seconds == DEFAULT_CREATE_SESSION_COMPLETED_RETENTION_SECONDS
    assert cleanup.retryable_retention_seconds == DEFAULT_CREATE_SESSION_RETRYABLE_RETENTION_SECONDS
    assert cleanup.failed_retention_seconds == DEFAULT_CREATE_SESSION_FAILED_RETENTION_SECONDS
    assert settings.embeddings_batch_create_session_cleanup_interval_seconds == cleanup.interval_seconds
    assert settings.embeddings_batch_create_session_completed_retention_seconds == cleanup.completed_retention_seconds
    assert settings.embeddings_batch_create_session_retryable_retention_seconds == cleanup.retryable_retention_seconds
    assert settings.embeddings_batch_create_session_failed_retention_seconds == cleanup.failed_retention_seconds


def test_batch_create_session_create_requires_full_idempotency_pair() -> None:
    with pytest.raises(ValueError, match="must both be set or both be omitted"):
        BatchCreateSessionCreate(
            target_batch_id="batch-1",
            endpoint="/v1/embeddings",
            input_file_id="file-1",
            staged_storage_backend="local",
            staged_storage_key="staged/file-1.jsonl",
            staged_bytes=512,
            expected_item_count=3,
            idempotency_scope_key="team:team-1",
        )


def test_batch_create_session_create_normalizes_blank_idempotency_values() -> None:
    session = BatchCreateSessionCreate(
        target_batch_id="batch-1",
        endpoint="/v1/embeddings",
        input_file_id="file-1",
        staged_storage_backend="local",
        staged_storage_key="staged/file-1.jsonl",
        staged_bytes=512,
        expected_item_count=3,
        idempotency_scope_key="   ",
        idempotency_key="",
    )

    assert session.idempotency_scope_key is None
    assert session.idempotency_key is None


@pytest.mark.asyncio
async def test_create_session_repository_insert_and_lookup_contract() -> None:
    now = datetime.now(tz=UTC)
    prisma = _PrismaSpy(
        rows=[
            {
                "session_id": "session-1",
                "target_batch_id": "batch-1",
                "status": BatchCreateSessionStatus.STAGED,
                "endpoint": "/v1/embeddings",
                "input_file_id": "file-1",
                "staged_storage_backend": "local",
                "staged_storage_key": "staged/file-1.jsonl",
                "staged_checksum": "checksum-1",
                "staged_bytes": 512,
                "expected_item_count": 3,
                "inferred_model": "text-embedding-3-small",
                "metadata": {"source": "test"},
                "requested_service_tier": None,
                "effective_service_tier": None,
                "service_tier_source": None,
                "scheduling_scope_key": "team:team-1",
                "priority_quota_scope_key": "team:team-1",
                "idempotency_scope_key": "team:team-1",
                "idempotency_key": "idem-1",
                "last_error_code": None,
                "last_error_message": None,
                "promotion_attempt_count": 0,
                "created_by_api_key": "key-1",
                "created_by_user_id": "user-1",
                "created_by_team_id": "team-1",
                "created_by_organization_id": "org-1",
                "created_at": now,
                "completed_at": None,
                "last_attempt_at": None,
                "expires_at": None,
            }
        ]
    )
    repository = BatchCreateSessionRepository(prisma_client=prisma)

    record = await repository.create_session(
        BatchCreateSessionCreate(
            target_batch_id="batch-1",
            endpoint="/v1/embeddings",
            input_file_id="file-1",
            staged_storage_backend="local",
            staged_storage_key="staged/file-1.jsonl",
            staged_checksum="checksum-1",
            staged_bytes=512,
            expected_item_count=3,
            inferred_model="text-embedding-3-small",
            metadata={"source": "test"},
            scheduling_scope_key="team:team-1",
            priority_quota_scope_key="team:team-1",
            idempotency_scope_key="team:team-1",
            idempotency_key="idem-1",
            created_by_api_key="key-1",
            created_by_user_id="user-1",
            created_by_team_id="team-1",
            created_by_organization_id="org-1",
        )
    )

    assert record is not None
    assert record.status == BatchCreateSessionStatus.STAGED
    assert "INSERT INTO deltallm_batch_create_session" in prisma.sql
    assert "idempotency_scope_key" in prisma.sql
    assert prisma.params[1] == "batch-1"

    prisma.rows = []
    await repository.get_session_by_idempotency_key(
        idempotency_scope_key="team:team-1",
        idempotency_key="idem-1",
    )

    assert "WHERE idempotency_scope_key = $1" in prisma.sql
    assert "AND idempotency_key = $2" in prisma.sql


@pytest.mark.asyncio
async def test_create_session_repository_rejects_inconsistent_mutated_idempotency_pair() -> None:
    prisma = _PrismaSpy()
    repository = BatchCreateSessionRepository(prisma_client=prisma)
    session = BatchCreateSessionCreate(
        target_batch_id="batch-1",
        endpoint="/v1/embeddings",
        input_file_id="file-1",
        staged_storage_backend="local",
        staged_storage_key="staged/file-1.jsonl",
        staged_bytes=512,
        expected_item_count=3,
    )
    session.idempotency_scope_key = "team:team-1"
    session.idempotency_key = None

    with pytest.raises(ValueError, match="must both be set or both be omitted"):
        await repository.create_session(session)

    assert prisma.sql == ""


@pytest.mark.asyncio
async def test_get_session_by_idempotency_key_rejects_blank_lookup_inputs() -> None:
    prisma = _PrismaSpy()
    repository = BatchCreateSessionRepository(prisma_client=prisma)

    with pytest.raises(ValueError, match="are required for lookup"):
        await repository.get_session_by_idempotency_key(
            idempotency_scope_key="team:team-1",
            idempotency_key="   ",
        )

    assert prisma.sql == ""


@pytest.mark.asyncio
async def test_create_session_repository_lists_expired_and_deletes() -> None:
    now = datetime.now(tz=UTC)
    prisma = _PrismaSpy(
        rows=[
            {
                "session_id": "session-2",
                "target_batch_id": "batch-2",
                "status": BatchCreateSessionStatus.EXPIRED,
                "endpoint": "/v1/embeddings",
                "input_file_id": "file-2",
                "staged_storage_backend": "local",
                "staged_storage_key": "staged/file-2.jsonl",
                "staged_checksum": None,
                "staged_bytes": 64,
                "expected_item_count": 1,
                "inferred_model": None,
                "metadata": None,
                "requested_service_tier": None,
                "effective_service_tier": None,
                "service_tier_source": None,
                "scheduling_scope_key": None,
                "priority_quota_scope_key": None,
                "idempotency_scope_key": None,
                "idempotency_key": None,
                "last_error_code": None,
                "last_error_message": None,
                "promotion_attempt_count": 0,
                "created_by_api_key": None,
                "created_by_user_id": None,
                "created_by_team_id": None,
                "created_by_organization_id": None,
                "created_at": now,
                "completed_at": None,
                "last_attempt_at": None,
                "expires_at": now,
            }
        ]
    )
    repository = BatchCreateSessionRepository(prisma_client=prisma)

    sessions = await repository.list_expired_sessions(now=now, limit=20)

    assert len(sessions) == 1
    assert "ORDER BY expires_at ASC" in prisma.sql
    assert prisma.params[1] == 20

    await repository.delete_session("session-2")

    assert "DELETE FROM deltallm_batch_create_session" in prisma.sql
    assert prisma.deleted_session_id == "session-2"
