from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from src.api.admin.endpoints.batches import _scheduler_flow_response, _scheduler_policy_fields
from src.batch.models import (
    BatchFairShareClaimResult,
    BatchJobStatus,
    BatchSchedulerFlowRecord,
    BatchWorkClaim,
)
from src.batch.repository import BatchRepository
from src.batch.repositories.item_repository import BatchItemRepository
from src.batch.repositories.job_repository import BatchJobRepository, flow_from_row
from src.batch.repositories.maintenance_repository import BatchMaintenanceRepository
from src.batch.repositories.mappers import job_from_row
from src.batch.repositories import job_repository as job_repository_module
import src.batch.scheduling.advisory_locks as advisory_locks_module
from src.batch.scheduling import (
    API_KEY_TENANT_SCOPE_PREFIX,
    MIXED_MODEL_GROUP,
    BatchSizeAgingConfig,
    advisory_lock_key,
    stable_tenant_scope_id,
)


class _PrismaSpy:
    def __init__(self) -> None:
        self.sql = ""
        self.params = ()
        self.queries: list[str] = []
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    async def query_raw(self, sql: str, *params):
        self.sql = sql
        self.params = params
        self.queries.append(sql)
        self.calls.append((sql, params))
        return []


class _LeaseSweepPrisma:
    def __init__(
        self,
        *,
        item_pages: list[int] | None = None,
        job_pages: list[int] | None = None,
        refresh_pages: list[int] | None = None,
    ) -> None:
        self.item_pages = list(item_pages or [])
        self.job_pages = list(job_pages or [])
        self.refresh_pages = list(refresh_pages or [])
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    async def query_raw(self, sql: str, *params):
        self.calls.append((sql, params))
        if "active_items" in sql:
            return [{"active_count": 3}]
        if "active_jobs" in sql:
            return [{"active_count": 4}]
        if "WITH target AS" in sql and "UNNEST($1::text[])" in sql:
            count = self.refresh_pages.pop(0) if self.refresh_pages else len(params[0])
            return [{"batch_id": f"batch-{index}"} for index in range(count)]
        if "UPDATE deltallm_batch_item i" in sql:
            count = self.item_pages.pop(0) if self.item_pages else 0
            return [
                {"item_id": f"item-{index}", "batch_id": f"batch-{index}"}
                for index in range(count)
            ]
        if "UPDATE deltallm_batch_job j" in sql:
            count = self.job_pages.pop(0) if self.job_pages else 0
            return [{"batch_id": f"batch-{index}"} for index in range(count)]
        return []

    @property
    def item_release_calls(self) -> list[tuple[str, tuple[object, ...]]]:
        return [(sql, params) for sql, params in self.calls if "UPDATE deltallm_batch_item i" in sql]

    @property
    def job_release_calls(self) -> list[tuple[str, tuple[object, ...]]]:
        return [
            (sql, params)
            for sql, params in self.calls
            if "UPDATE deltallm_batch_job j" in sql
            and "status IN ('queued', 'in_progress', 'finalizing')" in sql
        ]

    @property
    def job_refresh_calls(self) -> list[tuple[str, tuple[object, ...]]]:
        return [
            (sql, params)
            for sql, params in self.calls
            if "WITH target AS" in sql and "UNNEST($1::text[])" in sql
        ]


@pytest.mark.asyncio
async def test_maintenance_sweep_expired_batch_leases_uses_bounded_skip_locked_queries() -> None:
    prisma = _LeaseSweepPrisma(item_pages=[2, 0], job_pages=[1, 0])
    repository = BatchMaintenanceRepository(prisma)
    now = datetime.now(tz=UTC)

    result = await repository.sweep_expired_batch_leases(
        now=now,
        page_size=10_000,
        max_rows_per_run=10_000,
    )

    assert result == {
        "items": 2,
        "jobs": 1,
        "refreshed_jobs": 2,
        "skipped_active_items": 3,
        "skipped_active_jobs": 4,
    }
    item_update = prisma.item_release_calls[0][0]
    job_update = prisma.job_release_calls[0][0]
    job_refresh = prisma.job_refresh_calls[0][0]
    assert "FOR UPDATE OF i SKIP LOCKED" in item_update
    assert "JOIN deltallm_batch_job j ON j.batch_id = i.batch_id" in item_update
    assert "j.status IN ('queued', 'in_progress', 'finalizing')" in item_update
    assert "locked_by IS NOT NULL" in item_update
    assert "lease_expires_at < $1::timestamp" in item_update
    assert "LIMIT $2" in item_update
    assert "FOR UPDATE SKIP LOCKED" in job_update
    assert "locked_by IS NOT NULL" in job_update
    assert "lease_expires_at < $1::timestamp" in job_update
    assert "LIMIT $2" in job_update
    assert "FOR UPDATE OF j SKIP LOCKED" in job_refresh
    assert prisma.calls[0][1][1] == 1_000
    assert all(params[1] <= 1_000 for _, params in prisma.calls if len(params) > 1)


@pytest.mark.asyncio
async def test_maintenance_sweep_expired_batch_leases_respects_max_rows_per_run() -> None:
    prisma = _LeaseSweepPrisma(item_pages=[1], job_pages=[1])
    repository = BatchMaintenanceRepository(prisma)

    result = await repository.sweep_expired_batch_leases(
        now=datetime.now(tz=UTC),
        page_size=100,
        max_rows_per_run=1,
    )

    assert result["items"] == 1
    assert result["jobs"] == 0
    assert result["refreshed_jobs"] == 1
    assert not prisma.job_release_calls


@pytest.mark.asyncio
async def test_maintenance_sweep_expired_batch_leases_uses_single_row_budget_for_jobs() -> None:
    prisma = _LeaseSweepPrisma(item_pages=[0], job_pages=[1])
    repository = BatchMaintenanceRepository(prisma)

    result = await repository.sweep_expired_batch_leases(
        now=datetime.now(tz=UTC),
        page_size=100,
        max_rows_per_run=1,
    )

    assert result["items"] == 0
    assert result["jobs"] == 1
    assert result["refreshed_jobs"] == 0
    assert prisma.item_release_calls[0][1][1] == 1
    assert prisma.job_release_calls[0][1][1] == 1


@pytest.mark.asyncio
async def test_maintenance_sweep_expired_batch_leases_splits_budget_between_items_and_jobs() -> None:
    prisma = _LeaseSweepPrisma(item_pages=[5], job_pages=[5])
    repository = BatchMaintenanceRepository(prisma)

    result = await repository.sweep_expired_batch_leases(
        now=datetime.now(tz=UTC),
        page_size=100,
        max_rows_per_run=10,
    )

    assert result["items"] == 5
    assert result["jobs"] == 5
    assert result["refreshed_jobs"] == 5
    assert prisma.item_release_calls[0][1][1] == 5
    assert prisma.job_release_calls[0][1][1] == 5


@pytest.mark.asyncio
async def test_bulk_item_completion_requires_worker_owner() -> None:
    prisma = _PrismaSpy()
    repository = BatchItemRepository(prisma)

    updated = await repository.mark_items_completed_bulk(
        items=[
            {
                "item_id": "item-1",
                "response_body": {"ok": True},
                "usage": {},
                "provider_cost": 0.0,
                "billed_cost": 0.0,
            }
        ],
        worker_id=None,
    )

    assert updated is False
    assert prisma.calls == []


@pytest.mark.asyncio
async def test_bulk_item_completion_requires_claim_epoch() -> None:
    prisma = _PrismaSpy()
    repository = BatchItemRepository(prisma)

    updated = await repository.mark_items_completed_bulk(
        items=[
            {
                "item_id": "item-1",
                "response_body": {"ok": True},
                "usage": {},
                "provider_cost": 0.0,
                "billed_cost": 0.0,
            }
        ],
        worker_id="worker-1",
    )

    assert updated is False
    assert prisma.calls == []


@pytest.mark.asyncio
async def test_complete_items_with_outbox_counts_not_owned_completion_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, int | str]] = []
    monkeypatch.setattr(
        "src.batch.repository.increment_batch_duplicate_completion_rejection",
        lambda *, reason, count=1: calls.append({"reason": reason, "count": count}),
    )

    class _NotOwnedRepository(BatchRepository):
        async def mark_items_completed_bulk(self, *, items, worker_id):  # noqa: ANN001
            del items, worker_id
            return False

        async def list_items_by_ids(self, item_ids):  # noqa: ANN001
            del item_ids
            return []

        async def list_completion_outbox_by_item_ids(self, item_ids):  # noqa: ANN001
            del item_ids
            return []

    repository = _NotOwnedRepository()

    result = await repository.complete_items_with_outbox_bulk(
        items=[
            {
                "item_id": "item-1",
                "claim_epoch": 1,
                "response_body": {"ok": True},
                "usage": {},
                "provider_cost": 0.0,
                "billed_cost": 0.0,
                "outbox_payload": {"batch_id": "batch-1"},
            },
            {
                "item_id": "item-2",
                "claim_epoch": 1,
                "response_body": {"ok": True},
                "usage": {},
                "provider_cost": 0.0,
                "billed_cost": 0.0,
                "outbox_payload": {"batch_id": "batch-1"},
            },
        ],
        worker_id="worker-1",
    )

    assert result == "not_owned"
    assert calls == [{"reason": "not_owned", "count": 2}]


@pytest.mark.asyncio
async def test_complete_items_with_outbox_does_not_count_idempotent_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, int | str]] = []
    monkeypatch.setattr(
        "src.batch.repository.increment_batch_duplicate_completion_rejection",
        lambda *, reason, count=1: calls.append({"reason": reason, "count": count}),
    )

    class _AlreadyCompletedRepository(BatchRepository):
        async def mark_items_completed_bulk(self, *, items, worker_id):  # noqa: ANN001
            del items, worker_id
            return False

        async def list_items_by_ids(self, item_ids):  # noqa: ANN001
            return [SimpleNamespace(item_id=item_id, status="completed") for item_id in item_ids]

        async def list_completion_outbox_by_item_ids(self, item_ids):  # noqa: ANN001
            return [SimpleNamespace(item_id=item_id) for item_id in item_ids]

    repository = _AlreadyCompletedRepository()

    result = await repository.complete_items_with_outbox_bulk(
        items=[
            {
                "item_id": "item-1",
                "claim_epoch": 1,
                "response_body": {"ok": True},
                "usage": {},
                "provider_cost": 0.0,
                "billed_cost": 0.0,
                "outbox_payload": {"batch_id": "batch-1"},
            }
        ],
        worker_id="worker-1",
    )

    assert result == "already_completed"
    assert calls == []


def _job_row(**overrides):
    row = {
        "batch_id": "batch-1",
        "endpoint": "/v1/embeddings",
        "status": "queued",
        "execution_mode": "managed_internal",
        "input_file_id": "file-1",
        "model": "m1",
        "metadata": "{}",
        "total_items": 2,
        "created_by_api_key": None,
        "created_by_user_id": None,
        "created_by_team_id": None,
        "created_by_organization_id": None,
        "created_at": datetime.now(tz=UTC),
        "scheduler_version": "fifo_v1",
        "scheduling_model": "m1",
        "scheduling_model_group": "m1",
        "scheduling_endpoint": "/v1/embeddings",
        "tenant_scope_type": "anonymous",
        "tenant_scope_id": "anonymous",
        "service_tier": "standard",
        "estimated_work_units": 2,
        "remaining_work_units": 2,
        "size_class": "xs",
        "queue_entered_at": datetime.now(tz=UTC),
        "scheduler_debug": "{}",
    }
    row.update(overrides)
    return row


def _scheduler_flow(**overrides) -> BatchSchedulerFlowRecord:
    now = datetime.now(tz=UTC)
    values = {
        "flow_id": "flow-1",
        "service_tier": "standard",
        "model_group": "model-a",
        "tenant_scope_type": "team",
        "tenant_scope_id": "team-1",
        "weight": 1,
        "quantum_work_units": 16,
        "deficit_work_units": 16,
        "active": True,
        "queued_jobs": 1,
        "queued_work_units": 10,
        "in_flight_work_units": 0,
        "last_selected_at": None,
        "last_refilled_at": None,
        "created_at": now,
        "updated_at": now,
        "oldest_queue_entered_at": now,
        "next_item_work_units": 1,
        "skip_reasons": {},
    }
    values.update(overrides)
    return BatchSchedulerFlowRecord(**values)


def _scheduler_flow_snapshot():
    return job_repository_module._SchedulerFlowRefreshSnapshot(
        service_tier="standard",
        model_group="model-a",
        aggregates=(),
        legacy_api_key_scope_repairs={},
    )


def test_scheduler_flow_snapshot_merge_uses_final_in_flight_state_for_selection() -> None:
    now = datetime.now(tz=UTC)
    candidate_snapshot = job_repository_module._SchedulerFlowRefreshSnapshot(
        service_tier="standard",
        model_group="model-a",
        aggregates=(
            job_repository_module._SchedulerFlowRefreshAggregate(
                service_tier="standard",
                model_group="model-a",
                tenant_scope_type="team",
                tenant_scope_id="team-a",
                queued_jobs=1,
                queued_work_units=1,
                in_flight_work_units=0,
                oldest_queue_entered_at=now,
                next_item_work_units=1,
                next_batch_id="batch-a",
            ),
        ),
        legacy_api_key_scope_repairs={"sk-in-flight": "api_key:stable-in-flight"},
    )
    in_flight_snapshot = job_repository_module._SchedulerFlowRefreshSnapshot(
        service_tier="standard",
        model_group="model-a",
        aggregates=(
            job_repository_module._SchedulerFlowRefreshAggregate(
                service_tier="standard",
                model_group="model-a",
                tenant_scope_type="team",
                tenant_scope_id="team-a",
                in_flight_work_units=32,
            ),
        ),
        legacy_api_key_scope_repairs={},
    )

    merged = BatchJobRepository._merge_scheduler_flow_snapshots(
        candidate_snapshot,
        in_flight_snapshot,
    )

    assert len(merged.aggregates) == 1
    aggregate = merged.aggregates[0]
    assert aggregate.queued_jobs == 1
    assert aggregate.next_batch_id == "batch-a"
    assert aggregate.in_flight_work_units == 32
    assert merged.legacy_api_key_scope_repairs == {
        "sk-in-flight": "api_key:stable-in-flight",
    }

    flow = BatchJobRepository._preview_flow_from_refresh_aggregate(
        aggregate,
        existing=None,
        base_quantum_work_units=16,
        max_deficit_multiplier=8,
        now=now,
    )
    selection = BatchJobRepository._select_scheduler_flow(
        [flow],
        max_work_units=1,
        tenant_max_in_flight_work_units=32,
        allow_deficit_bypass=False,
    )

    assert selection.selected is None
    assert selection.skip_reasons == {flow.flow_id: "tenant_in_flight_full"}


@pytest.mark.asyncio
async def test_scheduler_flow_in_flight_snapshot_merges_legacy_api_key_scope_rows() -> None:
    raw_api_key = "sk-legacy"
    stable_api_key_scope = stable_tenant_scope_id(scope_type="api_key", scope_id=raw_api_key)

    class _Db:
        async def query_raw(self, sql: str, *params):
            assert "i.status = 'in_progress'" in sql
            assert params == ("model-a", "standard")
            return [
                {
                    "service_tier": "standard",
                    "model_group": "model-a",
                    "tenant_scope_type": "api_key",
                    "tenant_scope_id": raw_api_key,
                    "in_flight_work_units": 3,
                },
                {
                    "service_tier": "standard",
                    "model_group": "model-a",
                    "tenant_scope_type": "api_key",
                    "tenant_scope_id": stable_api_key_scope,
                    "in_flight_work_units": 5,
                },
            ]

    snapshot = await BatchJobRepository()._load_scheduler_flow_in_flight_snapshot(
        _Db(),
        service_tier="standard",
        model_group="model-a",
    )

    assert len(snapshot.aggregates) == 1
    aggregate = snapshot.aggregates[0]
    assert aggregate.tenant_scope_type == "api_key"
    assert aggregate.tenant_scope_id == stable_api_key_scope
    assert aggregate.in_flight_work_units == 8
    assert snapshot.legacy_api_key_scope_repairs == {raw_api_key: stable_api_key_scope}


def test_batch_repository_with_prisma_preserves_tenant_scope_preference() -> None:
    repository = BatchRepository(
        prisma_client=object(),
        tenant_scope_preference="api_key,team,organization",
    )

    tx_repository = repository.with_prisma(object())

    assert tx_repository.tenant_scope_preference == ("api_key", "team", "organization")
    assert tx_repository.maintenance.tenant_scope_preference == (
        "api_key",
        "team",
        "organization",
    )


@pytest.mark.asyncio
async def test_list_jobs_uses_or_scope_for_api_key_and_team():
    prisma = _PrismaSpy()
    repository = BatchRepository(prisma_client=prisma)

    jobs = await repository.list_jobs(
        limit=20,
        created_by_api_key="key-1",
        created_by_team_id="team-1",
    )

    assert jobs == []
    assert "created_by_api_key" in prisma.sql
    assert "created_by_team_id" in prisma.sql
    assert " OR " in prisma.sql


@pytest.mark.asyncio
async def test_list_jobs_can_filter_by_organization_scope() -> None:
    prisma = _PrismaSpy()
    repository = BatchRepository(prisma_client=prisma)

    jobs = await repository.list_jobs(
        limit=20,
        created_by_api_key="key-1",
        created_by_organization_id="org-1",
    )

    assert jobs == []
    assert "created_by_api_key" in prisma.sql
    assert "created_by_organization_id" in prisma.sql
    assert " OR " in prisma.sql


@pytest.mark.asyncio
async def test_claim_items_reclaims_expired_in_progress_rows():
    prisma = _PrismaSpy()
    repository = BatchRepository(prisma_client=prisma)

    items = await repository.claim_items(
        batch_id="batch-1",
        worker_id="worker-1",
        limit=10,
        lease_seconds=120,
    )

    assert items == []
    assert "status = 'pending'" in prisma.sql
    assert "status = 'in_progress'" in prisma.sql
    assert "lease_expires_at < NOW()" in prisma.sql
    assert "not_before_at IS NULL OR not_before_at <= NOW()" in prisma.sql


@pytest.mark.asyncio
async def test_claim_items_honors_not_before_at_for_pending_rows() -> None:
    prisma = _PrismaSpy()
    repository = BatchRepository(prisma_client=prisma)

    await repository.claim_items(
        batch_id="batch-1",
        worker_id="worker-1",
        limit=10,
        lease_seconds=120,
    )

    assert "status = 'pending'" in prisma.sql
    assert "AND (not_before_at IS NULL OR not_before_at <= NOW())" in prisma.sql
    assert "OR (status = 'in_progress' AND lease_expires_at < NOW())" in prisma.sql


