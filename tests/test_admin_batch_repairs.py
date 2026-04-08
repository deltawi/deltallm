from __future__ import annotations

from datetime import UTC, datetime

import pytest
from prometheus_client import generate_latest

from src.api.admin.endpoints.common import AuthScope
from src.batch.models import BatchJobRecord, BatchJobStatus
from src.batch.models import encode_operator_failed_reason
from src.metrics import get_prometheus_registry


class _FakeBatchRepairDB:
    def __init__(
        self,
        *,
        status: str = "finalizing",
        created_by_team_id: str | None = "team-1",
        created_by_organization_id: str | None = None,
    ) -> None:
        self.status = status
        self.created_by_team_id = created_by_team_id
        self.created_by_organization_id = created_by_organization_id

    async def query_raw(self, query: str, *params):
        if "FROM deltallm_batch_job" in query and "WHERE batch_id = $1" in query:
            return [{
                "batch_id": str(params[0]),
                "status": self.status,
                "created_by_team_id": self.created_by_team_id,
                "created_by_organization_id": self.created_by_organization_id,
            }]
        return []


class _FakeBatchRepairRepository:
    def __init__(self, *, status: str = "finalizing", refresh_status: str = "finalizing") -> None:
        self.status = status
        self.refresh_status = refresh_status
        self.retry_calls: list[str] = []
        self.requeue_calls: list[str] = []
        self.fail_calls: list[tuple[str, str]] = []
        self.provider_errors: list[tuple[str, str | None]] = []

    async def summarize_runtime_statuses(self, *, now):  # noqa: ANN001
        del now
        return {
            "queued": 0,
            "in_progress": 0,
            "finalizing": 1,
            "pending_items": 0,
            "in_progress_items": 0,
            "oldest_pending_item_age_seconds": 0.0,
            "oldest_in_progress_item_age_seconds": 0.0,
        }

    async def retry_finalization_now(self, batch_id: str):
        self.retry_calls.append(batch_id)
        return BatchJobRecord(
            batch_id=batch_id,
            endpoint="/v1/embeddings",
            status=BatchJobStatus.FINALIZING,
            execution_mode="managed_internal",
            input_file_id="f1",
            output_file_id=None,
            error_file_id=None,
            model="m1",
            metadata={},
            provider_batch_id=None,
            provider_status=None,
            provider_error=None,
            provider_last_sync_at=None,
            total_items=1,
            in_progress_items=0,
            completed_items=0,
            failed_items=1,
            cancelled_items=0,
            locked_by=None,
            lease_expires_at=None,
            cancel_requested_at=None,
            status_last_updated_at=datetime.now(tz=UTC),
            created_by_api_key="key-a",
            created_by_user_id=None,
            created_by_team_id="team-1",
            created_at=datetime.now(tz=UTC),
            started_at=datetime.now(tz=UTC),
            completed_at=None,
            expires_at=None,
        )

    async def requeue_expired_in_progress_items(self, batch_id: str) -> int:
        self.requeue_calls.append(batch_id)
        return 2

    async def refresh_job_progress(self, batch_id: str):
        return BatchJobRecord(
            batch_id=batch_id,
            endpoint="/v1/embeddings",
            status=self.refresh_status,
            execution_mode="managed_internal",
            input_file_id="f1",
            output_file_id=None,
            error_file_id=None,
            model="m1",
            metadata={},
            provider_batch_id=None,
            provider_status=None,
            provider_error=None,
            provider_last_sync_at=None,
            total_items=1,
            in_progress_items=0,
            completed_items=0,
            failed_items=1,
            cancelled_items=0,
            locked_by=None,
            lease_expires_at=None,
            cancel_requested_at=None,
            status_last_updated_at=datetime.now(tz=UTC),
            created_by_api_key="key-a",
            created_by_user_id=None,
            created_by_team_id="team-1",
            created_at=datetime.now(tz=UTC),
            started_at=datetime.now(tz=UTC),
            completed_at=None,
            expires_at=None,
        )

    async def fail_nonterminal_items(self, *, batch_id: str, reason: str) -> int:
        self.fail_calls.append((batch_id, reason))
        return 3

    async def set_provider_error(self, *, batch_id: str, provider_error: str | None):
        self.provider_errors.append((batch_id, provider_error))
        return None


