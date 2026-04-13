from __future__ import annotations

import logging
from datetime import UTC, datetime

import pytest

from src.batch.create import (
    BatchCreateSessionCreate,
    BatchCreateSessionRecord,
    BatchCreateSessionStager,
    BatchCreateSessionStatus,
    BatchCreateStagedRequest,
    StagedBatchCreateArtifact,
)


class _SessionRepositoryStub:
    def __init__(self) -> None:
        self.created: list[BatchCreateSessionCreate] = []
        self.raise_on_create: Exception | None = None
        now = datetime.now(tz=UTC)
        self.result = BatchCreateSessionRecord(
            session_id="session-1",
            target_batch_id="batch-1",
            status=BatchCreateSessionStatus.STAGED,
            endpoint="/v1/embeddings",
            input_file_id="file-1",
            staged_storage_backend="local",
            staged_storage_key="batch-create-stage/session-1.jsonl",
            staged_checksum="checksum-1",
            staged_bytes=128,
            expected_item_count=1,
            inferred_model=None,
            metadata=None,
            requested_service_tier=None,
            effective_service_tier=None,
            service_tier_source=None,
            scheduling_scope_key=None,
            priority_quota_scope_key=None,
            idempotency_scope_key=None,
            idempotency_key=None,
            last_error_code=None,
            last_error_message=None,
            promotion_attempt_count=0,
            created_by_api_key=None,
            created_by_user_id=None,
            created_by_team_id=None,
            created_by_organization_id=None,
            created_at=now,
            completed_at=None,
            last_attempt_at=None,
            expires_at=None,
        )

    async def create_session(self, session: BatchCreateSessionCreate) -> BatchCreateSessionRecord | None:
        self.created.append(session)
        if self.raise_on_create is not None:
            raise self.raise_on_create
        return self.result


class _StagingStub:
    def __init__(self) -> None:
        self.deleted: list[str] = []
        self.delete_failures_remaining = 0
        self.artifact = StagedBatchCreateArtifact(
            storage_backend="local",
            storage_key="batch-create-stage/session-1.jsonl",
            bytes_size=128,
            checksum="checksum-1",
        )

    async def write_records(self, records, *, filename: str):  # noqa: ANN001
        del filename
        if hasattr(records, "__aiter__"):
            collected = [record async for record in records]
        else:
            collected = list(records)
        assert collected
        return self.artifact

    async def delete(self, artifact: StagedBatchCreateArtifact) -> None:
        if self.delete_failures_remaining > 0:
            self.delete_failures_remaining -= 1
            raise RuntimeError("delete failed")
        self.deleted.append(artifact.storage_key)


def _build_session(artifact: StagedBatchCreateArtifact) -> BatchCreateSessionCreate:
    return BatchCreateSessionCreate(
        target_batch_id="batch-1",
        endpoint="/v1/embeddings",
        input_file_id="file-1",
        staged_storage_backend=artifact.storage_backend,
        staged_storage_key=artifact.storage_key,
        staged_checksum=artifact.checksum,
        staged_bytes=artifact.bytes_size,
        expected_item_count=1,
    )


@pytest.mark.asyncio
async def test_batch_create_session_stager_returns_created_session() -> None:
    repository = _SessionRepositoryStub()
    staging = _StagingStub()
    stager = BatchCreateSessionStager(
        repository=repository,  # type: ignore[arg-type]
        staging=staging,  # type: ignore[arg-type]
    )

    created = await stager.stage_session(
        records=[BatchCreateStagedRequest(line_number=1, custom_id="req-1", request_body={"model": "m1", "input": "a"})],
        filename="session-1.jsonl",
        build_session=_build_session,
    )

    assert created.session_id == "session-1"
    assert repository.created[0].staged_storage_key == "batch-create-stage/session-1.jsonl"
    assert staging.deleted == []


@pytest.mark.asyncio
async def test_batch_create_session_stager_compensates_artifact_when_session_insert_fails() -> None:
    repository = _SessionRepositoryStub()
    repository.raise_on_create = RuntimeError("duplicate target_batch_id")
    staging = _StagingStub()
    stager = BatchCreateSessionStager(
        repository=repository,  # type: ignore[arg-type]
        staging=staging,  # type: ignore[arg-type]
    )

    with pytest.raises(RuntimeError, match="duplicate target_batch_id"):
        await stager.stage_session(
            records=[BatchCreateStagedRequest(line_number=1, custom_id="req-1", request_body={"model": "m1", "input": "a"})],
            filename="session-1.jsonl",
            build_session=_build_session,
        )

    assert staging.deleted == ["batch-create-stage/session-1.jsonl"]


@pytest.mark.asyncio
async def test_batch_create_session_stager_logs_compensation_delete_failure_without_masking_insert_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    repository = _SessionRepositoryStub()
    repository.raise_on_create = RuntimeError("duplicate idempotency key")
    staging = _StagingStub()
    staging.delete_failures_remaining = 10
    stager = BatchCreateSessionStager(
        repository=repository,  # type: ignore[arg-type]
        staging=staging,  # type: ignore[arg-type]
    )

    with caplog.at_level(logging.WARNING):
        with pytest.raises(RuntimeError, match="duplicate idempotency key"):
            await stager.stage_session(
                records=[
                    BatchCreateStagedRequest(
                        line_number=1,
                        custom_id="req-1",
                        request_body={"model": "m1", "input": "a"},
                    )
                ],
                filename="session-1.jsonl",
                build_session=_build_session,
            )

    assert "stage compensation delete failed" in caplog.text


@pytest.mark.asyncio
async def test_batch_create_session_stager_retries_compensation_delete_before_succeeding() -> None:
    repository = _SessionRepositoryStub()
    repository.raise_on_create = RuntimeError("duplicate target_batch_id")
    staging = _StagingStub()
    staging.delete_failures_remaining = 2
    stager = BatchCreateSessionStager(
        repository=repository,  # type: ignore[arg-type]
        staging=staging,  # type: ignore[arg-type]
    )

    with pytest.raises(RuntimeError, match="duplicate target_batch_id"):
        await stager.stage_session(
            records=[BatchCreateStagedRequest(line_number=1, custom_id="req-1", request_body={"model": "m1", "input": "a"})],
            filename="session-1.jsonl",
            build_session=_build_session,
        )

    assert staging.deleted == ["batch-create-stage/session-1.jsonl"]
