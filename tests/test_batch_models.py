from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.batch.models import (
    BATCH_JOB_STATUSES,
    BATCH_JOB_STATUS_VALUES,
    BatchJobRecord,
    BatchJobStatus,
    normalize_batch_job_status,
)


def test_batch_job_status_values_follow_enum_definition() -> None:
    assert BATCH_JOB_STATUSES == tuple(BatchJobStatus)
    assert BATCH_JOB_STATUS_VALUES == tuple(status.value for status in BatchJobStatus)


@pytest.mark.parametrize("status", ["queued", BatchJobStatus.QUEUED])
def test_normalize_batch_job_status_returns_enum(status: str | BatchJobStatus) -> None:
    assert normalize_batch_job_status(status) is BatchJobStatus.QUEUED


def test_batch_job_record_normalizes_status_to_enum() -> None:
    now = datetime.now(tz=UTC)

    record = BatchJobRecord(
        batch_id="batch-1",
        endpoint="/v1/embeddings",
        status="queued",
        execution_mode="managed_internal",
        input_file_id="file-1",
        output_file_id=None,
        error_file_id=None,
        model="m1",
        metadata=None,
        provider_batch_id=None,
        provider_status=None,
        provider_error=None,
        provider_last_sync_at=None,
        total_items=1,
        in_progress_items=0,
        completed_items=0,
        failed_items=0,
        cancelled_items=0,
        locked_by=None,
        lease_expires_at=None,
        cancel_requested_at=None,
        status_last_updated_at=now,
        created_by_api_key="key-1",
        created_by_user_id=None,
        created_by_team_id=None,
        created_at=now,
        started_at=None,
        completed_at=None,
        expires_at=None,
        created_by_organization_id=None,
    )

    assert record.status is BatchJobStatus.QUEUED
