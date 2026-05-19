from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from prometheus_client import generate_latest

from src.api.admin.endpoints.common import AuthScope
from src.audit.actions import AuditAction
from src.batch.models import BatchJobRecord, BatchJobStatus, BatchSchedulerFlowRecord
from src.batch.models import encode_operator_failed_reason
from src.batch.scheduling import BatchTenantFairShareConfig, scheduler_config_fingerprint
from src.config import AppConfig
from src.config_runtime.dynamic import DynamicConfigPersistenceError, DynamicConfigValidationError
from src.metrics import (
    get_prometheus_registry,
    increment_batch_scheduler_rollback,
    increment_batch_scheduler_shadow_comparison,
)


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
        self.scheduler_backfill_limits: list[int] = []
        self.scheduler_backfill_calls: list[dict[str, object]] = []
        self.refresh_scheduler_flow_calls: list[dict[str, object]] = []
        self.list_scheduler_flow_calls: list[dict[str, object]] = []

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

    async def backfill_scheduler_dimensions(
        self,
        *,
        limit: int,
        service_tier: str | None = None,
        model_group: str | None = None,
    ) -> dict[str, int]:
        self.scheduler_backfill_limits.append(limit)
        self.scheduler_backfill_calls.append(
            {
                "limit": limit,
                "service_tier": service_tier,
                "model_group": model_group,
            }
        )
        return {"jobs": 4, "items": 9}

    async def refresh_scheduler_flows(self, **kwargs):  # noqa: ANN003
        self.refresh_scheduler_flow_calls.append(kwargs)
        return [
            BatchSchedulerFlowRecord(
                flow_id="flow-1",
                service_tier=str(kwargs.get("service_tier") or "standard"),
                model_group=str(kwargs.get("model_group") or "m1"),
                tenant_scope_type="team",
                tenant_scope_id="team-1",
                weight=1,
                quantum_work_units=16,
                deficit_work_units=0,
                active=True,
                queued_jobs=1,
                queued_work_units=2,
                in_flight_work_units=0,
                last_selected_at=None,
                last_refilled_at=None,
                created_at=datetime.now(tz=UTC),
                updated_at=datetime.now(tz=UTC),
                next_item_work_units=3,
                next_batch_id="batch-next",
                next_size_class="xs",
                next_scheduler_rank=1.5,
                next_age_credit_work_units=2,
                next_policy_reason="aging_credit",
            )
        ]

    async def list_scheduler_flows(self, **kwargs):  # noqa: ANN003
        self.list_scheduler_flow_calls.append(kwargs)
        return [
            BatchSchedulerFlowRecord(
                flow_id="flow-1",
                service_tier=str(kwargs.get("service_tier") or "standard"),
                model_group=str(kwargs.get("model_group") or "m1"),
                tenant_scope_type=str(kwargs.get("tenant_scope_type") or "team"),
                tenant_scope_id="team-1",
                weight=1,
                quantum_work_units=16,
                deficit_work_units=0,
                active=True,
                queued_jobs=1,
                queued_work_units=2,
                in_flight_work_units=0,
                last_selected_at=None,
                last_refilled_at=None,
                created_at=datetime.now(tz=UTC),
                updated_at=datetime.now(tz=UTC),
                next_item_work_units=3,
                next_batch_id="batch-next",
                next_size_class="xs",
                next_scheduler_rank=1.5,
                next_age_credit_work_units=2,
                next_policy_reason="aging_credit",
            )
        ]


class _FakeDynamicConfigManager:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.generation = 1
        self.update_calls: list[tuple[dict[str, object], str]] = []
        self.fail_update = False
        self.fail_validation = False

    async def update_config(self, config_update: dict[str, object], updated_by: str) -> None:
        self.update_calls.append((config_update, updated_by))
        if self.fail_validation:
            raise DynamicConfigValidationError("invalid config")
        if self.fail_update:
            raise DynamicConfigPersistenceError("write failed")
        merged = self.config.model_dump(mode="python", exclude_none=True)
        for section, section_update in config_update.items():
            if isinstance(section_update, dict):
                current = merged.setdefault(section, {})
                assert isinstance(current, dict)
                current.update(section_update)
            else:
                merged[section] = section_update
        self.config = AppConfig.model_validate(merged)
        self.generation += 1

    def get_app_config(self) -> AppConfig:
        return self.config

    def get_config_generation(self) -> int:
        return self.generation


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