@pytest.mark.asyncio
async def test_claim_next_work_uses_item_slice_claim_shape() -> None:
    prisma = _PrismaSpy()
    repository = BatchRepository(prisma_client=prisma)

    claim = await repository.claim_next_work(
        worker_id="worker-1",
        max_items=10,
        max_work_units=25,
        lease_seconds=120,
    )

    assert claim is None
    assert "j.status IN ('queued', 'in_progress')" in prisma.sql
    assert "j.locked_by IS NULL OR j.lease_expires_at IS NULL OR j.lease_expires_at < NOW()" in prisma.sql
    assert "ORDER BY j.last_scheduled_at ASC NULLS FIRST" in prisma.sql
    assert "WITH selected_job AS" in prisma.sql
    assert "pg_advisory_xact_lock" not in prisma.sql
    assert "FOR KEY SHARE SKIP LOCKED" in prisma.sql
    # Single-job selection: no candidate_jobs CTE and no per-candidate seed lock.
    assert "WITH candidate_jobs AS" not in prisma.sql
    assert "claimable_jobs" not in prisma.sql
    assert "LIMIT GREATEST($2 - 1, 0)" not in prisma.sql
    # locked_items operates on the single selected_job.
    assert "i.status = 'pending'" in prisma.sql
    assert "i.not_before_at IS NULL OR i.not_before_at <= NOW()" in prisma.sql
    assert "FOR UPDATE SKIP LOCKED" in prisma.sql
    assert "LIMIT $2" in prisma.sql
    assert "SUM(estimated_work_units) OVER" in prisma.sql
    # locked_items LIMIT $2 enforces the max-items cap, eligible_items only
    # enforces the work-unit cap with claim_rank = 1 as the at-least-one fallback.
    assert "claim_rank <= $2" not in prisma.sql
    assert "cumulative_work_units <= $3 OR claim_rank = 1" in prisma.sql
    # updated_items must depend on updated_job for finalize-race symmetry.
    assert "FROM eligible_items s, updated_job uj" in prisma.sql
    assert "locked_by = $1" in prisma.sql
    assert "locked_by = NULL" in prisma.sql
    assert "first_claimed_at = COALESCE" in prisma.sql
    assert "queue_wait_observed" in prisma.sql


@pytest.mark.asyncio
async def test_recommend_next_work_uses_read_only_shadow_query() -> None:
    prisma = _PrismaSpy()
    repository = BatchRepository(prisma_client=prisma)

    recommendation = await repository.recommend_next_work(
        max_items=4,
        max_work_units=9,
        allowed_model_groups=["model-a"],
        service_tier="standard",
        claim_order="fifo",
        capacity_model_group="model-a",
        capacity_service_tier="standard",
        capacity_max_in_flight_items=8,
        capacity_max_in_flight_work_units=24,
        allow_oversized_first_item=False,
        reason="model_capacity_v1",
    )

    assert recommendation is None
    assert "WITH" in prisma.sql
    assert "selected_job AS" in prisma.sql
    assert "capacity_state AS" in prisma.sql
    assert "ORDER BY COALESCE(j.queue_entered_at, j.created_at) ASC" in prisma.sql
    assert (
        "head_item.estimated_work_units <= LEAST($2, "
        "(SELECT remaining_work_units FROM capacity_state))"
    ) in prisma.sql
    assert "UPDATE " not in prisma.sql
    assert "FOR UPDATE" not in prisma.sql
    assert "FOR KEY SHARE" not in prisma.sql
    assert "locked_by = $" not in prisma.sql


@pytest.mark.asyncio
async def test_recommend_next_work_fifo_shadow_does_not_apply_legacy_filter() -> None:
    prisma = _PrismaSpy()
    repository = BatchRepository(prisma_client=prisma)

    recommendation = await repository.recommend_next_work(
        max_items=4,
        max_work_units=9,
        claim_order="fifo",
        reason="fifo_v1",
    )

    assert recommendation is None
    assert "ORDER BY COALESCE(j.queue_entered_at, j.created_at) ASC" in prisma.sql
    assert "COALESCE(NULLIF(j.scheduler_version, ''), 'fifo_v1') = 'fifo_v1'" not in prisma.sql
    assert "j.scheduling_model_group IS NULL OR j.scheduling_model_group = ''" not in prisma.sql


@pytest.mark.asyncio
async def test_claim_next_work_can_filter_selected_model_group_and_service_tier() -> None:
    prisma = _PrismaSpy()
    repository = BatchRepository(prisma_client=prisma)

    claim = await repository.claim_next_work(
        worker_id="worker-1",
        max_items=10,
        max_work_units=25,
        lease_seconds=120,
        allowed_model_groups=["model-a"],
        service_tier="standard",
    )

    assert claim is None
    assert "j.scheduling_model_group = ANY($5::text[])" in prisma.sql
    assert "j.service_tier = $6" in prisma.sql
    assert prisma.params[4] == ["model-a"]
    assert prisma.params[5] == "standard"


@pytest.mark.asyncio
async def test_claim_next_work_can_use_fifo_order_for_capacity_claims() -> None:
    prisma = _PrismaSpy()
    repository = BatchRepository(prisma_client=prisma)

    claim = await repository.claim_next_work(
        worker_id="worker-1",
        max_items=10,
        max_work_units=25,
        lease_seconds=120,
        allowed_model_groups=["model-a"],
        service_tier="standard",
        claim_order="fifo",
    )

    assert claim is None
    assert "COALESCE(j.queue_entered_at, j.created_at) ASC" in prisma.sql
    assert "j.batch_id ASC" in prisma.sql
    assert "ORDER BY j.last_scheduled_at ASC NULLS FIRST" not in prisma.sql


@pytest.mark.asyncio
async def test_claim_next_work_can_rank_jobs_by_size_and_aging() -> None:
    prisma = _PrismaSpy()
    repository = BatchRepository(prisma_client=prisma)

    claim = await repository.claim_next_work(
        worker_id="worker-1",
        max_items=10,
        max_work_units=25,
        lease_seconds=120,
        allowed_model_groups=["model-a"],
        service_tier="standard",
        claim_order="size_aging",
        size_aware_scheduling_enabled=True,
        aging_seconds_per_work_unit=15,
        max_age_credit_work_units=200,
        min_large_job_claim_interval_seconds=45,
        small_job_max_work_units=50,
        work_claim_min_items_for_microbatch=4,
    )

    assert claim is None
    assert "scheduler_rank" in prisma.sql
    assert "age_credit_work_units" in prisma.sql
    assert "large_job_progress_floor DESC" in prisma.sql
    assert "next_policy_reason" in prisma.sql
    assert "jsonb_build_object" in prisma.sql
    assert "LEAST($2, (SELECT selected_max_items FROM selected_job))" in prisma.sql
    assert "COALESCE(j.queue_entered_at, j.created_at) ASC" in prisma.sql
    assert prisma.params[-5:] == (15, 200, 45, 50, 4)


@pytest.mark.asyncio
async def test_claim_next_work_capacity_guard_rechecks_in_flight_under_model_lock() -> None:
    class _Prisma:
        def __init__(self) -> None:
            self.tx_client = _PrismaSpy()
            self.tx_entered = False

        @asynccontextmanager
        async def tx(self):
            self.tx_entered = True
            yield self.tx_client

        async def query_raw(self, sql: str, *params):  # pragma: no cover
            del sql, params
            raise AssertionError("capacity claim must use the transaction client")

    prisma = _Prisma()
    repository = BatchRepository(prisma_client=prisma)

    claim = await repository.claim_next_work(
        worker_id="worker-1",
        max_items=10,
        max_work_units=25,
        lease_seconds=120,
        allowed_model_groups=["model-a"],
        service_tier="standard",
        claim_order="fifo",
        capacity_model_group="model-a",
        capacity_service_tier="standard",
        capacity_max_in_flight_items=2,
        capacity_max_in_flight_work_units=30,
        allow_oversized_first_item=False,
    )

    assert claim is None
    assert prisma.tx_entered is True
    assert len(prisma.tx_client.calls) == 2
    lock_sql, lock_params = prisma.tx_client.calls[0]
    claim_sql, claim_params = prisma.tx_client.calls[1]
    assert "pg_advisory_xact_lock" in lock_sql
    assert "SELECT 1::int AS locked" in lock_sql
    assert "hashtext($1)" in lock_sql
    assert "$3::bigint" in lock_sql
    assert lock_params == (
        "model-a",
        "standard",
        advisory_lock_key("batch_model_capacity", "standard", "model-a"),
    )
    assert "capacity_lock AS" not in claim_sql
    assert "capacity_usage AS" in claim_sql
    assert "capacity_state AS" in claim_sql
    assert "capacity_item.status = 'in_progress'" in claim_sql
    assert "capacity_item.lease_expires_at IS NULL" in claim_sql
    assert "capacity_item.lease_expires_at > NOW()" in claim_sql
    assert "AS in_flight_work_units" in claim_sql
    assert "AND (SELECT remaining_slots FROM capacity_state) > 0" in claim_sql
    assert "AND (SELECT remaining_work_units FROM capacity_state) > 0" in claim_sql
    assert "LIMIT LEAST($2, (SELECT remaining_slots FROM capacity_state))" in claim_sql
    assert "WHERE cumulative_work_units <= LEAST($3, (SELECT remaining_work_units FROM capacity_state))" in claim_sql
    assert "head_item.estimated_work_units <= LEAST($3, (SELECT remaining_work_units FROM capacity_state))" in claim_sql
    assert "OR claim_rank = 1" not in claim_sql
    assert claim_params[6] == "model-a"
    assert claim_params[7] == "standard"
    assert claim_params[8] == 2
    assert claim_params[9] == 30


@pytest.mark.asyncio
async def test_model_capacity_lock_uses_execute_raw_to_avoid_void_deserialization() -> None:
    class _ExecPrisma:
        def __init__(self) -> None:
            self.execute_calls: list[tuple[str, tuple[object, ...]]] = []

        async def execute_raw(self, sql: str, *params):
            self.execute_calls.append((sql, params))
            return 0

        async def query_raw(self, sql: str, *params):  # pragma: no cover
            del sql, params
            raise AssertionError("blocking advisory lock should not be read through query_raw")

    prisma = _ExecPrisma()
    repository = BatchJobRepository()

    await repository._acquire_model_capacity_lock(
        prisma,
        model_group="model-a",
        service_tier="standard",
    )

    assert len(prisma.execute_calls) == 2
    legacy_sql, legacy_params = prisma.execute_calls[0]
    canonical_sql, canonical_params = prisma.execute_calls[1]
    assert "pg_advisory_xact_lock(hashtext($1), hashtext($2))" in legacy_sql
    assert legacy_params == ("model-a", "standard")
    assert "pg_advisory_xact_lock($1::bigint)" in canonical_sql
    assert canonical_params == (
        advisory_lock_key("batch_model_capacity", "standard", "model-a"),
    )


@pytest.mark.asyncio
async def test_claim_next_work_capacity_guard_keeps_work_units_per_claim_when_no_work_unit_cap() -> None:
    class _Prisma:
        def __init__(self) -> None:
            self.tx_client = _PrismaSpy()

        @asynccontextmanager
        async def tx(self):
            yield self.tx_client

    prisma = _Prisma()
    repository = BatchRepository(prisma_client=prisma)

    claim = await repository.claim_next_work(
        worker_id="worker-1",
        max_items=10,
        max_work_units=25,
        lease_seconds=120,
        allowed_model_groups=["model-a"],
        service_tier="standard",
        claim_order="fifo",
        capacity_model_group="model-a",
        capacity_service_tier="standard",
        capacity_max_in_flight_items=16,
        allow_oversized_first_item=False,
    )

    assert claim is None
    assert len(prisma.tx_client.calls) == 2
    claim_sql, claim_params = prisma.tx_client.calls[1]
    assert "capacity_state AS" in claim_sql
    assert "$9::int AS remaining_work_units" not in claim_sql
    assert "$3::int AS remaining_work_units" in claim_sql
    assert "WHERE cumulative_work_units <= LEAST($3, (SELECT remaining_work_units FROM capacity_state))" in claim_sql
    assert "head_item.estimated_work_units <= LEAST($3, (SELECT remaining_work_units FROM capacity_state))" in claim_sql
    assert len(claim_params) == 9
    assert claim_params[8] == 16


@pytest.mark.asyncio
async def test_claim_next_work_can_filter_to_tenant_flow() -> None:
    prisma = _PrismaSpy()
    repository = BatchRepository(prisma_client=prisma)

    claim = await repository.claim_next_work(
        worker_id="worker-1",
        max_items=10,
        max_work_units=25,
        lease_seconds=120,
        allowed_model_groups=["model-a"],
        service_tier="standard",
        tenant_scope_type="team",
        tenant_scope_id="team-1",
    )

    assert claim is None
    assert "AND j.tenant_scope_type = $7 AND j.tenant_scope_id = $8" in prisma.sql
    assert prisma.params[6] == "team"
    assert prisma.params[7] == "team-1"


def test_select_scheduler_flow_uses_oversized_fallback_for_single_flow() -> None:
    flow = _scheduler_flow(next_item_work_units=10, deficit_work_units=128)

    selection = BatchJobRepository._select_scheduler_flow(
        [flow],
        max_work_units=4,
        tenant_max_in_flight_work_units=0,
        allow_deficit_bypass=False,
    )

    assert selection.selected == flow
    assert selection.selected_uses_oversized_first_item is True
    assert selection.skip_reasons == {}
    assert selection.active_flow_count == 1
    assert selection.eligible_flow_count == 1

    fallback_selection = BatchJobRepository._select_scheduler_flow(
        [flow],
        max_work_units=4,
        tenant_max_in_flight_work_units=0,
        allow_deficit_bypass=True,
    )
    assert fallback_selection.selected == flow
    assert fallback_selection.skip_reasons == {}
    assert fallback_selection.single_eligible_flow is True
    assert fallback_selection.selected_uses_oversized_first_item is True


def test_select_scheduler_flow_allows_oversized_flow_at_capped_deficit() -> None:
    now = datetime.now(tz=UTC)
    fitting = _scheduler_flow(
        flow_id="flow-fitting",
        tenant_scope_id="team-fitting",
        next_item_work_units=1,
        deficit_work_units=1,
        last_selected_at=now,
    )
    oversized = _scheduler_flow(
        flow_id="flow-oversized",
        tenant_scope_id="team-oversized",
        next_item_work_units=10,
        quantum_work_units=1,
        deficit_work_units=4,
        oldest_queue_entered_at=now,
    )

    selection = BatchJobRepository._select_scheduler_flow(
        [fitting, oversized],
        max_work_units=1,
        tenant_max_in_flight_work_units=0,
        allow_deficit_bypass=False,
        max_deficit_multiplier=4,
    )

    assert selection.selected == oversized
    assert selection.selected_uses_oversized_first_item is True
    assert selection.skip_reasons == {}


def test_select_scheduler_flow_respects_tenant_in_flight_remaining_budget() -> None:
    fitting = _scheduler_flow(
        flow_id="flow-fitting",
        in_flight_work_units=31,
        next_item_work_units=1,
        deficit_work_units=16,
    )
    too_large = _scheduler_flow(
        flow_id="flow-too-large",
        in_flight_work_units=31,
        next_item_work_units=2,
        deficit_work_units=16,
    )

    selection = BatchJobRepository._select_scheduler_flow(
        [too_large],
        max_work_units=16,
        tenant_max_in_flight_work_units=32,
        allow_deficit_bypass=True,
    )
    assert selection.selected is None
    assert selection.skip_reasons == {too_large.flow_id: "tenant_in_flight_full"}
    assert selection.eligible_flow_count == 0

    selection = BatchJobRepository._select_scheduler_flow(
        [fitting],
        max_work_units=16,
        tenant_max_in_flight_work_units=32,
        allow_deficit_bypass=False,
    )
    assert selection.selected == fitting
    assert selection.skip_reasons == {}
    assert selection.single_eligible_flow is True
    assert (
        BatchJobRepository._flow_claim_work_units(
            fitting,
            max_work_units=16,
            tenant_max_in_flight_work_units=32,
            single_eligible_flow=True,
        )
        == 1
    )


def test_select_scheduler_flow_allows_work_conserving_borrow_from_capped_tenant() -> None:
    borrower = _scheduler_flow(
        flow_id="flow-borrower",
        deficit_work_units=1,
        next_item_work_units=1,
        queued_work_units=50,
    )
    capped = _scheduler_flow(
        flow_id="flow-capped",
        tenant_scope_id="team-capped",
        in_flight_work_units=32,
        deficit_work_units=16,
        next_item_work_units=1,
        queued_work_units=50,
    )

    selection = BatchJobRepository._select_scheduler_flow(
        [borrower, capped],
        max_work_units=16,
        tenant_max_in_flight_work_units=32,
        allow_deficit_bypass=False,
    )

    assert selection.selected == borrower
    assert selection.skip_reasons == {capped.flow_id: "tenant_in_flight_full"}
    assert selection.active_flow_count == 2
    assert selection.eligible_flow_count == 1
    assert (
        BatchJobRepository._flow_claim_work_units(
            borrower,
            max_work_units=16,
            tenant_max_in_flight_work_units=32,
            single_eligible_flow=selection.single_eligible_flow,
        )
        == 16
    )


@pytest.mark.asyncio
async def test_record_scheduler_flow_skip_reasons_emits_bounded_metrics(monkeypatch: pytest.MonkeyPatch) -> None:
    class _SkipReasonPrisma:
        def __init__(self) -> None:
            self.sql = ""
            self.params: tuple[object, ...] = ()

        async def query_raw(self, sql: str, *params):
            self.sql = sql
            self.params = params
            return []

    recorded: list[str] = []
    monkeypatch.setattr(
        job_repository_module,
        "increment_batch_scheduler_flow_skip",
        lambda *, reason: recorded.append(reason),
    )
    prisma = _SkipReasonPrisma()
    repository = BatchJobRepository()

    await repository._record_scheduler_flow_skip_reasons(
        prisma,
        {
            "flow-a": "tenant_in_flight_full",
            "flow-b": "oversized_head_item",
            "flow-c": "not-a-public-reason",
        },
    )

    assert recorded == ["tenant_in_flight_full", "oversized_head_item", "unknown"]
    assert prisma.params == (
        "flow-a",
        "tenant_in_flight_full",
        "flow-b",
        "oversized_head_item",
        "flow-c",
        "unknown",
    )


def test_flow_from_row_maps_durable_skip_reason_summary() -> None:
    flow = flow_from_row(
        {
            "flow_id": "flow-1",
            "service_tier": "standard",
            "model_group": "model-a",
            "tenant_scope_type": "team",
            "tenant_scope_id": "team-1",
            "weight": 1,
            "quantum_work_units": 16,
            "deficit_work_units": 0,
            "active": True,
            "queued_jobs": 1,
            "queued_work_units": 1,
            "in_flight_work_units": 0,
            "skip_reason_summary": {"tenant_in_flight_full": 2, "oversized_head_item": 1},
        }
    )

    assert flow.skip_reasons == {"tenant_in_flight_full": 2, "oversized_head_item": 1}


def test_scheduler_flow_admin_response_includes_skip_reason_summary() -> None:
    flow = _scheduler_flow(skip_reasons={"tenant_in_flight_full": 2})

    response = _scheduler_flow_response(flow)

    assert response["skip_reason_summary"] == {"tenant_in_flight_full": 2}


def test_scheduler_flow_admin_response_includes_next_candidate_fields() -> None:
    flow = _scheduler_flow(
        next_item_work_units=3,
        next_batch_id="batch-live",
        next_size_class="xs",
        next_scheduler_rank=1.25,
        next_age_credit_work_units=4,
        next_policy_reason="aging_credit",
    )

    response = _scheduler_flow_response(flow)

    assert response["next_item_work_units"] == 3
    assert response["next_batch_id"] == "batch-live"
    assert response["next_size_class"] == "xs"
    assert response["next_scheduler_rank"] == 1.25
    assert response["next_age_credit_work_units"] == 4
    assert response["next_policy_reason"] == "aging_credit"