@pytest.mark.asyncio
async def test_retry_finalization_endpoint(client, test_app, monkeypatch):
    test_app.state.prisma_manager = type("Prisma", (), {"client": _FakeBatchRepairDB(status="finalizing")})()
    test_app.state.batch_repository = _FakeBatchRepairRepository(status="finalizing")

    monkeypatch.setattr(
        "src.api.admin.endpoints.batches.get_auth_scope",
        lambda request, authorization=None, x_master_key=None, required_permission=None: AuthScope(  # noqa: ARG005
            is_platform_admin=True,
            account_id="acct-1",
        ),
    )

    response = await client.post("/ui/api/batches/batch-1/retry-finalization", headers={"Authorization": "Bearer mk-test"})

    assert response.status_code == 200
    assert response.json() == {"batch_id": "batch-1", "status": "finalizing", "retried": True}


@pytest.mark.asyncio
async def test_requeue_stale_endpoint(client, test_app, monkeypatch):
    test_app.state.prisma_manager = type("Prisma", (), {"client": _FakeBatchRepairDB(status="in_progress")})()
    test_app.state.batch_repository = _FakeBatchRepairRepository(status="in_progress", refresh_status="in_progress")

    monkeypatch.setattr(
        "src.api.admin.endpoints.batches.get_auth_scope",
        lambda request, authorization=None, x_master_key=None, required_permission=None: AuthScope(  # noqa: ARG005
            is_platform_admin=True,
            account_id="acct-1",
        ),
    )

    response = await client.post("/ui/api/batches/batch-1/requeue-stale", headers={"Authorization": "Bearer mk-test"})

    assert response.status_code == 200
    assert response.json() == {"batch_id": "batch-1", "status": "in_progress", "requeued_items": 2}


@pytest.mark.asyncio
async def test_retry_finalization_allows_org_scoped_batch_without_team(client, test_app, monkeypatch):
    test_app.state.prisma_manager = type(
        "Prisma",
        (),
        {"client": _FakeBatchRepairDB(status="finalizing", created_by_team_id=None, created_by_organization_id="org-1")},
    )()
    test_app.state.batch_repository = _FakeBatchRepairRepository(status="finalizing")

    monkeypatch.setattr(
        "src.api.admin.endpoints.batches.get_auth_scope",
        lambda request, authorization=None, x_master_key=None, required_permission=None: AuthScope(  # noqa: ARG005
            is_platform_admin=False,
            account_id="acct-1",
            org_ids=["org-1"],
        ),
    )

    response = await client.post("/ui/api/batches/batch-1/retry-finalization", headers={"Authorization": "Bearer mk-test"})

    assert response.status_code == 200
    assert response.json() == {"batch_id": "batch-1", "status": "finalizing", "retried": True}