@pytest.mark.asyncio
async def test_scheduler_status_includes_modes_and_metric_snapshot(client, test_app, monkeypatch):
    test_app.state.app_config = AppConfig.model_validate(
        {
            "general_settings": {
                "embeddings_batch_scheduler_mode": "model_capacity_v1",
                "embeddings_batch_scheduler_shadow_mode": "fair_share_v1",
                "embeddings_batch_scheduler_strict_model_homogeneity_enabled": True,
            }
        }
    )
    test_app.state.dynamic_config_manager = _FakeDynamicConfigManager(test_app.state.app_config)
    increment_batch_scheduler_shadow_comparison(
        active_mode="model_capacity_v1",
        shadow_mode="fair_share_v1",
        result="job_mismatch",
    )
    increment_batch_scheduler_rollback(
        from_mode="fair_share_v1",
        to_mode="model_capacity_v1",
        reason="active_mode_downgrade",
    )

    monkeypatch.setattr(
        "src.api.admin.endpoints.batches.get_auth_scope",
        lambda request, authorization=None, x_master_key=None, required_permission=None: AuthScope(  # noqa: ARG005
            is_platform_admin=True,
            account_id="acct-1",
        ),
    )

    response = await client.get(
        "/ui/api/batches/scheduler/status",
        headers={"Authorization": "Bearer mk-test"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["active_mode"] == "model_capacity_v1"
    assert payload["shadow_mode"] == "fair_share_v1"
    assert len(payload["config"]["hash"]) == 16
    assert payload["config"]["generation"] == 1
    assert payload["local_worker"]["present"] is False
    assert payload["local_worker"]["config_hash"] is None
    assert payload["effective"]["shadow_tenant_fair_share"] is True
    assert payload["fair_share"]["enabled"] is True
    assert payload["metrics"]["scope"] == "process_local"
    assert payload["metrics"]["cluster_wide"] is False
    assert "Prometheus" in payload["metrics"]["warning"]
    assert payload["metrics"]["metric_names"]["rollbacks"] == "deltallm_batch_scheduler_rollbacks_total"
    samples = payload["metrics"]["process_local_samples"]
    assert any(
        sample["labels"] == {
            "active_mode": "model_capacity_v1",
            "shadow_mode": "fair_share_v1",
            "result": "job_mismatch",
        }
        and sample["value"] >= 1.0
        for sample in samples["counters"]["shadow_comparisons"]
    )
    assert any(
        sample["labels"] == {
            "from_mode": "fair_share_v1",
            "to_mode": "model_capacity_v1",
            "reason": "active_mode_downgrade",
        }
        and sample["value"] >= 1.0
        for sample in samples["counters"]["rollbacks"]
    )


@pytest.mark.asyncio
async def test_scheduler_status_reports_worker_config_mismatch(client, test_app, monkeypatch):
    test_app.state.app_config = AppConfig.model_validate(
        {
            "general_settings": {
                "embeddings_batch_scheduler_mode": "fair_share_v1",
                "embeddings_batch_scheduler_shadow_mode": "smart_v1",
            }
        }
    )
    test_app.state.dynamic_config_manager = _FakeDynamicConfigManager(test_app.state.app_config)
    test_app.state.batch_runtime = SimpleNamespace(
        worker=SimpleNamespace(
            config=SimpleNamespace(worker_id="worker-1"),
            _active_scheduler_mode=lambda: "fair_share_v1",
            _shadow_scheduler_mode=lambda: "smart_v1",
            _scheduler_config_hash=lambda: "oldhash",
            _scheduler_config_generation=lambda: 0,
        )
    )
    monkeypatch.setattr(
        "src.api.admin.endpoints.batches.get_auth_scope",
        lambda request, authorization=None, x_master_key=None, required_permission=None: AuthScope(  # noqa: ARG005
            is_platform_admin=True,
            account_id="acct-1",
        ),
    )

    response = await client.get(
        "/ui/api/batches/scheduler/status",
        headers={"Authorization": "Bearer mk-test"},
    )

    assert response.status_code == 200
    local_worker = response.json()["local_worker"]
    assert local_worker["present"] is True
    assert local_worker["worker_id"] == "worker-1"
    assert local_worker["active_mode"] == "fair_share_v1"
    assert local_worker["shadow_mode"] == "smart_v1"
    assert local_worker["config_hash"] == "oldhash"
    assert local_worker["config_generation"] == 0
    assert local_worker["matches_config"] is False


@pytest.mark.asyncio
async def test_scheduler_status_ignores_global_generation_when_scheduler_config_matches(
    client,
    test_app,
    monkeypatch,
):
    test_app.state.app_config = AppConfig.model_validate(
        {
            "general_settings": {
                "embeddings_batch_scheduler_mode": "fair_share_v1",
                "embeddings_batch_scheduler_shadow_mode": "smart_v1",
            }
        }
    )
    config_hash = scheduler_config_fingerprint(test_app.state.app_config.general_settings)
    test_app.state.dynamic_config_manager = _FakeDynamicConfigManager(test_app.state.app_config)
    test_app.state.batch_runtime = SimpleNamespace(
        worker=SimpleNamespace(
            config=SimpleNamespace(worker_id="worker-1"),
            _active_scheduler_mode=lambda: "fair_share_v1",
            _shadow_scheduler_mode=lambda: "smart_v1",
            _scheduler_config_hash=lambda: config_hash,
            _scheduler_config_generation=lambda: 0,
        )
    )
    monkeypatch.setattr(
        "src.api.admin.endpoints.batches.get_auth_scope",
        lambda request, authorization=None, x_master_key=None, required_permission=None: AuthScope(  # noqa: ARG005
            is_platform_admin=True,
            account_id="acct-1",
        ),
    )

    response = await client.get(
        "/ui/api/batches/scheduler/status",
        headers={"Authorization": "Bearer mk-test"},
    )

    assert response.status_code == 200
    local_worker = response.json()["local_worker"]
    assert local_worker["config_generation"] == 0
    assert local_worker["matches_config"] is True


@pytest.mark.asyncio
async def test_scheduler_status_explicit_fifo_rolls_back_legacy_capabilities(
    client,
    test_app,
    monkeypatch,
):
    test_app.state.app_config = AppConfig.model_validate(
        {
            "general_settings": {
                "embeddings_batch_scheduler_mode": "fifo_v1",
                "embeddings_batch_scheduler_shadow_mode": "none",
                "embeddings_batch_model_capacity_enabled": True,
                "embeddings_batch_tenant_fair_share_enabled": True,
                "embeddings_batch_size_aware_scheduling_enabled": True,
            }
        }
    )

    monkeypatch.setattr(
        "src.api.admin.endpoints.batches.get_auth_scope",
        lambda request, authorization=None, x_master_key=None, required_permission=None: AuthScope(  # noqa: ARG005
            is_platform_admin=True,
            account_id="acct-1",
        ),
    )

    response = await client.get(
        "/ui/api/batches/scheduler/status",
        headers={"Authorization": "Bearer mk-test"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["active_mode"] == "fifo_v1"
    assert payload["shadow_mode"] == "none"
    assert payload["effective"]["model_capacity_gates"] is False
    assert payload["effective"]["tenant_fair_share"] is False
    assert payload["effective"]["size_aware_ranking"] is False
    assert payload["model_capacity"]["enabled"] is False
    assert payload["fair_share"]["enabled"] is False
    assert payload["size_aging"]["enabled"] is False


@pytest.mark.asyncio
async def test_scheduler_mode_endpoint_updates_dynamic_config_and_audits(
    client,
    test_app,
    monkeypatch,
) -> None:
    initial_config = AppConfig.model_validate(
        {
            "general_settings": {
                "embeddings_batch_scheduler_mode": "smart_v1",
                "embeddings_batch_scheduler_shadow_mode": "fair_share_v1",
            }
        }
    )
    dynamic_config = _FakeDynamicConfigManager(initial_config)
    test_app.state.app_config = initial_config
    test_app.state.dynamic_config_manager = dynamic_config
    recorded: list[dict[str, object]] = []

    monkeypatch.setattr(
        "src.api.admin.endpoints.batches.get_auth_scope",
        lambda request, authorization=None, x_master_key=None, required_permission=None: AuthScope(  # noqa: ARG005
            is_platform_admin=True,
            account_id="acct-1",
        ),
    )

    async def _capture_audit(**kwargs):  # noqa: ANN003
        recorded.append(kwargs)

    monkeypatch.setattr("src.api.admin.endpoints.batches.emit_admin_mutation_audit", _capture_audit)

    response = await client.post(
        "/ui/api/batches/scheduler/mode",
        headers={"Authorization": "Bearer mk-test"},
        json={
            "active_mode": "fifo_v1",
            "shadow_mode": "none",
            "reason": "rollback",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["active_mode"] == "fifo_v1"
    assert payload["shadow_mode"] == "none"
    assert test_app.state.app_config.general_settings.embeddings_batch_scheduler_mode == "fifo_v1"
    assert dynamic_config.update_calls == [
        (
            {
                "general_settings": {
                    "embeddings_batch_scheduler_mode": "fifo_v1",
                    "embeddings_batch_scheduler_shadow_mode": "none",
                }
            },
            "admin_api",
        )
    ]
    assert recorded[0]["action"] == AuditAction.ADMIN_BATCH_SCHEDULER_MODE_UPDATE
    assert recorded[0]["request_payload"] == {
        "active_mode": "fifo_v1",
        "shadow_mode": "none",
        "reason": "rollback",
    }
    assert recorded[0]["response_payload"] == {
        "active_mode": "fifo_v1",
        "shadow_mode": "none",
    }


@pytest.mark.asyncio
async def test_scheduler_mode_endpoint_fails_closed_when_dynamic_config_write_fails(
    client,
    test_app,
    monkeypatch,
) -> None:
    initial_config = AppConfig.model_validate(
        {
            "general_settings": {
                "embeddings_batch_scheduler_mode": "smart_v1",
                "embeddings_batch_scheduler_shadow_mode": "fair_share_v1",
            }
        }
    )
    dynamic_config = _FakeDynamicConfigManager(initial_config)
    dynamic_config.fail_update = True
    test_app.state.app_config = initial_config
    test_app.state.dynamic_config_manager = dynamic_config
    recorded: list[dict[str, object]] = []

    monkeypatch.setattr(
        "src.api.admin.endpoints.batches.get_auth_scope",
        lambda request, authorization=None, x_master_key=None, required_permission=None: AuthScope(  # noqa: ARG005
            is_platform_admin=True,
            account_id="acct-1",
        ),
    )

    async def _capture_audit(**kwargs):  # noqa: ANN003
        recorded.append(kwargs)

    monkeypatch.setattr("src.api.admin.endpoints.batches.emit_admin_mutation_audit", _capture_audit)

    response = await client.post(
        "/ui/api/batches/scheduler/mode",
        headers={"Authorization": "Bearer mk-test"},
        json={
            "active_mode": "fifo_v1",
            "shadow_mode": "none",
            "reason": "rollback",
        },
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "Scheduler mode update could not be persisted"
    assert test_app.state.app_config.general_settings.embeddings_batch_scheduler_mode == "smart_v1"
    assert recorded == []


@pytest.mark.asyncio
async def test_scheduler_mode_endpoint_rejects_invalid_dynamic_config_before_audit(
    client,
    test_app,
    monkeypatch,
) -> None:
    initial_config = AppConfig.model_validate(
        {
            "general_settings": {
                "embeddings_batch_scheduler_mode": "smart_v1",
                "embeddings_batch_scheduler_shadow_mode": "fair_share_v1",
            }
        }
    )
    dynamic_config = _FakeDynamicConfigManager(initial_config)
    dynamic_config.fail_validation = True
    test_app.state.app_config = initial_config
    test_app.state.dynamic_config_manager = dynamic_config
    recorded: list[dict[str, object]] = []

    monkeypatch.setattr(
        "src.api.admin.endpoints.batches.get_auth_scope",
        lambda request, authorization=None, x_master_key=None, required_permission=None: AuthScope(  # noqa: ARG005
            is_platform_admin=True,
            account_id="acct-1",
        ),
    )

    async def _capture_audit(**kwargs):  # noqa: ANN003
        recorded.append(kwargs)

    monkeypatch.setattr("src.api.admin.endpoints.batches.emit_admin_mutation_audit", _capture_audit)

    response = await client.post(
        "/ui/api/batches/scheduler/mode",
        headers={"Authorization": "Bearer mk-test"},
        json={
            "active_mode": "fifo_v1",
            "shadow_mode": "none",
            "reason": "rollback",
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "Scheduler mode update is invalid"
    assert test_app.state.app_config.general_settings.embeddings_batch_scheduler_mode == "smart_v1"
    assert recorded == []


@pytest.mark.asyncio
async def test_scheduler_dimensions_backfill_endpoint(client, test_app, monkeypatch):
    repository = _FakeBatchRepairRepository()
    test_app.state.batch_repository = repository

    monkeypatch.setattr(
        "src.api.admin.endpoints.batches.get_auth_scope",
        lambda request, authorization=None, x_master_key=None, required_permission=None: AuthScope(  # noqa: ARG005
            is_platform_admin=True,
            account_id="acct-1",
        ),
    )

    response = await client.post(
        "/ui/api/batches/scheduler-dimensions/backfill",
        headers={"Authorization": "Bearer mk-test"},
        json={"limit": 123},
    )

    assert response.status_code == 200
    assert response.json() == {"jobs": 4, "items": 9}
    assert repository.scheduler_backfill_limits == [123]


@pytest.mark.asyncio
async def test_scheduler_flows_get_lists_without_refreshing(client, test_app, monkeypatch):
    repository = _FakeBatchRepairRepository()
    test_app.state.batch_repository = repository
    test_app.state.batch_tenant_fair_share_config = BatchTenantFairShareConfig(enabled=True)

    monkeypatch.setattr(
        "src.api.admin.endpoints.batches.get_auth_scope",
        lambda request, authorization=None, x_master_key=None, required_permission=None: AuthScope(  # noqa: ARG005
            is_platform_admin=True,
            account_id="acct-1",
        ),
    )

    response = await client.get(
        "/ui/api/batches/scheduler/flows?model_group=m1&service_tier=standard&limit=123",
        headers={"Authorization": "Bearer mk-test"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["enabled"] is True
    assert payload["limit"] == 123
    assert payload["data"][0]["model_group"] == "m1"
    assert repository.refresh_scheduler_flow_calls == []
    assert repository.list_scheduler_flow_calls == [
        {
            "service_tier": "standard",
            "model_group": "m1",
            "tenant_scope_type": None,
            "active": None,
            "limit": 123,
        }
    ]


@pytest.mark.asyncio
async def test_scheduler_flows_refresh_endpoint_refreshes_and_audits(client, test_app, monkeypatch):
    repository = _FakeBatchRepairRepository()
    test_app.state.batch_repository = repository
    test_app.state.batch_tenant_fair_share_config = BatchTenantFairShareConfig(
        enabled=True,
        base_quantum_work_units=32,
        max_deficit_multiplier=4,
        max_candidate_jobs_per_flow=17,
    )
    test_app.state.app_config = AppConfig.model_validate(
        {
            "general_settings": {
                "embeddings_batch_scheduler_mode": "smart_v1",
                "embeddings_batch_scheduler_shadow_mode": "none",
                "embeddings_batch_aging_seconds_per_work_unit": 45,
                "embeddings_batch_max_age_credit_work_units": 12,
                "embeddings_batch_min_large_job_claim_interval_seconds": 90,
                "embeddings_batch_small_job_max_work_units": 25,
            }
        }
    )
    recorded: list[dict[str, object]] = []

    monkeypatch.setattr(
        "src.api.admin.endpoints.batches.get_auth_scope",
        lambda request, authorization=None, x_master_key=None, required_permission=None: AuthScope(  # noqa: ARG005
            is_platform_admin=True,
            account_id="acct-1",
        ),
    )

    async def _capture_audit(**kwargs):  # noqa: ANN003
        recorded.append(kwargs)

    monkeypatch.setattr("src.api.admin.endpoints.batches.emit_admin_mutation_audit", _capture_audit)

    response = await client.post(
        "/ui/api/batches/scheduler/flows/refresh",
        headers={"Authorization": "Bearer mk-test"},
        json={"model_group": "m1", "service_tier": "standard"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["refreshed_flows"] == 1
    assert payload["limit"] == 200
    assert payload["repaired_jobs"] == 4
    assert payload["repaired_items"] == 9
    assert payload["repair_skipped"] == 0
    assert payload["data"][0]["tenant_scope_id"].startswith("team:")
    assert payload["data"][0]["next_batch_id"] == "batch-next"
    assert payload["data"][0]["next_scheduler_rank"] == 1.5
    assert payload["data"][0]["next_policy_reason"] == "aging_credit"
    assert repository.scheduler_backfill_limits == [500]
    assert repository.scheduler_backfill_calls == [
        {
            "limit": 500,
            "service_tier": "standard",
            "model_group": "m1",
        }
    ]
    assert repository.refresh_scheduler_flow_calls == [
        {
            "service_tier": "standard",
            "model_group": "m1",
            "base_quantum_work_units": 32,
            "max_deficit_multiplier": 4,
            "max_candidate_jobs_per_flow": 17,
            "size_aware_scheduling_enabled": True,
            "aging_seconds_per_work_unit": 45,
            "max_age_credit_work_units": 12,
            "min_large_job_claim_interval_seconds": 90,
            "small_job_max_work_units": 25,
        }
    ]
    assert repository.list_scheduler_flow_calls[-1] == {
        "service_tier": "standard",
        "model_group": "m1",
        "active": None,
        "limit": 200,
    }
    assert recorded[0]["action"] == AuditAction.ADMIN_BATCH_SCHEDULER_FLOW_REFRESH
    assert recorded[0]["request_payload"] == {
        "model_group": "m1",
        "service_tier": "standard",
        "repair_limit": 500,
        "flow_limit": 200,
    }
    assert recorded[0]["response_payload"] == {
        "repaired_jobs": 4,
        "repaired_items": 9,
        "repair_skipped": 0,
        "refreshed_flows": 1,
    }


@pytest.mark.asyncio
async def test_scheduler_dimensions_backfill_endpoint_reports_lock_skip(client, test_app, monkeypatch):
    class _LockedBackfillRepository(_FakeBatchRepairRepository):
        async def backfill_scheduler_dimensions(
            self,
            *,
            limit: int,
            service_tier: str | None = None,
            model_group: str | None = None,
        ) -> dict[str, int]:
            del service_tier, model_group
            self.scheduler_backfill_limits.append(limit)
            return {"jobs": 0, "items": 0, "skipped": 1}

    repository = _LockedBackfillRepository()
    test_app.state.batch_repository = repository

    monkeypatch.setattr(
        "src.api.admin.endpoints.batches.get_auth_scope",
        lambda request, authorization=None, x_master_key=None, required_permission=None: AuthScope(  # noqa: ARG005
            is_platform_admin=True,
            account_id="acct-1",
        ),
    )

    response = await client.post(
        "/ui/api/batches/scheduler-dimensions/backfill",
        headers={"Authorization": "Bearer mk-test"},
        json={"limit": 123},
    )

    assert response.status_code == 200
    assert response.json() == {"jobs": 0, "items": 0, "skipped": 1}
    assert repository.scheduler_backfill_limits == [123]


@pytest.mark.asyncio
async def test_scheduler_dimensions_backfill_endpoint_rejects_oversized_limit(
    client,
    test_app,
    monkeypatch,
):
    test_app.state.batch_repository = _FakeBatchRepairRepository()
    monkeypatch.setattr(
        "src.api.admin.endpoints.batches.get_auth_scope",
        lambda request, authorization=None, x_master_key=None, required_permission=None: AuthScope(  # noqa: ARG005
            is_platform_admin=True,
            account_id="acct-1",
        ),
    )

    response = await client.post(
        "/ui/api/batches/scheduler-dimensions/backfill",
        headers={"Authorization": "Bearer mk-test"},
        json={"limit": 5001},
    )

    assert response.status_code == 422


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