def test_scheduler_policy_fields_compute_live_rank_without_debug() -> None:
    fields = _scheduler_policy_fields(
        {
            "queue_entered_at": datetime.now(tz=UTC) - timedelta(seconds=90),
            "last_scheduled_at": None,
            "remaining_work_units": 100,
            "estimated_work_units": 100,
            "total_items": 1,
            "size_class": "s",
            "scheduler_debug": {},
        }
    )

    assert fields["age_seconds"] is not None
    assert fields["age_credit_work_units"] == 3
    assert fields["scheduler_rank"] == pytest.approx(97.0, abs=0.01)
    assert fields["next_policy_reason"] == "aging_credit"


def test_scheduler_policy_fields_use_size_aging_config_for_live_rank() -> None:
    fields = _scheduler_policy_fields(
        {
            "queue_entered_at": datetime.now(tz=UTC) - timedelta(seconds=90),
            "last_scheduled_at": None,
            "remaining_work_units": 100,
            "estimated_work_units": 100,
            "total_items": 1,
            "size_class": "s",
            "scheduler_debug": {},
        },
        size_aging_config=BatchSizeAgingConfig(
            enabled=True,
            aging_seconds_per_work_unit=60,
            max_age_credit_work_units=1_000,
            min_large_job_claim_interval_seconds=300,
            small_job_max_work_units=25,
        ),
    )

    assert fields["age_credit_work_units"] == 1
    assert fields["scheduler_rank"] == pytest.approx(98.5, abs=0.01)
    assert fields["next_policy_reason"] == "aging_credit"


@pytest.mark.parametrize(
    ("scope_type", "scope_id"),
    [
        ("organization", "org-sensitive-123"),
        ("team", "team-sensitive-123"),
        ("user", "user-sensitive-123"),
    ],
)
def test_scheduler_flow_admin_response_redacts_non_api_key_tenant_ids(
    scope_type: str,
    scope_id: str,
) -> None:
    flow = _scheduler_flow(tenant_scope_type=scope_type, tenant_scope_id=scope_id)

    response = _scheduler_flow_response(flow)

    assert response["tenant_scope_id"].startswith(f"{scope_type}:")
    assert scope_id not in response["tenant_scope_id"]


@pytest.mark.asyncio
async def test_list_scheduler_flows_applies_optional_bounded_limit() -> None:
    prisma = _PrismaSpy()
    repository = BatchRepository(prisma_client=prisma)

    flows = await repository.list_scheduler_flows(
        service_tier="standard",
        model_group="m1",
        tenant_scope_type="team",
        active=True,
        limit=5_000,
    )

    assert flows == []
    assert "WHERE service_tier = $1 AND model_group = $2 AND tenant_scope_type = $3 AND active = $4" in prisma.sql
    assert "ORDER BY service_tier ASC" in prisma.sql
    assert "LIMIT $5" in prisma.sql
    assert prisma.params == ("standard", "m1", "team", True, 1000)


@pytest.mark.asyncio
async def test_refresh_scheduler_flows_upserts_durable_flow_state() -> None:
    now = datetime.now(tz=UTC)

    class _FlowPrisma:
        def __init__(self) -> None:
            self.calls: list[tuple[str, tuple[object, ...]]] = []

        async def query_raw(self, sql: str, *params):
            self.calls.append((sql, params))
            if "WITH base_jobs AS" in sql:
                return [
                    {
                        "service_tier": "standard",
                        "model_group": "model-a",
                        "tenant_scope_type": "team",
                        "tenant_scope_id": "team-1",
                        "queued_jobs": 2,
                        "queued_work_units": 7,
                        "in_flight_work_units": 3,
                        "oldest_queue_entered_at": now,
                        "next_item_work_units": 2,
                    }
                ]
            if "INSERT INTO deltallm_batch_scheduler_flow" in sql:
                return [
                    {
                        "flow_id": params[0],
                        "service_tier": params[1],
                        "model_group": params[2],
                        "tenant_scope_type": params[3],
                        "tenant_scope_id": params[4],
                        "weight": params[5],
                        "quantum_work_units": params[6],
                        "deficit_work_units": 0,
                        "active": params[7],
                        "queued_jobs": params[8],
                        "queued_work_units": params[9],
                        "in_flight_work_units": params[10],
                        "last_selected_at": None,
                        "last_refilled_at": None,
                        "created_at": now,
                        "updated_at": now,
                        "oldest_queue_entered_at": params[13],
                        "next_item_work_units": params[14],
                    }
                ]
            return []

    prisma = _FlowPrisma()
    repository = BatchRepository(prisma_client=prisma)

    flows = await repository.refresh_scheduler_flows(
        service_tier="standard",
        model_group="model-a",
        base_quantum_work_units=16,
        max_deficit_multiplier=8,
    )

    assert len(flows) == 1
    flow = flows[0]
    assert flow.model_group == "model-a"
    assert flow.tenant_scope_type == "team"
    assert flow.queued_work_units == 7
    assert flow.in_flight_work_units == 3
    assert flow.quantum_work_units == 16
    refresh_sql = prisma.calls[0][0]
    assert "runnable_job_backlog AS" in refresh_sql
    assert "SUM(GREATEST(COALESCE(i.estimated_work_units, 1), 1))" in refresh_sql
    assert "SUM(remaining_work_units)" in refresh_sql
    assert "FROM base_jobs" in refresh_sql
    assert "FROM flow_jobs" in refresh_sql
    assert "FROM candidate_seed_jobs c" in refresh_sql
    assert refresh_sql.index("candidate_seed_jobs AS") < refresh_sql.index("JOIN LATERAL")
    assert "ON CONFLICT" in prisma.calls[1][0]
    assert "weight = GREATEST(deltallm_batch_scheduler_flow.weight, 1)" in prisma.calls[1][0]
    assert (
        "active = true OR queued_jobs <> 0 OR queued_work_units <> 0 OR in_flight_work_units <> 0"
        in prisma.calls[2][0]
    )
    assert prisma.calls[2][1][0] == [flow.flow_id]


@pytest.mark.asyncio
async def test_refresh_scheduler_flows_uses_size_aware_candidate_order() -> None:
    prisma = _PrismaSpy()
    repository = BatchRepository(prisma_client=prisma)

    flows = await repository.refresh_scheduler_flows(
        service_tier="standard",
        model_group="model-a",
        size_aware_scheduling_enabled=True,
        aging_seconds_per_work_unit=15,
        max_age_credit_work_units=200,
        min_large_job_claim_interval_seconds=45,
        small_job_max_work_units=50,
    )

    assert flows == []
    refresh_sql, refresh_params = prisma.calls[0]
    assert "ARRAY_AGG(next_item_work_units ORDER BY" in refresh_sql
    assert "large_job_progress_floor DESC" in refresh_sql
    assert "scheduler_rank ASC" in refresh_sql
    assert "GREATEST(0.0" in refresh_sql
    assert refresh_params == ("model-a", "standard", 50, 15, 200, 45, 50)


@pytest.mark.asyncio
async def test_refresh_scheduler_flows_normalizes_legacy_api_key_scope_ids() -> None:
    now = datetime.now(tz=UTC)
    raw_api_key = "sk-legacy-key"
    stable_scope_id = stable_tenant_scope_id(scope_type="api_key", scope_id=raw_api_key)

    class _ApiKeyFlowPrisma:
        def __init__(self) -> None:
            self.calls: list[tuple[str, tuple[object, ...]]] = []

        async def query_raw(self, sql: str, *params):
            self.calls.append((sql, params))
            if "WITH base_jobs AS" in sql:
                return [
                    {
                        "service_tier": "standard",
                        "model_group": "model-a",
                        "tenant_scope_type": "api_key",
                        "tenant_scope_id": raw_api_key,
                        "queued_jobs": 1,
                        "queued_work_units": 2,
                        "in_flight_work_units": 0,
                        "oldest_queue_entered_at": now + timedelta(seconds=1),
                        "next_item_work_units": 2,
                    },
                    {
                        "service_tier": "standard",
                        "model_group": "model-a",
                        "tenant_scope_type": "api_key",
                        "tenant_scope_id": stable_scope_id,
                        "queued_jobs": 1,
                        "queued_work_units": 3,
                        "in_flight_work_units": 4,
                        "oldest_queue_entered_at": now,
                        "next_item_work_units": 3,
                    },
                ]
            if "INSERT INTO deltallm_batch_scheduler_flow" in sql:
                return [
                    {
                        "flow_id": params[0],
                        "service_tier": params[1],
                        "model_group": params[2],
                        "tenant_scope_type": params[3],
                        "tenant_scope_id": params[4],
                        "weight": params[5],
                        "quantum_work_units": params[6],
                        "deficit_work_units": 0,
                        "active": params[7],
                        "queued_jobs": params[8],
                        "queued_work_units": params[9],
                        "in_flight_work_units": params[10],
                        "last_selected_at": None,
                        "last_refilled_at": None,
                        "created_at": now,
                        "updated_at": now,
                        "oldest_queue_entered_at": params[13],
                        "next_item_work_units": params[14],
                    }
                ]
            return []

    prisma = _ApiKeyFlowPrisma()
    repository = BatchRepository(prisma_client=prisma)

    flows = await repository.refresh_scheduler_flows(
        service_tier="standard",
        model_group="model-a",
        base_quantum_work_units=16,
        max_deficit_multiplier=8,
    )

    assert len(flows) == 1
    assert flows[0].tenant_scope_type == "api_key"
    assert flows[0].tenant_scope_id == stable_scope_id
    assert flows[0].queued_jobs == 2
    assert flows[0].queued_work_units == 5
    assert flows[0].in_flight_work_units == 4
    assert flows[0].oldest_queue_entered_at == now
    assert flows[0].next_item_work_units == 3
    repair_calls = [call for call in prisma.calls if "UPDATE deltallm_batch_job j" in call[0]]
    assert len(repair_calls) == 1
    assert repair_calls[0][1][:3] == (
        raw_api_key,
        stable_scope_id,
        f"{API_KEY_TENANT_SCOPE_PREFIX}%",
    )
    assert "j.scheduling_model_group = $4" in repair_calls[0][0]
    assert "COALESCE(NULLIF(j.service_tier, ''), 'standard') = $5" in repair_calls[0][0]
    upsert_calls = [call for call in prisma.calls if "INSERT INTO deltallm_batch_scheduler_flow" in call[0]]
    assert len(upsert_calls) == 1
    assert upsert_calls[0][1][4] == stable_scope_id
    assert prisma.calls[-1][1][0] == [flows[0].flow_id]


@pytest.mark.asyncio
async def test_preview_scheduler_flows_reads_live_state_without_writes() -> None:
    now = datetime.now(tz=UTC)

    class _PreviewFlowPrisma:
        def __init__(self) -> None:
            self.calls: list[tuple[str, tuple[object, ...]]] = []

        async def query_raw(self, sql: str, *params):
            self.calls.append((sql, params))
            normalized_sql = " ".join(sql.split())
            assert "INSERT INTO" not in normalized_sql
            assert "UPDATE " not in normalized_sql
            assert "pg_try_advisory_xact_lock" not in normalized_sql
            if "WITH base_jobs AS" in sql:
                return [
                    {
                        "service_tier": "standard",
                        "model_group": "model-a",
                        "tenant_scope_type": "team",
                        "tenant_scope_id": "team-1",
                        "queued_jobs": 2,
                        "queued_work_units": 7,
                        "in_flight_work_units": 3,
                        "oldest_queue_entered_at": now,
                        "next_item_work_units": 2,
                        "next_batch_id": "batch-live",
                        "next_size_class": "xs",
                        "next_scheduler_rank": 1.25,
                        "next_age_credit_work_units": 2,
                        "next_policy_reason": "aging_credit",
                    }
                ]
            if "FROM deltallm_batch_scheduler_flow" in sql:
                return [
                    {
                        "flow_id": "durable-flow",
                        "service_tier": "standard",
                        "model_group": "model-a",
                        "tenant_scope_type": "team",
                        "tenant_scope_id": "team-1",
                        "weight": 4,
                        "quantum_work_units": 64,
                        "deficit_work_units": 20,
                        "active": True,
                        "queued_jobs": 99,
                        "queued_work_units": 99,
                        "in_flight_work_units": 99,
                        "skip_reason_summary": {"insufficient_deficit": 1},
                        "last_selected_at": now - timedelta(seconds=30),
                        "last_refilled_at": now - timedelta(seconds=60),
                        "created_at": now - timedelta(minutes=5),
                        "updated_at": now - timedelta(minutes=1),
                        "oldest_queue_entered_at": None,
                        "next_item_work_units": 1,
                    }
                ]
            return []

    prisma = _PreviewFlowPrisma()
    repository = BatchJobRepository(prisma)

    flows = await repository.preview_scheduler_flows(
        service_tier="standard",
        model_group="model-a",
        base_quantum_work_units=16,
        max_deficit_multiplier=8,
        size_aware_scheduling_enabled=True,
    )

    assert len(flows) == 1
    flow = flows[0]
    assert flow.flow_id == "durable-flow"
    assert flow.weight == 4
    assert flow.quantum_work_units == 64
    assert flow.deficit_work_units == 20
    assert flow.queued_jobs == 2
    assert flow.queued_work_units == 7
    assert flow.in_flight_work_units == 3
    assert flow.next_batch_id == "batch-live"
    assert flow.next_policy_reason == "aging_credit"
    assert flow.skip_reasons == {"insufficient_deficit": 1}
    assert len(prisma.calls) == 2


@pytest.mark.asyncio
async def test_refresh_scheduler_flows_preserves_existing_weight_for_quantum() -> None:
    now = datetime.now(tz=UTC)

    class _WeightedFlowPrisma:
        def __init__(self) -> None:
            self.calls: list[tuple[str, tuple[object, ...]]] = []

        async def query_raw(self, sql: str, *params):
            self.calls.append((sql, params))
            if "WITH base_jobs AS" in sql:
                return [
                    {
                        "service_tier": "standard",
                        "model_group": "model-a",
                        "tenant_scope_type": "team",
                        "tenant_scope_id": "team-1",
                        "queued_jobs": 4,
                        "queued_work_units": 4,
                        "in_flight_work_units": 0,
                        "oldest_queue_entered_at": now,
                        "next_item_work_units": 1,
                    }
                ]
            if "INSERT INTO deltallm_batch_scheduler_flow" in sql:
                return [
                    {
                        "flow_id": params[0],
                        "service_tier": params[1],
                        "model_group": params[2],
                        "tenant_scope_type": params[3],
                        "tenant_scope_id": params[4],
                        "weight": 4,
                        "quantum_work_units": 64,
                        "deficit_work_units": 0,
                        "active": params[7],
                        "queued_jobs": params[8],
                        "queued_work_units": params[9],
                        "in_flight_work_units": params[10],
                        "last_selected_at": None,
                        "last_refilled_at": None,
                        "created_at": now,
                        "updated_at": now,
                        "oldest_queue_entered_at": params[13],
                        "next_item_work_units": params[14],
                    }
                ]
            return []

    prisma = _WeightedFlowPrisma()
    repository = BatchRepository(prisma_client=prisma)

    flows = await repository.refresh_scheduler_flows(
        service_tier="standard",
        model_group="model-a",
        base_quantum_work_units=16,
        max_deficit_multiplier=8,
    )

    assert len(flows) == 1
    assert flows[0].weight == 4
    assert flows[0].quantum_work_units == 64
    refresh_sql, refresh_params = prisma.calls[1]
    assert "$12::int * GREATEST(deltallm_batch_scheduler_flow.weight, 1)" in refresh_sql
    assert refresh_params[11] == 16
    assert refresh_params[12] == 8


@pytest.mark.asyncio
async def test_upsert_scheduler_flow_for_job_preserves_existing_weight_for_quantum() -> None:
    now = datetime.now(tz=UTC)

    class _UpsertFlowPrisma:
        def __init__(self) -> None:
            self.calls: list[tuple[str, tuple[object, ...]]] = []

        async def query_raw(self, sql: str, *params):
            self.calls.append((sql, params))
            return [
                {
                    "flow_id": params[0],
                    "service_tier": params[1],
                    "model_group": params[2],
                    "tenant_scope_type": params[3],
                    "tenant_scope_id": params[4],
                    "weight": 4,
                    "quantum_work_units": 64,
                    "deficit_work_units": 0,
                    "active": params[7],
                    "queued_jobs": params[8],
                    "queued_work_units": params[9],
                    "in_flight_work_units": 0,
                    "last_selected_at": None,
                    "last_refilled_at": None,
                    "created_at": now,
                    "updated_at": now,
                }
            ]

    prisma = _UpsertFlowPrisma()
    repository = BatchJobRepository(prisma)
    job = job_from_row(
        _job_row(
            scheduling_model_group="model-a",
            tenant_scope_type="team",
            tenant_scope_id="team-1",
            remaining_work_units=4,
        )
    )

    flow = await repository.upsert_scheduler_flow_for_job(
        job,
        base_quantum_work_units=16,
        max_deficit_multiplier=8,
    )

    assert flow is not None
    assert flow.weight == 4
    assert flow.quantum_work_units == 64
    upsert_sql, upsert_params = prisma.calls[0]
    assert "weight = GREATEST(deltallm_batch_scheduler_flow.weight, 1)" in upsert_sql
    assert "$11::int * GREATEST(deltallm_batch_scheduler_flow.weight, 1)" in upsert_sql
    assert upsert_params[10] == 16
    assert upsert_params[11] == 8


@pytest.mark.asyncio
async def test_get_tenant_queued_work_units_counts_admission_backlog() -> None:
    class _QueuedWorkPrisma(_PrismaSpy):
        async def query_raw(self, sql: str, *params):
            self.sql = sql
            self.params = params
            self.calls.append((sql, params))
            return [{"queued_work_units": 7}]

    prisma = _QueuedWorkPrisma()
    repository = BatchRepository(prisma_client=prisma)

    queued_work_units = await repository.get_tenant_queued_work_units(
        tenant_scope_type="team",
        tenant_scope_id="team-1",
    )

    assert queued_work_units == 7
    assert "FROM deltallm_batch_item i" in prisma.sql
    assert "JOIN deltallm_batch_job j ON j.batch_id = i.batch_id" in prisma.sql
    assert "i.status = 'pending'" in prisma.sql
    assert "i.status = 'in_progress' AND i.lease_expires_at < NOW()" in prisma.sql
    assert "i.not_before_at" not in prisma.sql
    assert "remaining_work_units" not in prisma.sql
    assert prisma.params == ("team", "team-1", "", "", "", "")


@pytest.mark.asyncio
async def test_get_tenant_queued_work_units_counts_legacy_raw_api_key_backlog() -> None:
    class _QueuedWorkPrisma(_PrismaSpy):
        async def query_raw(self, sql: str, *params):
            self.sql = sql
            self.params = params
            self.calls.append((sql, params))
            return [{"queued_work_units": 11}]

    prisma = _QueuedWorkPrisma()
    repository = BatchRepository(prisma_client=prisma)

    queued_work_units = await repository.get_tenant_queued_work_units(
        tenant_scope_type="api_key",
        tenant_scope_id=stable_tenant_scope_id(scope_type="api_key", scope_id="sk-legacy-key"),
        created_by_api_key="sk-legacy-key",
    )

    assert queued_work_units == 11
    assert "j.tenant_scope_id = $3" in prisma.sql
    assert "j.created_by_api_key = $3" in prisma.sql
    assert prisma.params == (
        "api_key",
        stable_tenant_scope_id(scope_type="api_key", scope_id="sk-legacy-key"),
        "sk-legacy-key",
        "",
        "",
        "",
    )