@pytest.mark.asyncio
async def test_mark_batch_failed_endpoint(client, test_app, monkeypatch):
    test_app.state.prisma_manager = type("Prisma", (), {"client": _FakeBatchRepairDB(status="in_progress")})()
    repository = _FakeBatchRepairRepository(status="in_progress", refresh_status="finalizing")
    test_app.state.batch_repository = repository

    monkeypatch.setattr(
        "src.api.admin.endpoints.batches.get_auth_scope",
        lambda request, authorization=None, x_master_key=None, required_permission=None: AuthScope(  # noqa: ARG005
            is_platform_admin=True,
            account_id="acct-1",
        ),
    )

    response = await client.post(
        "/ui/api/batches/batch-1/mark-failed",
        headers={"Authorization": "Bearer mk-test"},
        json={"reason": "manual stop"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "batch_id": "batch-1",
        "current_status": "finalizing",
        "intended_status": "failed",
        "failed_items": 3,
        "reason": "manual stop",
    }
    assert repository.provider_errors == [("batch-1", encode_operator_failed_reason("manual stop"))]


class _FailingBatchRepairRepository(_FakeBatchRepairRepository):
    async def retry_finalization_now(self, batch_id: str):
        raise RuntimeError("database offline")


class _MetricsRefreshFailingBatchRepairRepository(_FakeBatchRepairRepository):
    async def summarize_runtime_statuses(self, *, now):  # noqa: ANN001
        del now
        raise RuntimeError("metrics unavailable")


@pytest.mark.asyncio
async def test_retry_finalization_records_error_counter_on_repository_failure(client, test_app, monkeypatch):
    test_app.state.prisma_manager = type("Prisma", (), {"client": _FakeBatchRepairDB(status="finalizing")})()
    test_app.state.batch_repository = _FailingBatchRepairRepository(status="finalizing")

    monkeypatch.setattr(
        "src.api.admin.endpoints.batches.get_auth_scope",
        lambda request, authorization=None, x_master_key=None, required_permission=None: AuthScope(  # noqa: ARG005
            is_platform_admin=True,
            account_id="acct-1",
        ),
    )

    before = generate_latest(get_prometheus_registry()).decode("utf-8")
    before_error = _repair_counter_value(before, action="retry_finalization", status="error")

    with pytest.raises(RuntimeError, match="database offline"):
        await client.post(
            "/ui/api/batches/batch-1/retry-finalization",
            headers={"Authorization": "Bearer mk-test"},
        )

    after = generate_latest(get_prometheus_registry()).decode("utf-8")
    after_error = _repair_counter_value(after, action="retry_finalization", status="error")
    assert after_error == before_error + 1.0


@pytest.mark.asyncio
async def test_retry_finalization_succeeds_when_metrics_refresh_fails(client, test_app, monkeypatch):
    test_app.state.prisma_manager = type("Prisma", (), {"client": _FakeBatchRepairDB(status="finalizing")})()
    test_app.state.batch_repository = _MetricsRefreshFailingBatchRepairRepository(status="finalizing")

    monkeypatch.setattr(
        "src.api.admin.endpoints.batches.get_auth_scope",
        lambda request, authorization=None, x_master_key=None, required_permission=None: AuthScope(  # noqa: ARG005
            is_platform_admin=True,
            account_id="acct-1",
        ),
    )

    before = generate_latest(get_prometheus_registry()).decode("utf-8")
    before_success = _repair_counter_value(before, action="retry_finalization", status="success")
    before_error = _repair_counter_value(before, action="retry_finalization", status="error")

    response = await client.post("/ui/api/batches/batch-1/retry-finalization", headers={"Authorization": "Bearer mk-test"})

    assert response.status_code == 200
    assert response.json() == {"batch_id": "batch-1", "status": "finalizing", "retried": True}

    after = generate_latest(get_prometheus_registry()).decode("utf-8")
    after_success = _repair_counter_value(after, action="retry_finalization", status="success")
    after_error = _repair_counter_value(after, action="retry_finalization", status="error")
    assert after_success == before_success + 1.0
    assert after_error == before_error


@pytest.mark.asyncio
async def test_retry_finalization_succeeds_when_metrics_publish_fails(client, test_app, monkeypatch):
    test_app.state.prisma_manager = type("Prisma", (), {"client": _FakeBatchRepairDB(status="finalizing")})()
    test_app.state.batch_repository = _FakeBatchRepairRepository(status="finalizing")

    monkeypatch.setattr(
        "src.api.admin.endpoints.batches.get_auth_scope",
        lambda request, authorization=None, x_master_key=None, required_permission=None: AuthScope(  # noqa: ARG005
            is_platform_admin=True,
            account_id="acct-1",
        ),
    )
    monkeypatch.setattr(
        "src.api.admin.endpoints.batches.publish_batch_runtime_summary",
        lambda summary: (_ for _ in ()).throw(RuntimeError("publish unavailable")),  # noqa: ARG005
    )

    before = generate_latest(get_prometheus_registry()).decode("utf-8")
    before_success = _repair_counter_value(before, action="retry_finalization", status="success")
    before_error = _repair_counter_value(before, action="retry_finalization", status="error")

    response = await client.post("/ui/api/batches/batch-1/retry-finalization", headers={"Authorization": "Bearer mk-test"})

    assert response.status_code == 200
    assert response.json() == {"batch_id": "batch-1", "status": "finalizing", "retried": True}

    after = generate_latest(get_prometheus_registry()).decode("utf-8")
    after_success = _repair_counter_value(after, action="retry_finalization", status="success")
    after_error = _repair_counter_value(after, action="retry_finalization", status="error")
    assert after_success == before_success + 1.0
    assert after_error == before_error


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [["manual stop"], "manual stop"])
async def test_mark_batch_failed_rejects_non_object_body(client, test_app, monkeypatch, payload):
    test_app.state.prisma_manager = type("Prisma", (), {"client": _FakeBatchRepairDB(status="in_progress")})()
    test_app.state.batch_repository = _FakeBatchRepairRepository(status="in_progress", refresh_status="finalizing")

    monkeypatch.setattr(
        "src.api.admin.endpoints.batches.get_auth_scope",
        lambda request, authorization=None, x_master_key=None, required_permission=None: AuthScope(  # noqa: ARG005
            is_platform_admin=True,
            account_id="acct-1",
        ),
    )

    response = await client.post(
        "/ui/api/batches/batch-1/mark-failed",
        headers={"Authorization": "Bearer mk-test"},
        json=payload,
    )

    assert response.status_code == 422


def _repair_counter_value(metrics_text: str, *, action: str, status: str) -> float:
    needle = (
        f'deltallm_batch_repair_actions_total{{action="{action}",status="{status}"}}'
    )
    for line in metrics_text.splitlines():
        if line.startswith(needle):
            return float(line.rsplit(" ", 1)[-1])
    return 0.0