@pytest.mark.asyncio
async def test_get_tenant_queued_work_units_counts_owner_rows_during_scope_migration() -> None:
    class _QueuedWorkPrisma(_PrismaSpy):
        async def query_raw(self, sql: str, *params):
            self.sql = sql
            self.params = params
            self.calls.append((sql, params))
            return [{"queued_work_units": 13}]

    prisma = _QueuedWorkPrisma()
    repository = BatchRepository(prisma_client=prisma)

    queued_work_units = await repository.get_tenant_queued_work_units(
        tenant_scope_type="api_key",
        tenant_scope_id=stable_tenant_scope_id(scope_type="api_key", scope_id="sk-migrating-key"),
        created_by_api_key="sk-migrating-key",
    )

    assert queued_work_units == 13
    assert "j.tenant_scope_type = $1" in prisma.sql
    assert "j.created_by_api_key = $3" in prisma.sql
    assert prisma.params == (
        "api_key",
        stable_tenant_scope_id(scope_type="api_key", scope_id="sk-migrating-key"),
        "sk-migrating-key",
        "",
        "",
        "",
    )


@pytest.mark.asyncio
async def test_get_tenant_queued_work_units_requires_raw_key_for_stable_api_key_scope() -> None:
    prisma = _PrismaSpy()
    repository = BatchRepository(prisma_client=prisma)

    with pytest.raises(ValueError, match="created_by_api_key is required"):
        await repository.get_tenant_queued_work_units(
            tenant_scope_type="api_key",
            tenant_scope_id=stable_tenant_scope_id(scope_type="api_key", scope_id="sk-legacy-key"),
        )

    assert prisma.calls == []


@pytest.mark.asyncio
async def test_scheduler_flow_refresh_snapshot_bounds_candidate_jobs_per_flow() -> None:
    prisma = _PrismaSpy()
    repository = BatchJobRepository()

    snapshot = await repository._load_scheduler_flow_refresh_snapshot(
        prisma,
        service_tier="standard",
        model_group="model-a",
        max_candidate_jobs_per_flow=7,
    )

    assert snapshot.aggregates == ()
    assert "queued AS" in prisma.sql
    assert "runnable_job_backlog AS" in prisma.sql
    assert "flow_jobs AS" in prisma.sql
    assert "SUM(GREATEST(COALESCE(i.estimated_work_units, 1), 1))" in prisma.sql
    assert "SUM(remaining_work_units)" in prisma.sql
    assert "ranked_candidate_seed_jobs AS" in prisma.sql
    assert "candidate_seed_jobs AS" in prisma.sql
    assert "candidate_jobs AS" in prisma.sql
    assert "ROW_NUMBER() OVER" in prisma.sql
    assert "flow_candidate_rank <= $3::int" in prisma.sql
    assert prisma.sql.index("candidate_seed_jobs AS") < prisma.sql.index("JOIN LATERAL")
    assert prisma.params == ("model-a", "standard", 7)


def test_scheduler_flow_selection_reports_scan_limit_when_bound_hides_candidates() -> None:
    now = datetime.now(tz=UTC)
    blocked = _scheduler_flow(
        flow_id="flow-blocked",
        deficit_work_units=0,
        next_item_work_units=4,
        queued_work_units=4,
        oldest_queue_entered_at=now,
    )
    eligible = _scheduler_flow(
        flow_id="flow-eligible",
        deficit_work_units=4,
        next_item_work_units=4,
        queued_work_units=4,
        oldest_queue_entered_at=now + timedelta(seconds=1),
    )

    selection = BatchJobRepository._select_scheduler_flow(
        [blocked, eligible],
        max_work_units=4,
        tenant_max_in_flight_work_units=0,
        allow_deficit_bypass=False,
        max_active_flows_per_decision=1,
    )

    assert selection.selected is None
    assert selection.active_flow_count == 2
    assert selection.scanned_flow_count == 1
    assert selection.scan_limit_reached is True
    assert selection.skip_reasons == {"flow-blocked": "insufficient_deficit"}
    assert (
        BatchJobRepository._terminal_empty_scheduler_flow_result(selection)
        == "flow_scan_limit_reached"
    )


def test_scheduler_flow_selection_does_not_use_single_flow_bypass_when_scan_is_bounded() -> None:
    now = datetime.now(tz=UTC)
    selected = _scheduler_flow(
        flow_id="flow-selected",
        deficit_work_units=4,
        next_item_work_units=4,
        queued_work_units=4,
        oldest_queue_entered_at=now,
    )
    hidden = _scheduler_flow(
        flow_id="flow-hidden",
        deficit_work_units=16,
        next_item_work_units=4,
        queued_work_units=4,
        oldest_queue_entered_at=now + timedelta(seconds=1),
    )

    selection = BatchJobRepository._select_scheduler_flow(
        [selected, hidden],
        max_work_units=16,
        tenant_max_in_flight_work_units=0,
        allow_deficit_bypass=False,
        max_active_flows_per_decision=1,
    )

    assert selection.selected is not None
    assert selection.selected.flow_id == "flow-selected"
    assert selection.active_flow_count == 2
    assert selection.scanned_flow_count == 1
    assert selection.scan_limit_reached is True
    assert selection.single_eligible_flow is False
    assert (
        BatchJobRepository._flow_claim_work_units(
            selection.selected,
            max_work_units=16,
            tenant_max_in_flight_work_units=0,
            single_eligible_flow=selection.single_eligible_flow,
        )
        == 4
    )


@pytest.mark.asyncio
async def test_fair_share_claim_reports_tenant_in_flight_full_when_all_active_flows_are_capped() -> None:
    flow = _scheduler_flow(
        flow_id="flow-full",
        in_flight_work_units=32,
        queued_work_units=1,
        next_item_work_units=1,
        deficit_work_units=16,
    )

    class _TxPrisma:
        @asynccontextmanager
        async def tx(self):
            yield object()

    class _TenantFullRepository(BatchJobRepository):
        def __init__(self) -> None:
            super().__init__(_TxPrisma())
            self.recorded_skip_reasons: list[dict[str, str]] = []
            self.refills = 0

        async def _try_acquire_scheduler_flow_lock(self, db, **kwargs):  # noqa: ANN001, ANN003
            del db, kwargs
            return True

        async def _load_scheduler_flow_refresh_snapshot(self, *args, **kwargs):  # noqa: ANN002, ANN003
            del args, kwargs
            return _scheduler_flow_snapshot()

        async def _load_scheduler_flow_in_flight_snapshot(self, *args, **kwargs):  # noqa: ANN002, ANN003
            del args, kwargs
            return _scheduler_flow_snapshot()

        async def _refresh_scheduler_flows_from_snapshot(self, **kwargs):  # noqa: ANN003
            del kwargs
            return [flow]

        async def _record_scheduler_flow_skip_reasons(self, db, skip_reasons):  # noqa: ANN001
            del db
            self.recorded_skip_reasons.append(dict(skip_reasons))
            return []

        async def _refill_scheduler_flow_deficits(self, db, **kwargs):  # noqa: ANN001, ANN003
            del db, kwargs
            self.refills += 1
            return 1

    repository = _TenantFullRepository()

    result = await repository.claim_next_fair_share_work(
        worker_id="worker-1",
        service_tier="standard",
        model_group="model-a",
        max_items=4,
        max_work_units=16,
        lease_seconds=120,
        tenant_max_in_flight_work_units=32,
        max_deficit_multiplier=1,
    )

    assert result.claim is None
    assert result.result == "tenant_in_flight_full"
    assert repository.refills == 0
    assert repository.recorded_skip_reasons == [{"flow-full": "tenant_in_flight_full"}]


@pytest.mark.asyncio
async def test_fair_share_claim_retries_next_flow_after_empty_selected_flow() -> None:
    now = datetime.now(tz=UTC)
    empty_flow = _scheduler_flow(
        flow_id="flow-empty",
        tenant_scope_id="team-empty",
        oldest_queue_entered_at=now,
    )
    next_flow = _scheduler_flow(
        flow_id="flow-next",
        tenant_scope_id="team-next",
        oldest_queue_entered_at=now + timedelta(seconds=1),
    )

    class _TxPrisma:
        @asynccontextmanager
        async def tx(self):
            yield object()

    class _RetryRepository(BatchJobRepository):
        def __init__(self) -> None:
            super().__init__(_TxPrisma())
            self.claimed_flow_ids: list[str] = []
            self.recorded_skip_reasons: list[dict[str, str]] = []

        async def _try_acquire_scheduler_flow_lock(self, db, **kwargs):  # noqa: ANN001, ANN003
            del db, kwargs
            return True

        async def _load_scheduler_flow_refresh_snapshot(self, *args, **kwargs):  # noqa: ANN002, ANN003
            del args, kwargs
            return _scheduler_flow_snapshot()

        async def _load_scheduler_flow_in_flight_snapshot(self, *args, **kwargs):  # noqa: ANN002, ANN003
            del args, kwargs
            return _scheduler_flow_snapshot()

        async def _refresh_scheduler_flows_from_snapshot(self, **kwargs):  # noqa: ANN003
            del kwargs
            return [empty_flow, next_flow]

        async def _record_scheduler_flow_skip_reasons(self, db, skip_reasons):  # noqa: ANN001
            del db
            self.recorded_skip_reasons.append(dict(skip_reasons))
            return []

        async def _refill_scheduler_flow_deficits(self, db, **kwargs):  # noqa: ANN001, ANN003
            del db, kwargs
            return 0

        async def _claim_scheduler_flow_with_client(self, db, *, flow, **kwargs):  # noqa: ANN001, ANN003
            del db, kwargs
            self.claimed_flow_ids.append(flow.flow_id)
            if flow.flow_id == "flow-empty":
                return BatchFairShareClaimResult(claim=None, result="empty_flow", flow=flow)
            return BatchFairShareClaimResult(
                claim=BatchWorkClaim(
                    claim_id="claim-next",
                    worker_id="worker-1",
                    batch_id="batch-next",
                    endpoint="/v1/embeddings",
                    model_group=flow.model_group,
                    tenant_scope_type=flow.tenant_scope_type,
                    tenant_scope_id=flow.tenant_scope_id,
                    service_tier=flow.service_tier,
                    item_ids=["item-1"],
                    claimed_work_units=1,
                    lease_expires_at=now + timedelta(seconds=60),
                ),
                result="claimed",
                flow=flow,
            )

    repository = _RetryRepository()

    result = await repository.claim_next_fair_share_work(
        worker_id="worker-1",
        service_tier="standard",
        model_group="model-a",
        max_items=4,
        max_work_units=16,
        lease_seconds=120,
    )

    assert result.claim is not None
    assert result.claim.batch_id == "batch-next"
    assert repository.claimed_flow_ids == ["flow-empty", "flow-next"]
    assert {"flow-empty": "empty_flow"} in repository.recorded_skip_reasons


@pytest.mark.asyncio
async def test_fair_share_claim_reuses_refreshed_flows_after_deficit_refill() -> None:
    now = datetime.now(tz=UTC)
    first_flow = _scheduler_flow(
        flow_id="flow-first",
        tenant_scope_id="team-first",
        deficit_work_units=0,
        quantum_work_units=4,
        next_item_work_units=4,
        queued_work_units=4,
        oldest_queue_entered_at=now,
    )
    second_flow = _scheduler_flow(
        flow_id="flow-second",
        tenant_scope_id="team-second",
        deficit_work_units=0,
        quantum_work_units=4,
        next_item_work_units=4,
        queued_work_units=4,
        oldest_queue_entered_at=now + timedelta(seconds=1),
    )

    class _TxPrisma:
        @asynccontextmanager
        async def tx(self):
            yield object()

    class _RefillRepository(BatchJobRepository):
        def __init__(self) -> None:
            super().__init__(_TxPrisma())
            self.refresh_calls = 0
            self.refills = 0
            self.claimed_deficits: list[int] = []

        async def _try_acquire_scheduler_flow_lock(self, db, **kwargs):  # noqa: ANN001, ANN003
            del db, kwargs
            return True

        async def _load_scheduler_flow_refresh_snapshot(self, *args, **kwargs):  # noqa: ANN002, ANN003
            del args, kwargs
            return _scheduler_flow_snapshot()

        async def _load_scheduler_flow_in_flight_snapshot(self, *args, **kwargs):  # noqa: ANN002, ANN003
            del args, kwargs
            return _scheduler_flow_snapshot()

        async def _refresh_scheduler_flows_from_snapshot(self, **kwargs):  # noqa: ANN003
            del kwargs
            self.refresh_calls += 1
            return [first_flow, second_flow]

        async def _record_scheduler_flow_skip_reasons(self, db, skip_reasons):  # noqa: ANN001
            del db, skip_reasons
            return []

        async def _refill_scheduler_flow_deficits(self, db, **kwargs):  # noqa: ANN001, ANN003
            del db, kwargs
            self.refills += 1
            return 2

        async def _claim_scheduler_flow_with_client(self, db, *, flow, **kwargs):  # noqa: ANN001, ANN003
            del db, kwargs
            self.claimed_deficits.append(flow.deficit_work_units)
            return BatchFairShareClaimResult(
                claim=BatchWorkClaim(
                    claim_id="claim-refilled",
                    worker_id="worker-1",
                    batch_id="batch-refilled",
                    endpoint="/v1/embeddings",
                    model_group=flow.model_group,
                    tenant_scope_type=flow.tenant_scope_type,
                    tenant_scope_id=flow.tenant_scope_id,
                    service_tier=flow.service_tier,
                    item_ids=["item-1"],
                    claimed_work_units=4,
                    lease_expires_at=now + timedelta(seconds=60),
                ),
                result="claimed",
                flow=flow,
            )

    repository = _RefillRepository()

    result = await repository.claim_next_fair_share_work(
        worker_id="worker-1",
        service_tier="standard",
        model_group="model-a",
        max_items=4,
        max_work_units=4,
        lease_seconds=120,
        max_deficit_multiplier=2,
    )

    assert result.result == "claimed"
    assert result.flow is not None
    assert result.flow.flow_id == "flow-first"
    assert repository.refresh_calls == 1
    assert repository.refills == 1
    assert repository.claimed_deficits == [4]


@pytest.mark.asyncio
async def test_fair_share_shadow_recommendation_selects_flow_without_claiming_items() -> None:
    flow = _scheduler_flow(
        flow_id="flow-recommended",
        tenant_scope_id="team-1",
        next_batch_id="batch-recommended",
        next_size_class="xs",
        next_scheduler_rank=1.0,
        next_age_credit_work_units=2,
        next_policy_reason="aging_credit",
    )

    class _ReadOnlyPrisma:
        pass

    class _RecommendRepository(BatchJobRepository):
        def __init__(self) -> None:
            super().__init__(_ReadOnlyPrisma())
            self.claim_called = False
            self.preview_kwargs: dict[str, object] = {}

        async def _try_acquire_scheduler_flow_lock(self, db, **kwargs):  # noqa: ANN001, ANN003
            del db, kwargs
            raise AssertionError("shadow recommendation should not acquire the active flow lock")

        async def refresh_scheduler_flows(self, **kwargs):  # noqa: ANN003
            del kwargs
            raise AssertionError("shadow recommendation should not refresh durable flow state")

        async def preview_scheduler_flows(self, **kwargs):  # noqa: ANN003
            self.preview_kwargs = dict(kwargs)
            return [flow]

        async def _record_scheduler_flow_skip_reasons(self, db, skip_reasons):  # noqa: ANN001
            del db, skip_reasons
            raise AssertionError("shadow recommendation should not update active skip summaries")

        async def _refill_scheduler_flow_deficits(self, db, **kwargs):  # noqa: ANN001, ANN003
            del db, kwargs
            raise AssertionError("shadow recommendation should not refill durable deficits")

        async def _claim_scheduler_flow_with_client(self, db, **kwargs):  # pragma: no cover
            del db, kwargs
            self.claim_called = True
            raise AssertionError("recommendation should not claim items")

    repository = _RecommendRepository()

    result = await repository.recommend_next_fair_share_flow(
        service_tier="standard",
        model_group="model-a",
        max_items=4,
        max_work_units=16,
        size_aware_scheduling_enabled=True,
        aging_seconds_per_work_unit=15,
        max_age_credit_work_units=200,
        min_large_job_claim_interval_seconds=45,
        small_job_max_work_units=50,
    )

    assert result.claim is None
    assert result.result == "recommended"
    assert result.flow == flow
    assert result.expected_share == 1.0
    assert result.active_flow_count == 1
    assert result.total_in_flight_work_units == 0
    assert result.recommended_batch_id == "batch-recommended"
    assert result.recommended_size_class == "xs"
    assert result.recommended_scheduler_rank == 1.0
    assert result.recommended_age_credit_work_units == 2
    assert result.recommended_policy_reason == "aging_credit"
    assert repository.preview_kwargs["size_aware_scheduling_enabled"] is True
    assert repository.preview_kwargs["aging_seconds_per_work_unit"] == 15
    assert repository.preview_kwargs["max_age_credit_work_units"] == 200
    assert repository.preview_kwargs["min_large_job_claim_interval_seconds"] == 45
    assert repository.preview_kwargs["small_job_max_work_units"] == 50
    assert repository.claim_called is False


@pytest.mark.asyncio
async def test_fair_share_shadow_recommendation_simulates_refill_without_durable_mutation() -> None:
    now = datetime.now(tz=UTC)
    flow = _scheduler_flow(
        flow_id="flow-refill",
        tenant_scope_id="team-1",
        deficit_work_units=0,
        quantum_work_units=4,
        next_item_work_units=4,
        queued_work_units=4,
        oldest_queue_entered_at=now,
    )
    peer_flow = _scheduler_flow(
        flow_id="flow-peer",
        tenant_scope_id="team-2",
        deficit_work_units=0,
        quantum_work_units=4,
        next_item_work_units=4,
        queued_work_units=4,
        oldest_queue_entered_at=now + timedelta(seconds=1),
    )

    class _ReadOnlyPrisma:
        pass

    class _RecommendRepository(BatchJobRepository):
        def __init__(self) -> None:
            super().__init__(_ReadOnlyPrisma())
            self.preview_calls = 0

        async def _try_acquire_scheduler_flow_lock(self, db, **kwargs):  # noqa: ANN001, ANN003
            del db, kwargs
            raise AssertionError("shadow recommendation should not acquire the active flow lock")

        async def refresh_scheduler_flows(self, **kwargs):  # noqa: ANN003
            del kwargs
            raise AssertionError("shadow recommendation should not refresh durable flow state")

        async def preview_scheduler_flows(self, **kwargs):  # noqa: ANN003
            del kwargs
            self.preview_calls += 1
            return [flow, peer_flow]

        async def _record_scheduler_flow_skip_reasons(self, db, skip_reasons):  # noqa: ANN001
            del db, skip_reasons
            raise AssertionError("shadow recommendation should not update active skip summaries")

        async def _refill_scheduler_flow_deficits(self, db, **kwargs):  # pragma: no cover
            del db, kwargs
            raise AssertionError("shadow recommendation should not refill durable deficits")

    repository = _RecommendRepository()

    result = await repository.recommend_next_fair_share_flow(
        service_tier="standard",
        model_group="model-a",
        max_items=4,
        max_work_units=4,
        max_deficit_multiplier=2,
    )

    assert result.result == "recommended"
    assert result.flow is not None
    assert result.flow.flow_id == "flow-refill"
    assert result.flow.deficit_work_units == 4
    assert result.expected_share == 0.5
    assert result.active_flow_count == 2
    assert flow.deficit_work_units == 0
    assert peer_flow.deficit_work_units == 0
    assert repository.preview_calls == 1


@pytest.mark.asyncio
async def test_fair_share_shadow_recommendation_uses_shadow_skip_metrics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    flow = _scheduler_flow(
        flow_id="flow-capped",
        in_flight_work_units=32,
        queued_work_units=1,
        next_item_work_units=1,
        deficit_work_units=16,
    )
    shadow_skips: list[tuple[str, str, str]] = []
    active_skips: list[str] = []
    monkeypatch.setattr(
        job_repository_module,
        "increment_batch_scheduler_shadow_skip",
        lambda *, model_group, service_tier, reason: shadow_skips.append(
            (model_group, service_tier, reason)
        ),
    )
    monkeypatch.setattr(
        job_repository_module,
        "increment_batch_scheduler_flow_skip",
        lambda *, reason: active_skips.append(reason),
    )

    class _ReadOnlyPrisma:
        pass

    class _RecommendRepository(BatchJobRepository):
        def __init__(self) -> None:
            super().__init__(_ReadOnlyPrisma())

        async def _try_acquire_scheduler_flow_lock(self, db, **kwargs):  # noqa: ANN001, ANN003
            del db, kwargs
            raise AssertionError("shadow recommendation should not acquire the active flow lock")

        async def refresh_scheduler_flows(self, **kwargs):  # noqa: ANN003
            del kwargs
            raise AssertionError("shadow recommendation should not refresh durable flow state")

        async def preview_scheduler_flows(self, **kwargs):  # noqa: ANN003
            del kwargs
            return [flow]

        async def _record_scheduler_flow_skip_reasons(self, db, skip_reasons):  # noqa: ANN001
            del db, skip_reasons
            raise AssertionError("shadow recommendation should not update active skip summaries")

    repository = _RecommendRepository()

    result = await repository.recommend_next_fair_share_flow(
        service_tier="standard",
        model_group="model-a",
        max_items=4,
        max_work_units=16,
        tenant_max_in_flight_work_units=32,
    )

    assert result.result == "tenant_in_flight_full"
    assert shadow_skips == [
        ("model-a", "standard", "tenant_in_flight_full"),
        ("model-a", "standard", "tenant_in_flight_full"),
    ]
    assert active_skips == []


@pytest.mark.asyncio
async def test_fairness_ratio_uses_actual_share_over_expected_weight_share(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = _scheduler_flow(
        flow_id="flow-selected",
        tenant_scope_id="team-selected",
        weight=1,
        in_flight_work_units=8,
    )
    peer = _scheduler_flow(
        flow_id="flow-peer",
        tenant_scope_id="team-peer",
        weight=3,
        in_flight_work_units=24,
    )
    observed: list[float] = []
    monkeypatch.setattr(
        job_repository_module,
        "observe_batch_scheduler_fairness_ratio",
        lambda **kwargs: observed.append(kwargs["ratio"]),
    )

    class _FairnessRepository(BatchJobRepository):
        async def list_scheduler_flows(self, **kwargs):  # noqa: ANN003
            assert kwargs["active"] is True
            return [selected, peer]

    repository = _FairnessRepository()
    claim = BatchWorkClaim(
        claim_id="claim-1",
        worker_id="worker-1",
        batch_id="batch-1",
        endpoint="/v1/embeddings",
        model_group="model-a",
        tenant_scope_type="team",
        tenant_scope_id="team-selected",
        service_tier="standard",
        item_ids=["item-1"],
        claimed_work_units=8,
        lease_expires_at=datetime.now(tz=UTC) + timedelta(seconds=60),
    )

    await repository._observe_scheduler_fairness_ratio_for_claim(object(), flow=selected, claim=claim)

    assert observed == [pytest.approx(1.6)]


@pytest.mark.asyncio
async def test_fairness_ratio_uses_claim_snapshot_without_extra_flow_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = _scheduler_flow(
        flow_id="flow-selected",
        tenant_scope_id="team-selected",
        weight=1,
        in_flight_work_units=8,
    )
    peer = _scheduler_flow(
        flow_id="flow-peer",
        tenant_scope_id="team-peer",
        weight=3,
        in_flight_work_units=24,
    )
    observed: list[float] = []
    monkeypatch.setattr(
        job_repository_module,
        "observe_batch_scheduler_fairness_ratio",
        lambda **kwargs: observed.append(kwargs["ratio"]),
    )

    class _FairnessRepository(BatchJobRepository):
        async def list_scheduler_flows(self, **kwargs):  # noqa: ANN003
            del kwargs
            raise AssertionError("claim-time fairness metric should reuse the decision snapshot")

    repository = _FairnessRepository()
    claim = BatchWorkClaim(
        claim_id="claim-1",
        worker_id="worker-1",
        batch_id="batch-1",
        endpoint="/v1/embeddings",
        model_group="model-a",
        tenant_scope_type="team",
        tenant_scope_id="team-selected",
        service_tier="standard",
        item_ids=["item-1"],
        claimed_work_units=8,
        lease_expires_at=datetime.now(tz=UTC) + timedelta(seconds=60),
    )

    await repository._observe_scheduler_fairness_ratio_for_claim(
        object(),
        flow=selected,
        claim=claim,
        active_flows=[selected, peer],
    )

    assert observed == [pytest.approx(1.6)]


@pytest.mark.asyncio
async def test_fair_share_claim_publishes_updated_flow_without_extra_flow_scan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(tz=UTC)
    flow = _scheduler_flow(
        flow_id="flow-selected",
        tenant_scope_id="team-selected",
        deficit_work_units=16,
        queued_work_units=8,
        in_flight_work_units=4,
    )
    claim = BatchWorkClaim(
        claim_id="claim-1",
        worker_id="worker-1",
        batch_id="batch-1",
        endpoint="/v1/embeddings",
        model_group="model-a",
        tenant_scope_type="team",
        tenant_scope_id="team-selected",
        service_tier="standard",
        item_ids=["item-1"],
        claimed_work_units=8,
        lease_expires_at=now + timedelta(seconds=60),
    )
    published: list[list[BatchSchedulerFlowRecord]] = []
    monkeypatch.setattr(
        job_repository_module,
        "publish_batch_scheduler_flows",
        lambda flows: published.append(list(flows)),
    )

    class _Db:
        async def query_raw(self, sql: str, *params):
            assert "UPDATE deltallm_batch_scheduler_flow" in sql
            assert "RETURNING *" in sql
            row = dict(flow.__dict__)
            row.update(deficit_work_units=8, skip_reason_summary=json.dumps(flow.skip_reasons or {}))
            return [row]

    class _ClaimRepository(BatchJobRepository):
        async def _claim_next_work_with_client(self, db, **kwargs):  # noqa: ANN001, ANN003
            del db, kwargs
            return claim

        async def list_scheduler_flows(self, **kwargs):  # noqa: ANN003
            del kwargs
            raise AssertionError("claim metrics should not scan scheduler flows in the claim transaction")

    repository = _ClaimRepository()

    result = await repository._claim_scheduler_flow_with_client(
        _Db(),
        flow=flow,
        worker_id="worker-1",
        max_items=4,
        max_work_units=16,
        lease_seconds=120,
        capacity_max_in_flight_items=None,
        capacity_max_in_flight_work_units=None,
        allow_oversized_first_item=False,
        max_deficit_multiplier=2,
        fairness_flows=[flow],
    )

    assert result.result == "claimed"
    assert result.flow is not None
    assert result.flow.deficit_work_units == 8
    assert len(published) == 1
    assert [published_flow.flow_id for published_flow in published[0]] == ["flow-selected"]


@pytest.mark.asyncio
async def test_fair_share_claim_reports_flow_lock_busy_after_candidate_snapshot() -> None:
    class _TxPrisma:
        def __init__(self) -> None:
            self.tx_client = _PrismaSpy()
            self.candidate_calls: list[tuple[str, tuple[object, ...]]] = []

        @asynccontextmanager
        async def tx(self):
            yield self.tx_client

        async def query_raw(self, sql: str, *params):
            self.candidate_calls.append((sql, params))
            return []

    class _LockBusySpy(_PrismaSpy):
        async def query_raw(self, sql: str, *params):
            self.sql = sql
            self.params = params
            self.calls.append((sql, params))
            return [{"locked": False}]

    prisma = _TxPrisma()
    prisma.tx_client = _LockBusySpy()
    repository = BatchRepository(prisma_client=prisma)

    result = await repository.claim_next_fair_share_work(
        worker_id="worker-1",
        service_tier="standard",
        model_group="model-a",
        max_items=4,
        max_work_units=16,
        lease_seconds=120,
    )

    assert result.claim is None
    assert result.result == "flow_lock_busy"
    assert len(prisma.candidate_calls) == 1
    assert "WITH base_jobs AS" in prisma.candidate_calls[0][0]
    assert "pg_try_advisory_xact_lock" in prisma.tx_client.sql
    assert "hashtext($1)" in prisma.tx_client.sql
    assert "$3::bigint" in prisma.tx_client.sql
    assert prisma.tx_client.params == (
        "model-a",
        "standard",
        advisory_lock_key("batch_scheduler_flow", "standard", "model-a"),
    )


@pytest.mark.asyncio
async def test_fair_share_claim_loads_candidates_outside_final_lock() -> None:
    class _TxPrisma:
        def __init__(self) -> None:
            self.tx_clients = [object()]
            self.tx_index = 0

        @asynccontextmanager
        async def tx(self):
            client = self.tx_clients[self.tx_index]
            self.tx_index += 1
            yield client

    class _Repository(BatchJobRepository):
        def __init__(self, prisma_client) -> None:  # noqa: ANN001
            super().__init__(prisma_client)
            self.lock_dbs: list[object] = []
            self.candidate_executor: object | None = None
            self.in_flight_executor: object | None = None
            self.refresh_snapshot: object | None = None

        async def _try_acquire_scheduler_flow_lock(self, db, **kwargs):  # noqa: ANN001, ANN003
            del kwargs
            self.lock_dbs.append(db)
            return True

        async def _load_scheduler_flow_refresh_snapshot(self, executor, **kwargs):  # noqa: ANN001, ANN003
            assert kwargs["include_in_flight"] is False
            self.candidate_executor = executor
            return _scheduler_flow_snapshot()

        async def _load_scheduler_flow_in_flight_snapshot(self, executor, **kwargs):  # noqa: ANN001, ANN003
            del kwargs
            self.in_flight_executor = executor
            return _scheduler_flow_snapshot()

        async def _refresh_scheduler_flows_from_snapshot(self, **kwargs):  # noqa: ANN003
            self.refresh_snapshot = kwargs["snapshot"]
            return []

    prisma = _TxPrisma()
    repository = _Repository(prisma)

    result = await repository.claim_next_fair_share_work(
        worker_id="worker-1",
        service_tier="standard",
        model_group="model-a",
        max_items=4,
        max_work_units=16,
        lease_seconds=120,
    )

    assert result.result == "no_active_flow"
    assert repository.lock_dbs == [prisma.tx_clients[0]]
    assert repository.candidate_executor is prisma
    assert repository.in_flight_executor is prisma.tx_clients[0]
    assert repository.refresh_snapshot is not None


@pytest.mark.asyncio
async def test_fair_share_claim_allows_concurrent_candidate_snapshots() -> None:
    class _TxPrisma:
        @asynccontextmanager
        async def tx(self):
            yield SimpleNamespace()

    class _Repository(BatchJobRepository):
        def __init__(self, prisma_client: _TxPrisma) -> None:
            super().__init__(prisma_client)
            self.both_snapshots_started = asyncio.Event()
            self.release_snapshot = asyncio.Event()
            self.snapshot_attempts = 0
            self.snapshot_active = 0
            self.max_snapshot_concurrency = 0

        async def _try_acquire_scheduler_flow_lock(self, db, **kwargs):  # noqa: ANN001, ANN003
            del db, kwargs
            return True

        async def _load_scheduler_flow_refresh_snapshot(self, executor, **kwargs):  # noqa: ANN001, ANN003
            assert kwargs["include_in_flight"] is False
            self.snapshot_attempts += 1
            self.snapshot_active += 1
            self.max_snapshot_concurrency = max(self.max_snapshot_concurrency, self.snapshot_active)
            if self.snapshot_active == 2:
                self.both_snapshots_started.set()
            await self.release_snapshot.wait()
            self.snapshot_active -= 1
            return _scheduler_flow_snapshot()

        async def _load_scheduler_flow_in_flight_snapshot(self, executor, **kwargs):  # noqa: ANN001, ANN003
            del executor, kwargs
            return _scheduler_flow_snapshot()

        async def _refresh_scheduler_flows_from_snapshot(self, **kwargs):  # noqa: ANN003
            del kwargs
            return []

    prisma = _TxPrisma()
    repository = _Repository(prisma)

    first_claim = asyncio.create_task(
        repository.claim_next_fair_share_work(
            worker_id="worker-0",
            service_tier="standard",
            model_group="model-a",
            max_items=4,
            max_work_units=16,
            lease_seconds=120,
        )
    )
    second_claim = asyncio.create_task(
        repository.claim_next_fair_share_work(
            worker_id="worker-1",
            service_tier="standard",
            model_group="model-a",
            max_items=4,
            max_work_units=16,
            lease_seconds=120,
        )
    )
    await repository.both_snapshots_started.wait()
    repository.release_snapshot.set()
    first_result, second_result = await asyncio.gather(first_claim, second_claim)

    assert first_result.result == "no_active_flow"
    assert second_result.result == "no_active_flow"
    assert repository.snapshot_attempts == 2
    assert repository.max_snapshot_concurrency == 2


@pytest.mark.asyncio
async def test_fair_share_claim_path_preserves_tenant_progress_under_hot_model_contention() -> None:
    worker_count = 50

    class _TxPrisma:
        def __init__(self) -> None:
            self.repository: _Repository | None = None

        @asynccontextmanager
        async def tx(self):
            tx = SimpleNamespace(lock_acquired=False)
            try:
                yield tx
            finally:
                if tx.lock_acquired:
                    assert self.repository is not None
                    self.repository.flow_lock.release()

    class _Repository(BatchJobRepository):
        def __init__(self, prisma_client: _TxPrisma) -> None:
            super().__init__(prisma_client)
            self.remaining_tenants = {f"team-{index}" for index in range(worker_count)}
            self.claimed_tenants: set[str] = set()
            self.flow_lock = asyncio.Lock()
            self.first_snapshot_barrier = asyncio.Event()
            self.snapshot_attempts = 0
            self.snapshot_active = 0
            self.max_snapshot_concurrency = 0
            self.flow_lock_busy_count = 0

        async def _try_acquire_scheduler_flow_lock(self, db, **kwargs):  # noqa: ANN001, ANN003
            del kwargs
            if self.flow_lock.locked():
                self.flow_lock_busy_count += 1
                return False
            await self.flow_lock.acquire()
            db.lock_acquired = True
            return True

        async def _load_scheduler_flow_refresh_snapshot(self, executor, **kwargs):  # noqa: ANN001, ANN003
            assert kwargs["include_in_flight"] is False
            self.snapshot_attempts += 1
            self.snapshot_active += 1
            self.max_snapshot_concurrency = max(self.max_snapshot_concurrency, self.snapshot_active)
            if self.snapshot_attempts == worker_count:
                self.first_snapshot_barrier.set()
            if self.snapshot_attempts <= worker_count:
                await self.first_snapshot_barrier.wait()
            self.snapshot_active -= 1
            aggregates = tuple(
                job_repository_module._SchedulerFlowRefreshAggregate(
                    service_tier="standard",
                    model_group="model-a",
                    tenant_scope_type="team",
                    tenant_scope_id=tenant,
                    queued_jobs=1,
                    queued_work_units=1,
                    in_flight_work_units=0,
                    oldest_queue_entered_at=datetime.now(tz=UTC),
                    next_item_work_units=1,
                    next_batch_id=f"batch-{tenant}",
                )
                for tenant in sorted(self.remaining_tenants)
            )
            return job_repository_module._SchedulerFlowRefreshSnapshot(
                service_tier="standard",
                model_group="model-a",
                aggregates=aggregates,
                legacy_api_key_scope_repairs={},
            )

        async def _load_scheduler_flow_in_flight_snapshot(self, executor, **kwargs):  # noqa: ANN001, ANN003
            del executor, kwargs
            return _scheduler_flow_snapshot()

        async def _refresh_scheduler_flows_from_snapshot(self, **kwargs):  # noqa: ANN003
            snapshot = kwargs["snapshot"]
            await asyncio.sleep(0)
            return [
                _scheduler_flow(
                    flow_id=f"flow-{aggregate.tenant_scope_id}",
                    tenant_scope_id=aggregate.tenant_scope_id,
                    queued_work_units=aggregate.queued_work_units,
                    next_batch_id=aggregate.next_batch_id,
                )
                for aggregate in snapshot.aggregates
            ]

        async def _claim_scheduler_selected_flow(self, db, **kwargs):  # noqa: ANN001, ANN003
            del db
            flow = kwargs["flow"]
            if flow.tenant_scope_id not in self.remaining_tenants:
                return BatchFairShareClaimResult(claim=None, result="empty_flow", flow=flow)
            self.remaining_tenants.remove(flow.tenant_scope_id)
            self.claimed_tenants.add(flow.tenant_scope_id)
            return BatchFairShareClaimResult(
                result="claimed",
                flow=flow,
                claim=BatchWorkClaim(
                    claim_id=f"claim-{flow.tenant_scope_id}",
                    worker_id=str(kwargs["worker_id"]),
                    batch_id=str(flow.next_batch_id),
                    endpoint="/v1/embeddings",
                    model_group=flow.model_group,
                    tenant_scope_type=flow.tenant_scope_type,
                    tenant_scope_id=flow.tenant_scope_id,
                    service_tier=flow.service_tier,
                    item_ids=[f"item-{flow.tenant_scope_id}"],
                    claimed_work_units=1,
                    lease_expires_at=datetime.now(tz=UTC),
                ),
            )

    prisma = _TxPrisma()
    repository = _Repository(prisma)
    prisma.repository = repository

    async def _claim_until_progress(worker_index: int) -> BatchFairShareClaimResult:
        result = BatchFairShareClaimResult(claim=None, result="not_started")
        for _ in range(worker_count * 2):
            result = await repository.claim_next_fair_share_work(
                worker_id=f"worker-{worker_index}",
                service_tier="standard",
                model_group="model-a",
                max_items=1,
                max_work_units=1,
                lease_seconds=120,
                base_quantum_work_units=1,
                max_deficit_multiplier=4,
            )
            if result.claim is not None:
                return result
            assert result.result in {"flow_lock_busy", "empty_flow", "no_active_flow"}
            await asyncio.sleep(0)
        return result

    results = await asyncio.gather(
        *(_claim_until_progress(index) for index in range(worker_count))
    )
    claims = [result.claim for result in results if result.claim is not None]

    assert len(claims) == worker_count
    assert repository.claimed_tenants == {f"team-{index}" for index in range(worker_count)}
    assert len({claim.batch_id for claim in claims}) == worker_count
    assert len({item_id for claim in claims for item_id in claim.item_ids}) == worker_count
    assert repository.max_snapshot_concurrency == worker_count
    assert repository.flow_lock_busy_count > 0


@pytest.mark.asyncio
async def test_fair_share_scheduler_lock_uses_capacity_lock_key_order() -> None:
    class _LockPrisma:
        def __init__(self) -> None:
            self.sql = ""
            self.params: tuple[object, ...] = ()

        async def query_raw(self, sql: str, *params):
            self.sql = sql
            self.params = params
            return [{"locked": True}]

    prisma = _LockPrisma()
    repository = BatchJobRepository()

    locked = await repository._try_acquire_scheduler_flow_lock(
        prisma,
        service_tier="standard",
        model_group="model-a",
    )

    assert locked is True
    assert "pg_try_advisory_xact_lock" in prisma.sql
    assert "hashtext($1)" in prisma.sql
    assert "$3::bigint" in prisma.sql
    assert prisma.params == (
        "model-a",
        "standard",
        advisory_lock_key("batch_scheduler_flow", "standard", "model-a"),
    )


@pytest.mark.asyncio
async def test_fair_share_scheduler_lock_can_skip_legacy_hashtext_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _LockPrisma:
        def __init__(self) -> None:
            self.sql = ""
            self.params: tuple[object, ...] = ()

        async def query_raw(self, sql: str, *params):
            self.sql = sql
            self.params = params
            return [{"locked": True}]

    del monkeypatch
    advisory_locks_module.set_advisory_lock_mode("canonical")

    try:
        prisma = _LockPrisma()
        repository = BatchJobRepository()

        locked = await repository._try_acquire_scheduler_flow_lock(
            prisma,
            service_tier="standard",
            model_group="model-a",
        )

        assert locked is True
        assert "hashtext" not in prisma.sql
        assert "pg_try_advisory_xact_lock($1::bigint)" in prisma.sql
        assert prisma.params == (advisory_lock_key("batch_scheduler_flow", "standard", "model-a"),)
    finally:
        advisory_locks_module.set_advisory_lock_mode("dual")


@pytest.mark.asyncio
async def test_diagnose_model_group_work_claim_empty_reports_oversized_head_item() -> None:
    class _DiagnosticPrisma:
        def __init__(self) -> None:
            self.sql = ""
            self.params = ()

        async def query_raw(self, sql: str, *params):
            self.sql = sql
            self.params = params
            return [
                {
                    "in_flight_items": 0,
                    "has_runnable_head_item": True,
                    "has_fitting_head_item": False,
                }
            ]

    prisma = _DiagnosticPrisma()
    repository = BatchRepository(prisma_client=prisma)

    reason = await repository.diagnose_model_group_work_claim_empty(
        model_group="model-a",
        service_tier="standard",
        max_work_units=5,
        capacity_max_in_flight_items=4,
    )

    assert reason == "oversized_head_item"
    assert "runnable_head_items AS" in prisma.sql
    assert "estimated_work_units <= $3" in prisma.sql
    assert prisma.params == ("model-a", "standard", 5)


@pytest.mark.asyncio
async def test_diagnose_model_group_work_claim_empty_reports_work_unit_capacity_full() -> None:
    class _DiagnosticPrisma:
        def __init__(self) -> None:
            self.sql = ""
            self.params = ()

        async def query_raw(self, sql: str, *params):
            self.sql = sql
            self.params = params
            return [
                {
                    "in_flight_items": 1,
                    "in_flight_work_units": 7,
                    "has_runnable_head_item": True,
                    "has_fitting_head_item": False,
                }
            ]

    prisma = _DiagnosticPrisma()
    repository = BatchRepository(prisma_client=prisma)

    reason = await repository.diagnose_model_group_work_claim_empty(
        model_group="model-a",
        service_tier="standard",
        max_work_units=5,
        capacity_max_in_flight_items=4,
        capacity_max_in_flight_work_units=7,
    )

    assert reason == "capacity_work_units_full_after_lock"
    assert "AS in_flight_work_units" in prisma.sql
    assert "estimated_work_units <= LEAST($3, GREATEST($4::int" in prisma.sql
    assert prisma.params == ("model-a", "standard", 5, 7)


@pytest.mark.asyncio
async def test_claim_next_work_legacy_only_claims_only_legacy_missing_model_group_jobs() -> None:
    prisma = _PrismaSpy()
    repository = BatchRepository(prisma_client=prisma)

    claim = await repository.claim_next_work(
        worker_id="worker-1",
        max_items=10,
        max_work_units=25,
        lease_seconds=120,
        legacy_only=True,
        claim_order="fifo",
    )

    assert claim is None
    assert "COALESCE(NULLIF(j.scheduler_version, ''), 'fifo_v1') = 'fifo_v1'" in prisma.sql
    assert "j.scheduling_model_group IS NULL OR j.scheduling_model_group = ''" in prisma.sql
    assert "ORDER BY COALESCE(j.queue_entered_at, j.created_at) ASC" in prisma.sql


@pytest.mark.asyncio
async def test_model_group_backlog_query_counts_runnable_jobs_by_model() -> None:
    prisma = _PrismaSpy()
    repository = BatchRepository(prisma_client=prisma)

    rows = await repository.list_model_group_backlog()

    assert rows == []
    assert "WITH base_jobs AS" in prisma.sql
    assert "runnable_job_backlog AS" in prisma.sql
    assert "j.scheduling_model_group IS NOT NULL" in prisma.sql
    assert "GROUP BY model_group, service_tier, size_class" in prisma.sql
    assert "SUM(runnable_work_units)" in prisma.sql
    assert "GREATEST(COALESCE(i.estimated_work_units, 1), 1)" in prisma.sql
    assert "i.not_before_at IS NULL OR i.not_before_at <= NOW()" in prisma.sql


@pytest.mark.asyncio
async def test_model_group_in_flight_query_excludes_expired_leases() -> None:
    prisma = _PrismaSpy()
    repository = BatchRepository(prisma_client=prisma)

    rows = await repository.list_model_group_in_flight()

    assert rows == []
    assert "i.status = 'in_progress'" in prisma.sql
    assert "i.lease_expires_at IS NULL OR i.lease_expires_at > NOW()" in prisma.sql
    assert "GROUP BY model_group, service_tier" in prisma.sql


@pytest.mark.asyncio
async def test_claim_next_work_returns_none_when_lease_unparseable(caplog: pytest.LogCaptureFixture) -> None:
    class _BadLeasePrisma:
        async def query_raw(self, sql, *params):
            del sql, params
            return [
                {
                    "batch_id": "b-bad-lease",
                    "endpoint": "/v1/embeddings",
                    "model_group": "m1",
                    "tenant_scope_type": "api_key",
                    "tenant_scope_id": "tok",
                    "service_tier": "standard",
                    "size_class": "xs",
                    "queue_entered_at": None,
                    "previous_status": "queued",
                    "previous_first_claimed_at": None,
                    "queue_wait_observed": False,
                    "item_ids": ["item-1"],
                    "claimed_work_units": 1,
                    "lease_expires_at": "not-a-timestamp",
                    "reclaimed_items": 0,
                }
            ]

    repository = BatchRepository(prisma_client=_BadLeasePrisma())
    with caplog.at_level(logging.ERROR, logger="src.batch.repositories.job_repository"):
        claim = await repository.claim_next_work(
            worker_id="w1",
            max_items=4,
            max_work_units=8,
            lease_seconds=60,
        )

    assert claim is None
    assert "missing lease_expires_at" in caplog.text


@pytest.mark.asyncio
async def test_claim_next_finalization_uses_job_lease_only_for_finalizing_jobs() -> None:
    prisma = _PrismaSpy()
    repository = BatchRepository(prisma_client=prisma)

    job = await repository.claim_next_finalization(worker_id="worker-1", lease_seconds=120)

    assert job is None
    assert "WHERE status = 'finalizing'" in prisma.sql
    assert "locked_by = $1" in prisma.sql
    assert "FOR UPDATE SKIP LOCKED" in prisma.sql
    assert "RETURNING j.*" in prisma.sql


@pytest.mark.asyncio
async def test_diagnose_empty_work_claim_uses_bounded_queue_state_probe() -> None:
    prisma = _PrismaSpy()
    repository = BatchRepository(prisma_client=prisma)

    reason = await repository.diagnose_empty_work_claim()

    assert reason == "no_available_work"
    assert "WITH candidate_jobs AS" in prisma.sql
    assert "WHERE j.status IN ('queued', 'in_progress')" in prisma.sql
    assert "LIMIT 100" in prisma.sql
    assert "unleased_jobs AS" in prisma.sql
    assert "claimable_probe AS" in prisma.sql
    assert "FOR UPDATE SKIP LOCKED" in prisma.sql
    assert "EXISTS (" in prisma.sql
    assert "COUNT(*) FILTER" not in prisma.sql


@pytest.mark.asyncio
async def test_diagnose_empty_work_claim_classifies_future_retry_delay() -> None:
    class _DiagnosticPrisma:
        async def query_raw(self, sql: str, *params):
            del sql, params
            return [
                {
                    "active_jobs": 1,
                    "unleased_jobs": 1,
                    "pending_or_in_progress_items": 1,
                    "pending_items": 2,
                    "in_progress_items": 0,
                    "due_pending_items": 0,
                    "future_pending_items": 2,
                    "runnable_items": 0,
                    "claimable_items": 0,
                }
            ]

    repository = BatchRepository(prisma_client=_DiagnosticPrisma())

    assert await repository.diagnose_empty_work_claim() == "not_before_future"


@pytest.mark.asyncio
async def test_diagnose_empty_work_claim_prefers_lock_contention_for_mixed_due_and_future() -> None:
    class _DiagnosticPrisma:
        async def query_raw(self, sql: str, *params):
            del sql, params
            return [
                {
                    "active_jobs": 1,
                    "unleased_jobs": 1,
                    "pending_or_in_progress_items": 1,
                    "pending_items": 2,
                    "in_progress_items": 0,
                    "due_pending_items": 1,
                    "future_pending_items": 1,
                    "runnable_items": 0,
                    "claimable_items": 0,
                }
            ]

    repository = BatchRepository(prisma_client=_DiagnosticPrisma())

    assert await repository.diagnose_empty_work_claim() == "all_items_locked"


@pytest.mark.asyncio
async def test_release_claim_items_releases_only_owned_in_progress_rows() -> None:
    prisma = _PrismaSpy()
    repository = BatchRepository(prisma_client=prisma)

    released = await repository.release_claim_items(item_ids=["item-1", "item-2"], worker_id="worker-1")

    assert released == 0
    assert "status = 'pending'" in prisma.sql
    assert "i.locked_by = $3" in prisma.sql
    assert "i.status = 'in_progress'" in prisma.sql
    assert "lease_expires_at = NULL" in prisma.sql


@pytest.mark.asyncio
async def test_claim_items_logs_reclaimed_expired_rows(caplog: pytest.LogCaptureFixture):
    class _ReclaimPrisma:
        async def query_raw(self, sql: str, *params):
            del sql, params
            return [
                {
                    "item_id": "item-1",
                    "batch_id": "batch-1",
                    "line_number": 1,
                    "custom_id": "custom-1",
                    "status": "in_progress",
                    "request_body": {"model": "m1"},
                    "response_body": None,
                    "error_body": None,
                    "usage": None,
                    "provider_cost": 0.0,
                    "billed_cost": 0.0,
                    "attempts": 2,
                    "last_error": None,
                    "locked_by": "worker-1",
                    "lease_expires_at": None,
                    "created_at": None,
                    "started_at": None,
                    "completed_at": None,
                    "previous_status": "in_progress",
                }
            ]

    repository = BatchRepository(prisma_client=_ReclaimPrisma())

    with caplog.at_level(logging.INFO):
        items = await repository.claim_items(
            batch_id="batch-1",
            worker_id="worker-1",
            limit=10,
            lease_seconds=120,
        )

    assert len(items) == 1
    assert "batch items reclaimed batch_id=batch-1 worker_id=worker-1 count=1" in caplog.text


@pytest.mark.asyncio
async def test_claim_next_job_includes_finalizing_jobs():
    prisma = _PrismaSpy()
    repository = BatchRepository(prisma_client=prisma)

    job = await repository.claim_next_job(worker_id="worker-1", lease_seconds=120)

    assert job is None
    assert "'finalizing'" in prisma.sql
    assert "CASE WHEN status = 'finalizing' THEN 1 ELSE 0 END" in prisma.sql
    assert '::"DeltaLLM_BatchJobStatus"' in prisma.sql


@pytest.mark.asyncio
async def test_request_cancel_preserves_finalizing_status():
    prisma = _PrismaSpy()
    repository = BatchRepository(prisma_client=prisma)

    job = await repository.request_cancel("batch-1")

    assert job is None
    assert "WHEN status = 'finalizing' THEN status" in prisma.sql
    assert '::"DeltaLLM_BatchJobStatus"' in prisma.sql


@pytest.mark.asyncio
async def test_reschedule_finalization_pushes_lease_forward():
    prisma = _PrismaSpy()
    repository = BatchRepository(prisma_client=prisma)

    rescheduled = await repository.reschedule_finalization(
        batch_id="batch-1",
        worker_id="worker-1",
        retry_delay_seconds=60,
    )

    assert rescheduled is False
    assert "lease_expires_at = NOW() + ($3 || ' seconds')::interval" in prisma.sql
    assert "status = 'finalizing'" in prisma.sql


@pytest.mark.asyncio
async def test_retryable_mark_item_failed_uses_database_deadline_guard():
    prisma = _PrismaSpy()
    repository = BatchRepository(prisma_client=prisma)

    updated = await repository.mark_item_failed(
        item_id="item-1",
        worker_id="worker-1",
        error_body={"message": "rate limited"},
        last_error="rate limited",
        retryable=True,
        retry_delay_seconds=60,
        claim_epoch=7,
    )

    assert updated is False
    assert prisma.params == (
        "item-1",
        json.dumps({"message": "rate limited"}),
        "rate limited",
        60,
        "worker-1",
        7,
    )
    assert "FROM deltallm_batch_job j" in prisma.sql
    assert "j.batch_id = i.batch_id" in prisma.sql
    assert "NOW() + ($4 || ' seconds')::interval < j.expires_at" in prisma.sql
    assert "i.claim_epoch = $6::bigint" in prisma.sql


@pytest.mark.asyncio
async def test_mark_item_failed_requires_claim_epoch():
    prisma = _PrismaSpy()
    repository = BatchRepository(prisma_client=prisma)

    updated = await repository.mark_item_failed(
        item_id="item-1",
        worker_id="worker-1",
        error_body={"message": "rate limited"},
        last_error="rate limited",
        retryable=True,
        retry_delay_seconds=60,
    )

    assert updated is False
    assert prisma.calls == []


@pytest.mark.asyncio
async def test_release_items_for_retry_requires_claim_epochs():
    prisma = _PrismaSpy()
    repository = BatchRepository(prisma_client=prisma)

    item_ids = await repository.release_items_for_retry(
        item_ids=["item-1"],
        worker_id="worker-1",
    )

    assert item_ids == []
    assert prisma.calls == []


@pytest.mark.asyncio
async def test_release_items_for_retry_preserves_immediate_requeue_defaults():
    prisma = _PrismaSpy()
    repository = BatchRepository(prisma_client=prisma)

    item_ids = await repository.release_items_for_retry(
        item_ids=["item-1"],
        worker_id="worker-1",
        item_claim_epochs={"item-1": 3},
    )

    assert item_ids == []
    assert prisma.params == ("item-1", 3, "worker-1", 0, None, None)
    assert "status = 'pending'" in prisma.sql
    assert "ELSE NULL" in prisma.sql
    assert "error_body = COALESCE($5::jsonb, i.error_body)" in prisma.sql
    assert "last_error = COALESCE($6, i.last_error)" in prisma.sql
    assert "j.batch_id = i.batch_id" in prisma.sql
    assert "NOW() + ($4 || ' seconds')::interval < j.expires_at" in prisma.sql
    assert "i.locked_by = $3" in prisma.sql
    assert "i.claim_epoch = p.claim_epoch" in prisma.sql


@pytest.mark.asyncio
async def test_release_items_for_retry_can_store_retry_metadata_and_delay():
    prisma = _PrismaSpy()
    repository = BatchRepository(prisma_client=prisma)

    await repository.release_items_for_retry(
        item_ids=["item-1", "item-2"],
        worker_id="worker-1",
        retry_delay_seconds=30,
        error_body={"retry_category": "upstream_5xx"},
        last_error="upstream unavailable",
        item_claim_epochs={"item-1": 11, "item-2": 12},
    )

    assert prisma.params[0:4] == ("item-1", 11, "item-2", 12)
    assert prisma.params[4] == "worker-1"
    assert prisma.params[5] == 30
    assert json.loads(prisma.params[6]) == {"retry_category": "upstream_5xx"}
    assert prisma.params[7] == "upstream unavailable"
    assert "WHEN $6 > 0 THEN NOW() + ($6 || ' seconds')::interval" in prisma.sql
    assert "i.locked_by = $5" in prisma.sql


@pytest.mark.asyncio
async def test_count_active_jobs_for_scope_prefers_team_scope():
    prisma = _PrismaSpy()
    repository = BatchRepository(prisma_client=prisma)

    count = await repository.count_active_jobs_for_scope(
        created_by_api_key="key-1",
        created_by_team_id="team-1",
    )

    assert count == 0
    assert "created_by_team_id = $1" in prisma.sql
    assert "status NOT IN ('completed', 'failed', 'cancelled', 'expired')" in prisma.sql


@pytest.mark.asyncio
async def test_list_items_page_uses_line_number_cursor():
    prisma = _PrismaSpy()
    repository = BatchRepository(prisma_client=prisma)

    items = await repository.list_items_page(
        batch_id="batch-1",
        limit=200,
        after_line_number=10,
    )

    assert items == []
    assert "line_number > $2" in prisma.sql
    assert "ORDER BY line_number ASC" in prisma.sql


@pytest.mark.asyncio
async def test_acquire_scope_advisory_lock_uses_postgres_advisory_lock():
    prisma = _PrismaSpy()
    repository = BatchRepository(prisma_client=prisma)

    await repository.acquire_scope_advisory_lock(scope_type="team", scope_id="team-1")

    assert "pg_advisory_xact_lock" in prisma.sql
    assert "hashtext($1)" in prisma.sql
    assert "$3::bigint" in prisma.sql
    assert prisma.params == ("team", "team-1", advisory_lock_key("batch_scope", "team", "team-1"))


@pytest.mark.asyncio
async def test_create_items_uses_bulk_insert_statement():
    prisma = _PrismaSpy()
    repository = BatchRepository(prisma_client=prisma)

    inserted = await repository.create_items(
        "batch-1",
        [
            type("Item", (), {"line_number": 1, "custom_id": "c1", "request_body": {"model": "m1"}})(),
            type("Item", (), {"line_number": 2, "custom_id": "c2", "request_body": {"model": "m2"}})(),
        ],
    )

    assert inserted == 0
    assert (
        "VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7, $8, $9, $10::timestamp), "
        "($11, $12, $13, $14, $15, $16::jsonb, $17, $18, $19, $20::timestamp)"
    ) in prisma.sql
    assert "ON CONFLICT (batch_id, line_number) DO NOTHING" in prisma.sql


@pytest.mark.asyncio
async def test_expired_file_gc_excludes_files_referenced_by_create_sessions() -> None:
    prisma = _PrismaSpy()
    repository = BatchRepository(prisma_client=prisma)

    files = await repository.files.list_expired_unreferenced_files(now=datetime.now(tz=UTC), limit=50)

    assert files == []
    assert "FROM deltallm_batch_create_session s" in prisma.sql
    assert "WHERE s.input_file_id = f.file_id" in prisma.sql


@pytest.mark.asyncio
async def test_create_job_rejects_invalid_status_before_sql() -> None:
    prisma = _PrismaSpy()
    repository = BatchRepository(prisma_client=prisma)

    with pytest.raises(ValueError, match="batch job status"):
        await repository.create_job(
            endpoint="/v1/embeddings",
            input_file_id="file-1",
            model="m1",
            metadata=None,
            created_by_api_key="key-1",
            created_by_user_id=None,
            created_by_team_id=None,
            expires_at=None,
            status="broken",
        )

    assert prisma.queries == []


@pytest.mark.asyncio
async def test_create_job_defaults_to_queued_status() -> None:
    prisma = _PrismaSpy()
    repository = BatchRepository(prisma_client=prisma)

    await repository.create_job(
        endpoint="/v1/embeddings",
        input_file_id="file-1",
        model="m1",
        metadata=None,
        created_by_api_key="key-1",
        created_by_user_id=None,
        created_by_team_id=None,
        expires_at=None,
    )

    assert '::"DeltaLLM_BatchJobStatus"' in prisma.sql
    assert prisma.params[2] == BatchJobStatus.QUEUED.value
    assert len(prisma.params) == 25
    assert "scheduler_version, scheduling_model, scheduling_model_group" in prisma.sql


@pytest.mark.asyncio
async def test_create_job_hashes_api_key_tenant_scope_id() -> None:
    class _CreateJobPrisma(_PrismaSpy):
        async def query_raw(self, sql: str, *params):
            self.sql = sql
            self.params = params
            self.queries.append(sql)
            if "INSERT INTO deltallm_batch_scheduler_flow" in sql:
                return []
            return [
                {
                    "batch_id": params[0],
                    "endpoint": params[1],
                    "status": params[2],
                    "execution_mode": params[3],
                    "input_file_id": params[4],
                    "model": params[5],
                    "metadata": params[6],
                    "total_items": params[7],
                    "scheduler_version": params[8],
                    "scheduling_model": params[9],
                    "scheduling_model_group": params[10],
                    "scheduling_endpoint": params[11],
                    "tenant_scope_type": params[12],
                    "tenant_scope_id": params[13],
                    "service_tier": params[14],
                    "estimated_work_units": params[15],
                    "remaining_work_units": params[16],
                    "size_class": params[17],
                    "queue_entered_at": params[18],
                    "scheduler_debug": params[19],
                    "created_by_api_key": params[20],
                    "created_at": datetime.now(tz=UTC),
                }
            ]

    prisma = _CreateJobPrisma()
    repository = BatchRepository(prisma_client=prisma)

    job = await repository.create_job(
        endpoint="/v1/embeddings",
        input_file_id="file-1",
        model="m1",
        metadata=None,
        created_by_api_key="sk-test-secret",
        created_by_user_id=None,
        created_by_team_id=None,
        expires_at=None,
    )

    assert job is not None
    assert job.tenant_scope_type == "api_key"
    assert job.tenant_scope_id is not None
    assert job.tenant_scope_id.startswith("api_key_sha256:")
    assert "sk-test-secret" not in job.tenant_scope_id


@pytest.mark.asyncio
async def test_create_job_uses_repository_tenant_scope_preference_by_default() -> None:
    class _CreateJobPrisma(_PrismaSpy):
        async def query_raw(self, sql: str, *params):
            self.sql = sql
            self.params = params
            self.queries.append(sql)
            if "INSERT INTO deltallm_batch_scheduler_flow" in sql:
                return []
            return [
                {
                    "batch_id": params[0],
                    "endpoint": params[1],
                    "status": params[2],
                    "execution_mode": params[3],
                    "input_file_id": params[4],
                    "model": params[5],
                    "metadata": params[6],
                    "total_items": params[7],
                    "scheduler_version": params[8],
                    "scheduling_model": params[9],
                    "scheduling_model_group": params[10],
                    "scheduling_endpoint": params[11],
                    "tenant_scope_type": params[12],
                    "tenant_scope_id": params[13],
                    "service_tier": params[14],
                    "estimated_work_units": params[15],
                    "remaining_work_units": params[16],
                    "size_class": params[17],
                    "queue_entered_at": params[18],
                    "scheduler_debug": params[19],
                    "created_by_api_key": params[20],
                    "created_by_team_id": params[22],
                    "created_by_organization_id": params[23],
                    "created_at": datetime.now(tz=UTC),
                }
            ]

    prisma = _CreateJobPrisma()
    repository = BatchRepository(
        prisma_client=prisma,
        tenant_scope_preference="api_key,team,organization",
    )

    job = await repository.create_job(
        endpoint="/v1/embeddings",
        input_file_id="file-1",
        model="m1",
        metadata=None,
        created_by_api_key="sk-preferred-secret",
        created_by_user_id=None,
        created_by_team_id="team-1",
        created_by_organization_id="org-1",
        expires_at=None,
    )

    assert job is not None
    assert job.tenant_scope_type == "api_key"
    assert job.tenant_scope_id == stable_tenant_scope_id(
        scope_type="api_key",
        scope_id="sk-preferred-secret",
    )
    assert job.tenant_scope_id != "team-1"


@pytest.mark.asyncio
async def test_claim_next_job_observes_queue_wait_only_for_first_queued_claim(monkeypatch: pytest.MonkeyPatch) -> None:
    class _ClaimPrisma:
        async def query_raw(self, sql: str, *params):  # noqa: ANN001
            del sql, params
            return [
                {
                    "batch_id": "batch-1",
                    "endpoint": "/v1/embeddings",
                    "status": "in_progress",
                    "execution_mode": "managed_internal",
                    "input_file_id": "file-1",
                    "model": "m1",
                    "metadata": "{}",
                    "total_items": 1,
                    "created_by_api_key": "key-1",
                    "created_at": datetime.now() - timedelta(seconds=30),
                    "queue_entered_at": datetime.now() - timedelta(seconds=20),
                    "scheduling_model_group": "m1",
                    "service_tier": "standard",
                    "size_class": "xs",
                    "previous_status": "queued",
                    "previous_first_claimed_at": None,
                }
            ]

    observations: list[dict[str, object]] = []
    monkeypatch.setattr(
        job_repository_module,
        "observe_batch_queue_wait",
        lambda **kwargs: observations.append(kwargs),
    )
    repository = BatchRepository(prisma_client=_ClaimPrisma())

    job = await repository.claim_next_job(worker_id="worker-1", lease_seconds=120)

    assert job is not None
    assert observations
    assert observations[0]["model_group"] == "m1"
    assert float(observations[0]["wait_seconds"]) >= 0.0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("previous_status", "previous_first_claimed_at"),
    [
        ("in_progress", None),
        ("finalizing", None),
        ("queued", datetime.now(tz=UTC)),
    ],
)
async def test_claim_next_job_skips_queue_wait_for_reclaims_and_repeated_claims(
    monkeypatch: pytest.MonkeyPatch,
    previous_status: str,
    previous_first_claimed_at: datetime | None,
) -> None:
    class _ClaimPrisma:
        async def query_raw(self, sql: str, *params):  # noqa: ANN001
            del sql, params
            return [
                {
                    "batch_id": "batch-1",
                    "endpoint": "/v1/embeddings",
                    "status": "in_progress",
                    "execution_mode": "managed_internal",
                    "input_file_id": "file-1",
                    "model": "m1",
                    "metadata": "{}",
                    "total_items": 1,
                    "created_by_api_key": "key-1",
                    "created_at": datetime.now(tz=UTC),
                    "queue_entered_at": datetime.now(tz=UTC) - timedelta(seconds=20),
                    "scheduling_model_group": "m1",
                    "service_tier": "standard",
                    "size_class": "xs",
                    "previous_status": previous_status,
                    "previous_first_claimed_at": previous_first_claimed_at,
                }
            ]

    observations: list[dict[str, object]] = []
    monkeypatch.setattr(
        job_repository_module,
        "observe_batch_queue_wait",
        lambda **kwargs: observations.append(kwargs),
    )
    repository = BatchRepository(prisma_client=_ClaimPrisma())

    await repository.claim_next_job(worker_id="worker-1", lease_seconds=120)

    assert observations == []


@pytest.mark.asyncio
async def test_set_job_queued_casts_status_parameter_to_enum() -> None:
    prisma = _PrismaSpy()
    repository = BatchRepository(prisma_client=prisma)

    queued = await repository.set_job_queued("batch-1", 2)

    assert queued is None
    assert 'status = $2::"DeltaLLM_BatchJobStatus"' in prisma.sql


@pytest.mark.asyncio
async def test_set_job_queued_repairs_missing_api_key_tenant_scope_id() -> None:
    class _SetQueuedPrisma:
        def __init__(self) -> None:
            self.calls: list[tuple[str, tuple[object, ...]]] = []
            self.first_row = _job_row(
                created_by_api_key="sk-test-secret",
                tenant_scope_type="api_key",
                tenant_scope_id=None,
            )

        async def query_raw(self, sql: str, *params):
            self.calls.append((sql, params))
            if len(self.calls) == 1:
                return [self.first_row]
            if "INSERT INTO deltallm_batch_scheduler_flow" in sql:
                return []
            repaired = dict(self.first_row)
            repaired["tenant_scope_id"] = params[1]
            return [repaired]

    prisma = _SetQueuedPrisma()
    repository = BatchRepository(prisma_client=prisma)

    queued = await repository.set_job_queued("batch-1", 2)

    expected_scope_id = stable_tenant_scope_id(scope_type="api_key", scope_id="sk-test-secret")
    assert queued is not None
    assert queued.tenant_scope_id == expected_scope_id
    assert len(prisma.calls) == 3
    assert prisma.calls[1][1][1] == expected_scope_id
    assert "tenant_scope_id NOT LIKE $3" in prisma.calls[1][0]
    assert "INSERT INTO deltallm_batch_scheduler_flow" in prisma.calls[2][0]


@pytest.mark.asyncio
async def test_set_job_queued_repairs_raw_api_key_tenant_scope_id() -> None:
    class _SetQueuedPrisma:
        def __init__(self) -> None:
            self.calls: list[tuple[str, tuple[object, ...]]] = []
            self.first_row = _job_row(
                created_by_api_key="sk-created-secret",
                tenant_scope_type="api_key",
                tenant_scope_id="sk-existing-raw-secret",
            )

        async def query_raw(self, sql: str, *params):
            self.calls.append((sql, params))
            if len(self.calls) == 1:
                return [self.first_row]
            if "INSERT INTO deltallm_batch_scheduler_flow" in sql:
                return []
            repaired = dict(self.first_row)
            repaired["tenant_scope_id"] = params[1]
            return [repaired]

    prisma = _SetQueuedPrisma()
    repository = BatchRepository(prisma_client=prisma)

    queued = await repository.set_job_queued("batch-1", 2)

    expected_scope_id = stable_tenant_scope_id(
        scope_type="api_key",
        scope_id="sk-existing-raw-secret",
    )
    assert queued is not None
    assert queued.tenant_scope_id == expected_scope_id
    assert len(prisma.calls) == 3
    assert "sk-existing-raw-secret" not in queued.tenant_scope_id
    assert prisma.calls[1][1][2] == f"{API_KEY_TENANT_SCOPE_PREFIX}%"
    assert "INSERT INTO deltallm_batch_scheduler_flow" in prisma.calls[2][0]


@pytest.mark.asyncio
async def test_set_job_queued_skips_tenant_scope_repair_for_team_scope() -> None:
    class _SetQueuedPrisma:
        def __init__(self) -> None:
            self.calls: list[tuple[str, tuple[object, ...]]] = []

        async def query_raw(self, sql: str, *params):
            self.calls.append((sql, params))
            if "INSERT INTO deltallm_batch_scheduler_flow" in sql:
                return []
            return [_job_row(created_by_team_id="team-1", tenant_scope_type="team", tenant_scope_id="team-1")]

    prisma = _SetQueuedPrisma()
    repository = BatchRepository(prisma_client=prisma)

    queued = await repository.set_job_queued("batch-1", 2)

    assert queued is not None
    assert queued.tenant_scope_id == "team-1"
    assert len(prisma.calls) == 2
    assert "INSERT INTO deltallm_batch_scheduler_flow" in prisma.calls[1][0]


@pytest.mark.asyncio
async def test_backfill_scheduler_dimensions_repairs_active_rows_with_estimator_and_lock() -> None:
    class _Router:
        def resolve_model_group(self, model_name: str) -> str:
            assert model_name == "m1"
            return "group-m1"

    class _BackfillPrisma:
        def __init__(self) -> None:
            self.calls: list[tuple[str, tuple[object, ...]]] = []

        async def query_raw(self, sql: str, *params):
            self.calls.append((sql, params))
            if "pg_try_advisory_xact_lock" in sql:
                return [{"acquired": True}]
            if "SELECT * FROM candidate" in sql and "FROM deltallm_batch_item" in sql:
                return [
                    {
                        "item_id": "item-1",
                        "request_body": {"model": "m1", "input": "a" * 257},
                        "scheduling_model": None,
                        "scheduling_model_group": None,
                        "estimated_work_units": 1,
                        "endpoint": "/v1/embeddings",
                        "job_model": "m1",
                        "line_number": 1,
                    }
                ]
            if "WITH payload(item_id" in sql:
                return [{"item_id": params[0]}]
            if "WITH field_candidate_jobs AS" in sql:
                return [
                    {
                        "batch_id": "batch-1",
                        "endpoint": "/v1/embeddings",
                        "model": "m1",
                        "created_by_organization_id": None,
                        "created_by_team_id": None,
                        "created_by_api_key": "sk-created-secret",
                        "created_by_user_id": None,
                        "tenant_scope_type": "api_key",
                        "tenant_scope_id": "sk-existing-raw-secret",
                        "service_tier": "standard",
                        "estimated_work_units": 0,
                        "remaining_work_units": 0,
                        "total_items": 2,
                        "created_at": datetime.now(tz=UTC),
                        "item_estimated_work_units": 2,
                        "item_remaining_work_units": 2,
                        "missing_dimension_items": 0,
                        "distinct_models": 1,
                        "distinct_model_groups": 0,
                        "item_scheduling_model": "m1",
                        "item_scheduling_model_group": None,
                    }
                ]
            return [{"batch_id": params[0]}]

    prisma = _BackfillPrisma()
    repository = BatchRepository(prisma_client=prisma, model_group_resolver=_Router())

    result = await repository.backfill_scheduler_dimensions(limit=500)

    expected_scope_id = stable_tenant_scope_id(
        scope_type="api_key",
        scope_id="sk-existing-raw-secret",
    )
    assert result == {"jobs": 1, "items": 1}
    item_select_sql, item_select_params = prisma.calls[1]
    item_update_sql, item_update_params = prisma.calls[2]
    select_sql, select_params = prisma.calls[3]
    update_sql, update_params = prisma.calls[4]
    assert "JOIN deltallm_batch_job j ON j.batch_id = i.batch_id" in item_select_sql
    assert "j.status IN ('queued', 'in_progress', 'finalizing')" in item_select_sql
    assert "FOR UPDATE OF i SKIP LOCKED" in item_select_sql
    assert item_select_params == (500,)
    assert "NULLIF(i.scheduling_model, '')" in item_update_sql
    assert "WITH payload(item_id, scheduling_model, scheduling_model_group, estimated_work_units)" in item_update_sql
    assert item_update_params[1] == "m1"
    assert item_update_params[2] == "group-m1"
    assert item_update_params[3] == 2
    assert "field_candidate_jobs AS" in select_sql
    assert "aggregate_candidate_jobs AS" in select_sql
    assert "aggregate_drift_scan_jobs AS" in select_sql
    assert "aggregate_drift_item_stats AS" in select_sql
    assert "aggregate_drift_candidate_jobs AS" in select_sql
    assert "tenant_scope_scan_jobs AS" in select_sql
    assert "candidate_jobs AS" in select_sql
    assert "WITH scanned_jobs AS" not in select_sql
    assert "JOIN candidate_jobs s ON s.batch_id = i.batch_id" in select_sql
    assert "status IN ('queued', 'in_progress', 'finalizing')" in select_sql
    assert "missing_dimension_items" in select_sql
    assert "ci.missing_dimension_items = 0" in select_sql
    assert "j.scheduler_debug->>'estimator_version' IS DISTINCT FROM $3" in select_sql
    assert "j.scheduler_debug->>'tenant_scope_preference' IS DISTINCT FROM $5" in select_sql
    assert "j.estimated_work_units IS DISTINCT FROM COALESCE(j.total_items, 0)" not in select_sql
    assert "ci.estimated_work_units IS DISTINCT FROM ci.derived_estimated_work_units" in select_sql
    assert "OR ci.candidate_priority = 1" in select_sql
    assert "ORDER BY ci.candidate_priority ASC" in select_sql
    assert "FOR UPDATE OF j SKIP LOCKED" in select_sql
    assert select_params == (
        500,
        5000,
        "v1",
        f"{API_KEY_TENANT_SCOPE_PREFIX}%",
        "organization,team,api_key,user",
    )
    assert update_params[5] == "api_key"
    assert update_params[6] == expected_scope_id
    assert update_params[8] == 2
    assert update_params[9] == 2
    assert update_params[3] == "group-m1"
    scheduler_debug = json.loads(str(update_params[12]))
    assert scheduler_debug["estimator_version"] == "v1"
    assert scheduler_debug["tenant_scope_preference"] == "organization,team,api_key,user"
    assert update_params[13] == f"{API_KEY_TENANT_SCOPE_PREFIX}%"
    assert "scheduling_model = $3" in update_sql
    assert "tenant_scope_type = $6" in update_sql
    assert "tenant_scope_id = $7" in update_sql
    assert "tenant_scope_id NOT LIKE $14" in update_sql
    assert "estimated_work_units = GREATEST($9, 0)" in update_sql
    assert "remaining_work_units = GREATEST($10, 0)" in update_sql
    assert "status IN ('queued', 'in_progress', 'finalizing')" in update_sql
    assert "'completed'" not in update_sql


@pytest.mark.asyncio
async def test_backfill_scheduler_dimensions_uses_configured_tenant_scope_preference() -> None:
    now = datetime.now(tz=UTC)

    class _BackfillPrisma:
        def __init__(self) -> None:
            self.calls: list[tuple[str, tuple[object, ...]]] = []

        async def query_raw(self, sql: str, *params):
            self.calls.append((sql, params))
            if "pg_try_advisory_xact_lock" in sql:
                return [{"acquired": True}]
            if "SELECT * FROM candidate" in sql and "FROM deltallm_batch_item" in sql:
                return []
            if "WITH field_candidate_jobs AS" in sql:
                return [
                    {
                        "batch_id": "batch-tenant-preference",
                        "endpoint": "/v1/embeddings",
                        "model": "m1",
                        "created_by_organization_id": "org-1",
                        "created_by_team_id": "team-1",
                        "created_by_api_key": "sk-preferred-secret",
                        "created_by_user_id": "user-1",
                        "tenant_scope_type": None,
                        "tenant_scope_id": None,
                        "service_tier": "standard",
                        "estimated_work_units": 0,
                        "remaining_work_units": 0,
                        "total_items": 1,
                        "created_at": now,
                        "item_estimated_work_units": 1,
                        "item_remaining_work_units": 1,
                        "missing_dimension_items": 0,
                        "distinct_models": 1,
                        "distinct_model_groups": 1,
                        "item_scheduling_model": "m1",
                        "item_scheduling_model_group": "m1",
                    }
                ]
            if "UPDATE deltallm_batch_job" in sql:
                return [{"batch_id": params[0]}]
            return []

    prisma = _BackfillPrisma()
    repository = BatchRepository(
        prisma_client=prisma,
        tenant_scope_preference=("api_key", "team", "organization", "user"),
    )

    result = await repository.backfill_scheduler_dimensions(limit=500)

    expected_scope_id = stable_tenant_scope_id(
        scope_type="api_key",
        scope_id="sk-preferred-secret",
    )
    update_sql, update_params = next(
        (sql, params) for sql, params in prisma.calls if "UPDATE deltallm_batch_job" in sql
    )
    assert result == {"jobs": 1, "items": 0}
    assert update_params[5] == "api_key"
    assert update_params[6] == expected_scope_id
    assert "tenant_scope_type = $6" in update_sql
    assert "tenant_scope_type IS DISTINCT FROM $6" in update_sql
    scheduler_debug = json.loads(str(update_params[12]))
    assert scheduler_debug["tenant_scope_preference"] == "api_key,team,organization,user"
    assert "sk-preferred-secret" not in expected_scope_id


@pytest.mark.asyncio
async def test_backfill_scheduler_dimensions_rewrites_stale_tenant_scope_fields_with_current_debug() -> None:
    now = datetime.now(tz=UTC)

    class _BackfillPrisma:
        def __init__(self) -> None:
            self.calls: list[tuple[str, tuple[object, ...]]] = []

        async def query_raw(self, sql: str, *params):
            self.calls.append((sql, params))
            if "pg_try_advisory_xact_lock" in sql:
                return [{"acquired": True}]
            if "SELECT * FROM candidate" in sql and "FROM deltallm_batch_item" in sql:
                return []
            if "WITH field_candidate_jobs AS" in sql:
                return [
                    {
                        "batch_id": "batch-stale-preference",
                        "endpoint": "/v1/embeddings",
                        "model": "m1",
                        "created_by_organization_id": "org-1",
                        "created_by_team_id": "team-1",
                        "created_by_api_key": "sk-preferred-secret",
                        "created_by_user_id": "user-1",
                        "tenant_scope_type": "team",
                        "tenant_scope_id": "team-1",
                        "service_tier": "standard",
                        "estimated_work_units": 1,
                        "remaining_work_units": 1,
                        "total_items": 1,
                        "created_at": now,
                        "scheduler_debug": {
                            "estimator_version": "v1",
                            "tenant_scope_preference": "api_key,team,organization,user",
                        },
                        "item_estimated_work_units": 1,
                        "item_remaining_work_units": 1,
                        "missing_dimension_items": 0,
                        "distinct_models": 1,
                        "distinct_model_groups": 1,
                        "item_scheduling_model": "m1",
                        "item_scheduling_model_group": "m1",
                    }
                ]
            if "UPDATE deltallm_batch_job" in sql:
                return [{"batch_id": params[0]}]
            return []

    prisma = _BackfillPrisma()
    repository = BatchRepository(
        prisma_client=prisma,
        tenant_scope_preference=("api_key", "team", "organization", "user"),
    )

    result = await repository.backfill_scheduler_dimensions(limit=500)

    expected_scope_id = stable_tenant_scope_id(
        scope_type="api_key",
        scope_id="sk-preferred-secret",
    )
    update_sql, update_params = next(
        (sql, params) for sql, params in prisma.calls if "UPDATE deltallm_batch_job" in sql
    )
    assert result == {"jobs": 1, "items": 0}
    assert update_params[5] == "api_key"
    assert update_params[6] == expected_scope_id
    select_sql, _select_params = next(
        (sql, params) for sql, params in prisma.calls if "WITH field_candidate_jobs AS" in sql
    )
    assert "tenant_scope_scan_jobs AS" in select_sql
    assert "OR ci.candidate_priority = 1" in select_sql
    assert "tenant_scope_id IS DISTINCT FROM $7" in update_sql
    assert (
        "scheduler_debug->>'tenant_scope_preference' IS DISTINCT FROM "
        "$13::jsonb->>'tenant_scope_preference'"
    ) in update_sql


@pytest.mark.asyncio
async def test_backfill_scheduler_dimensions_uses_targeted_candidates_before_job_repair_limit() -> None:
    class _BackfillPrisma:
        def __init__(self) -> None:
            self.calls: list[tuple[str, tuple[object, ...]]] = []

        async def query_raw(self, sql: str, *params):
            self.calls.append((sql, params))
            if "pg_try_advisory_xact_lock" in sql:
                return [{"acquired": True}]
            if "SELECT * FROM candidate" in sql and "FROM deltallm_batch_item" in sql:
                return []
            if "WITH field_candidate_jobs AS" in sql:
                return [
                    {
                        "batch_id": "batch-later",
                        "endpoint": "/v1/embeddings",
                        "model": "m1",
                        "created_by_organization_id": None,
                        "created_by_team_id": "team-1",
                        "created_by_api_key": None,
                        "created_by_user_id": None,
                        "tenant_scope_type": "team",
                        "tenant_scope_id": "team-1",
                        "service_tier": "standard",
                        "estimated_work_units": 0,
                        "remaining_work_units": 0,
                        "total_items": 1,
                        "created_at": datetime.now(tz=UTC),
                        "item_estimated_work_units": 1,
                        "item_remaining_work_units": 1,
                        "missing_dimension_items": 0,
                        "distinct_models": 1,
                        "distinct_model_groups": 1,
                        "item_scheduling_model": "m1",
                        "item_scheduling_model_group": "m1",
                    }
                ]
            if "UPDATE deltallm_batch_job" in sql:
                return [{"batch_id": params[0]}]
            return []

    prisma = _BackfillPrisma()
    repository = BatchRepository(prisma_client=prisma)

    result = await repository.backfill_scheduler_dimensions(limit=1)

    select_sql, select_params = next(
        (sql, params) for sql, params in prisma.calls if "WITH field_candidate_jobs AS" in sql
    )
    assert result == {"jobs": 1, "items": 0}
    assert select_params == (
        1,
        10,
        "v1",
        f"{API_KEY_TENANT_SCOPE_PREFIX}%",
        "organization,team,api_key,user",
    )
    assert "field_candidate_jobs AS" in select_sql
    assert "aggregate_candidate_jobs AS" in select_sql
    assert "aggregate_drift_candidate_jobs AS" in select_sql
    assert "tenant_scope_scan_jobs AS" in select_sql
    assert "candidate_jobs AS" in select_sql
    assert "WITH scanned_jobs AS" not in select_sql
    assert "LIMIT $2" in select_sql
    assert "ci.missing_dimension_items = 0" in select_sql
    assert "FOR UPDATE OF j SKIP LOCKED" in select_sql


@pytest.mark.asyncio
async def test_backfill_scheduler_dimensions_filters_targeted_refresh_by_flow() -> None:
    class _Router:
        config = SimpleNamespace(model_group_alias={"model-a": "group-a"})

    class _BackfillPrisma:
        def __init__(self) -> None:
            self.calls: list[tuple[str, tuple[object, ...]]] = []

        async def query_raw(self, sql: str, *params):
            self.calls.append((sql, params))
            if "pg_try_advisory_xact_lock" in sql:
                return [{"acquired": True}]
            return []

    prisma = _BackfillPrisma()
    repository = BatchRepository(prisma_client=prisma, model_group_resolver=_Router())

    result = await repository.backfill_scheduler_dimensions(
        limit=10,
        service_tier="standard",
        model_group="group-a",
    )

    item_select_sql, item_select_params = prisma.calls[1]
    job_select_sql, job_select_params = prisma.calls[2]
    assert result == {"jobs": 0, "items": 0}
    assert item_select_params == (10, ["group-a", "model-a"], "standard")
    assert (
        "COALESCE(NULLIF(i.scheduling_model_group, ''), "
        "NULLIF(j.scheduling_model_group, ''), NULLIF(i.scheduling_model, ''), "
        "NULLIF(i.request_body->>'model', ''), NULLIF(j.model, '')) = ANY($2::text[])"
    ) in item_select_sql
    assert "COALESCE(NULLIF(j.service_tier, ''), 'standard') = $3" in item_select_sql
    assert job_select_params == (
        10,
        100,
        "v1",
        f"{API_KEY_TENANT_SCOPE_PREFIX}%",
        "organization,team,api_key,user",
        ["group-a", "model-a"],
        "standard",
    )
    assert (
        "COALESCE(NULLIF(j.scheduling_model_group, ''), NULLIF(j.model, '')) = "
        "ANY($6::text[])"
    ) in job_select_sql
    assert "COALESCE(NULLIF(j.service_tier, ''), 'standard') = $7" in job_select_sql
    assert job_select_sql.count(
        "COALESCE(NULLIF(j.scheduling_model_group, ''), NULLIF(j.model, '')) = "
        "ANY($6::text[])"
    ) == 4
    assert job_select_sql.count("COALESCE(NULLIF(j.service_tier, ''), 'standard') = $7") == 4


@pytest.mark.asyncio
async def test_backfill_scheduler_dimensions_repairs_complete_job_aggregate_drift() -> None:
    class _BackfillPrisma:
        def __init__(self) -> None:
            self.calls: list[tuple[str, tuple[object, ...]]] = []

        async def query_raw(self, sql: str, *params):
            self.calls.append((sql, params))
            if "pg_try_advisory_xact_lock" in sql:
                return [{"acquired": True}]
            if "SELECT * FROM candidate" in sql and "FROM deltallm_batch_item" in sql:
                return []
            if "WITH field_candidate_jobs AS" in sql and "aggregate_drift_candidate_jobs AS" in sql:
                return [
                    {
                        "batch_id": "batch-1",
                        "endpoint": "/v1/embeddings",
                        "model": "m1",
                        "created_by_organization_id": None,
                        "created_by_team_id": "team-1",
                        "created_by_api_key": None,
                        "created_by_user_id": None,
                        "tenant_scope_type": "team",
                        "tenant_scope_id": "team-1",
                        "service_tier": "standard",
                        "estimated_work_units": 1_000,
                        "remaining_work_units": 1_000,
                        "size_class": "m",
                        "total_items": 2,
                        "created_at": datetime.now(tz=UTC),
                        "item_count": 2,
                        "item_estimated_work_units": 10,
                        "item_remaining_work_units": 4,
                        "missing_dimension_items": 0,
                        "distinct_models": 1,
                        "distinct_model_groups": 1,
                        "item_scheduling_model": "m1",
                        "item_scheduling_model_group": "m1",
                    }
                ]
            if "UPDATE deltallm_batch_job" in sql:
                return [{"batch_id": params[0]}]
            return []

    prisma = _BackfillPrisma()
    repository = BatchRepository(prisma_client=prisma)

    result = await repository.backfill_scheduler_dimensions(limit=500)

    assert result == {"jobs": 1, "items": 0}
    select_sql, _select_params = next(
        (sql, params) for sql, params in prisma.calls if "WITH field_candidate_jobs AS" in sql
    )
    update_sql, update_params = next(
        (sql, params) for sql, params in prisma.calls if "UPDATE deltallm_batch_job" in sql
    )
    assert "ci.estimated_work_units IS DISTINCT FROM ci.derived_estimated_work_units" in select_sql
    assert "ci.remaining_work_units IS DISTINCT FROM ci.derived_remaining_work_units" in select_sql
    assert "ci.size_class IS DISTINCT FROM" in select_sql
    assert "aggregate_drift_scan_jobs AS" in select_sql
    assert "aggregate_drift_item_stats AS" in select_sql
    assert "JOIN aggregate_drift_scan_jobs s ON s.batch_id = i.batch_id" in select_sql
    assert "dis.missing_dimension_items = 0" in select_sql
    assert "j.estimated_work_units IS DISTINCT FROM dis.estimated_work_units" in select_sql
    assert "FROM aggregate_drift_candidate_jobs" in select_sql
    assert update_params[8] == 10
    assert update_params[9] == 4
    assert update_params[10] == "xs"
    assert "estimated_work_units = GREATEST($9, 0)" in update_sql


@pytest.mark.asyncio
async def test_backfill_scheduler_dimensions_skips_job_until_all_items_are_normalized() -> None:
    class _BackfillPrisma:
        def __init__(self) -> None:
            self.calls: list[tuple[str, tuple[object, ...]]] = []

        async def query_raw(self, sql: str, *params):
            self.calls.append((sql, params))
            if "pg_try_advisory_xact_lock" in sql:
                return [{"acquired": True}]
            if "SELECT * FROM candidate" in sql and "FROM deltallm_batch_item" in sql:
                return []
            if "WITH field_candidate_jobs AS" in sql:
                return [
                    {
                        "batch_id": "batch-1",
                        "endpoint": "/v1/embeddings",
                        "model": "m1",
                        "created_by_organization_id": None,
                        "created_by_team_id": "team-1",
                        "created_by_api_key": None,
                        "created_by_user_id": None,
                        "tenant_scope_type": "team",
                        "tenant_scope_id": "team-1",
                        "service_tier": "standard",
                        "estimated_work_units": 1,
                        "remaining_work_units": 1,
                        "total_items": 10_000,
                        "created_at": datetime.now(tz=UTC),
                        "item_estimated_work_units": 500,
                        "item_remaining_work_units": 500,
                        "missing_dimension_items": 9_500,
                        "distinct_models": 1,
                        "distinct_model_groups": 1,
                        "item_scheduling_model": "m1",
                        "item_scheduling_model_group": "m1",
                    }
                ]
            if "UPDATE deltallm_batch_job" in sql:
                raise AssertionError("job aggregate must not be stamped from partial item data")
            return []

    prisma = _BackfillPrisma()
    repository = BatchRepository(prisma_client=prisma)

    result = await repository.backfill_scheduler_dimensions(limit=500)

    assert result == {"jobs": 0, "items": 0}
    assert not any("UPDATE deltallm_batch_job" in sql for sql, _params in prisma.calls)


@pytest.mark.asyncio
async def test_backfill_scheduler_dimensions_marks_legacy_mixed_model_jobs_from_items() -> None:
    class _BackfillPrisma:
        def __init__(self) -> None:
            self.calls: list[tuple[str, tuple[object, ...]]] = []

        async def query_raw(self, sql: str, *params):
            self.calls.append((sql, params))
            if "pg_try_advisory_xact_lock" in sql:
                return [{"acquired": True}]
            if "SELECT * FROM candidate" in sql and "FROM deltallm_batch_item" in sql:
                return []
            if "WITH field_candidate_jobs AS" in sql:
                return [
                    {
                        "batch_id": "batch-1",
                        "endpoint": "/v1/chat/completions",
                        "model": "legacy-job-model",
                        "created_by_organization_id": None,
                        "created_by_team_id": "team-1",
                        "created_by_api_key": None,
                        "created_by_user_id": None,
                        "tenant_scope_type": "team",
                        "tenant_scope_id": "team-1",
                        "service_tier": "standard",
                        "estimated_work_units": 1,
                        "remaining_work_units": 1,
                        "total_items": 2,
                        "created_at": datetime.now(tz=UTC),
                        "item_estimated_work_units": 9,
                        "item_remaining_work_units": 7,
                        "missing_dimension_items": 0,
                        "distinct_models": 2,
                        "distinct_model_groups": 2,
                        "item_scheduling_model": "model-a",
                        "item_scheduling_model_group": "group-a",
                    }
                ]
            if "UPDATE deltallm_batch_job" in sql:
                return [{"batch_id": params[0]}]
            return []

    prisma = _BackfillPrisma()
    repository = BatchRepository(prisma_client=prisma)

    result = await repository.backfill_scheduler_dimensions(limit=500)

    assert result == {"jobs": 1, "items": 0}
    update_sql, update_params = next(
        (sql, params) for sql, params in prisma.calls if "UPDATE deltallm_batch_job" in sql
    )
    assert update_params[2] == MIXED_MODEL_GROUP
    assert update_params[3] == MIXED_MODEL_GROUP
    assert update_params[8] == 9
    assert update_params[9] == 7
    assert json.loads(str(update_params[12]))["mixed_model"] is True
    assert "scheduling_model = $3" in update_sql


@pytest.mark.asyncio
async def test_backfill_scheduler_dimensions_skips_when_lock_is_held() -> None:
    class _LockedTx:
        def __init__(self) -> None:
            self.calls: list[tuple[str, tuple[object, ...]]] = []

        async def query_raw(self, sql: str, *params):
            self.calls.append((sql, params))
            return [{"acquired": False}]

    class _Prisma:
        def __init__(self) -> None:
            self.tx_client = _LockedTx()
            self.tx_entered = False

        @asynccontextmanager
        async def tx(self):
            self.tx_entered = True
            yield self.tx_client

        async def query_raw(self, sql: str, *params):
            del sql, params
            raise AssertionError("backfill should use the transaction client")

    prisma = _Prisma()
    repository = BatchRepository(prisma_client=prisma)

    result = await repository.backfill_scheduler_dimensions(limit=500)

    assert result == {"jobs": 0, "items": 0, "skipped": 1}
    assert prisma.tx_entered is True
    assert len(prisma.tx_client.calls) == 1
    assert "pg_try_advisory_xact_lock" in prisma.tx_client.calls[0][0]


@pytest.mark.asyncio
async def test_refresh_job_progress_casts_status_case_to_enum() -> None:
    prisma = _PrismaSpy()
    repository = BatchRepository(prisma_client=prisma)

    refreshed = await repository.refresh_job_progress("batch-1")

    assert refreshed is None
    assert "WHEN s.pending_items = 0 AND s.in_progress_items = 0 THEN 'finalizing'" in prisma.sql
    assert '::"DeltaLLM_BatchJobStatus"' in prisma.sql


@pytest.mark.asyncio
async def test_attach_artifacts_and_finalize_casts_status_parameter_to_enum() -> None:
    prisma = _PrismaSpy()
    repository = BatchRepository(prisma_client=prisma)

    finalized = await repository.attach_artifacts_and_finalize(
        batch_id="batch-1",
        output_file_id="out-1",
        error_file_id="err-1",
        final_status="completed",
    )

    assert finalized is None
    assert 'status = $4::"DeltaLLM_BatchJobStatus"' in prisma.sql
    assert prisma.params[3] == BatchJobStatus.COMPLETED.value


@pytest.mark.asyncio
async def test_attach_artifacts_and_finalize_rejects_invalid_status_before_sql() -> None:
    prisma = _PrismaSpy()
    repository = BatchRepository(prisma_client=prisma)

    with pytest.raises(ValueError, match="batch job status"):
        await repository.attach_artifacts_and_finalize(
            batch_id="batch-1",
            output_file_id=None,
            error_file_id=None,
            final_status="broken",
        )

    assert prisma.queries == []
