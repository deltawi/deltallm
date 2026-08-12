from __future__ import annotations

import hmac
import logging
from datetime import datetime
from typing import Any, Literal

from src.batch.claim_diagnostics import BatchClaimDecisionDiagnostic
from src.batch.create.session_repository import BatchCreateSessionRepository
from src.batch.models import (
    BatchCompletionOutboxCreate,
    BatchCompletionOutboxRecord,
    BatchCompletionOutboxStatus,
    BatchFileRecord,
    BatchItemCreate,
    BatchItemRecord,
    BatchJobRecord,
    BatchJobStatus,
    BatchFairShareClaimResult,
    BatchModelBacklogRecord,
    BatchModelInFlightRecord,
    BatchSchedulerFlowRecord,
    BatchWebhookOutboxCreate,
    BatchWebhookOutboxRecord,
    BatchWebhookQueueSummary,
    BatchWebhookReplayResult,
    BatchWebhookEventType,
    BatchWebhookConfigurationConflictError,
    BatchWorkClaim,
    BatchWorkRecommendation,
    normalize_batch_job_status,
)
from src.batch.repositories import (
    BatchCompletionOutboxRepository,
    BatchFileRepository,
    BatchItemRepository,
    BatchJobRepository,
    BatchMaintenanceRepository,
    BatchWebhookOutboxRepository,
)
from src.batch.scheduling import parse_tenant_scope_preference
from src.batch.webhooks.events import (
    batch_webhook_event_payload_sha256,
    batch_webhook_event_type_for_status,
    build_batch_webhook_event,
)
from src.metrics import increment_batch_duplicate_completion_rejection

logger = logging.getLogger(__name__)


def _prisma_client_is_transaction(client: object | None) -> bool:
    detector = getattr(client, "is_transaction", None)
    return bool(detector()) if callable(detector) else False


def _webhook_outbox_matches_terminal_job(
    record: BatchWebhookOutboxRecord,
    *,
    job: BatchJobRecord,
    event_type: BatchWebhookEventType,
) -> bool:
    """Verify the stored snapshot without comparing mutable post-terminal fields."""
    payload = record.payload_json
    data = payload.get("data") if isinstance(payload, dict) else None
    batch = data.get("batch") if isinstance(data, dict) else None
    webhook = batch.get("webhook") if isinstance(batch, dict) else None
    return bool(
        record.batch_id == job.batch_id
        and record.event_type == event_type
        and record.created_by_team_id == job.created_by_team_id
        and record.created_by_organization_id == job.created_by_organization_id
        and record.target_config_ciphertext == job.webhook_config_ciphertext
        and isinstance(payload, dict)
        and payload.get("id") == record.event_id
        and payload.get("object") == "event"
        and payload.get("type") == event_type.value
        and type(payload.get("created_at")) is int
        and isinstance(batch, dict)
        and batch.get("id") == job.batch_id
        and batch.get("object") == "batch"
        and batch.get("status") == job.status.value
        and isinstance(webhook, dict)
        and webhook.get("configured") is True
        and hmac.compare_digest(
            record.payload_sha256,
            batch_webhook_event_payload_sha256(payload),
        )
    )


class BatchRepository:
    """Compatibility facade delegating batch persistence by concern."""

    def __init__(
        self,
        prisma_client: Any | None = None,
        *,
        model_group_resolver: Any | None = None,
        tenant_scope_preference: tuple[str, ...] | list[str] | str | None = None,
        webhook_max_attempts: int = 8,
    ) -> None:
        self.prisma = prisma_client
        self.model_group_resolver = model_group_resolver
        self.tenant_scope_preference = parse_tenant_scope_preference(tenant_scope_preference)
        self.webhook_max_attempts = max(1, int(webhook_max_attempts))
        self.create_sessions = BatchCreateSessionRepository(prisma_client)
        self.files = BatchFileRepository(prisma_client)
        self.jobs = BatchJobRepository(prisma_client, model_group_resolver=model_group_resolver)
        self.items = BatchItemRepository(prisma_client)
        self.completion_outbox = BatchCompletionOutboxRepository(prisma_client)
        self.webhook_outbox = BatchWebhookOutboxRepository(prisma_client)
        self.maintenance = BatchMaintenanceRepository(
            prisma_client,
            model_group_resolver=model_group_resolver,
            tenant_scope_preference=self.tenant_scope_preference,
        )

    def with_prisma(self, prisma_client: Any | None) -> BatchRepository:
        return BatchRepository(
            prisma_client,
            model_group_resolver=self.model_group_resolver,
            tenant_scope_preference=self.tenant_scope_preference,
            webhook_max_attempts=self.webhook_max_attempts,
        )

    def set_model_group_resolver(self, model_group_resolver: Any | None) -> None:
        self.model_group_resolver = model_group_resolver
        self.jobs.model_group_resolver = model_group_resolver
        self.maintenance.model_group_resolver = model_group_resolver

    def set_tenant_scope_preference(
        self,
        tenant_scope_preference: tuple[str, ...] | list[str] | str | None,
    ) -> None:
        self.tenant_scope_preference = parse_tenant_scope_preference(tenant_scope_preference)
        self.maintenance.tenant_scope_preference = self.tenant_scope_preference

    async def create_file(
        self,
        *,
        purpose: str,
        filename: str,
        bytes_size: int,
        storage_backend: str,
        storage_key: str,
        checksum: str | None = None,
        created_by_api_key: str | None = None,
        created_by_user_id: str | None = None,
        created_by_team_id: str | None = None,
        created_by_organization_id: str | None = None,
        expires_at: datetime | None = None,
    ) -> BatchFileRecord | None:
        return await self.files.create_file(
            purpose=purpose,
            filename=filename,
            bytes_size=bytes_size,
            storage_backend=storage_backend,
            storage_key=storage_key,
            checksum=checksum,
            created_by_api_key=created_by_api_key,
            created_by_user_id=created_by_user_id,
            created_by_team_id=created_by_team_id,
            created_by_organization_id=created_by_organization_id,
            expires_at=expires_at,
        )

    async def get_file(self, file_id: str) -> BatchFileRecord | None:
        return await self.files.get_file(file_id)

    async def create_job(
        self,
        *,
        batch_id: str | None = None,
        endpoint: str,
        input_file_id: str,
        model: str | None,
        metadata: dict[str, Any] | None,
        created_by_api_key: str | None,
        created_by_user_id: str | None,
        created_by_team_id: str | None,
        created_by_organization_id: str | None = None,
        created_by_owner_account_id: str | None = None,
        created_by_owner_snapshot_complete: bool = True,
        expires_at: datetime | None = None,
        execution_mode: str = "managed_internal",
        status: str | BatchJobStatus = BatchJobStatus.QUEUED,
        total_items: int = 0,
        scheduler_version: str | None = None,
        scheduling_model: str | None = None,
        scheduling_model_group: str | None = None,
        scheduling_endpoint: str | None = None,
        tenant_scope_type: str | None = None,
        tenant_scope_id: str | None = None,
        service_tier: str | None = None,
        estimated_work_units: int | None = None,
        remaining_work_units: int | None = None,
        size_class: str | None = None,
        queue_entered_at: datetime | None = None,
        scheduler_debug: dict[str, Any] | None = None,
        webhook_config_ciphertext: str | None = None,
        webhook_config_fingerprint: str | None = None,
        tenant_scope_preference: tuple[str, ...] | list[str] | None = None,
    ) -> BatchJobRecord | None:
        effective_tenant_scope_preference = (
            tenant_scope_preference
            if tenant_scope_preference is not None
            else self.tenant_scope_preference
        )
        return await self.jobs.create_job(
            batch_id=batch_id,
            endpoint=endpoint,
            input_file_id=input_file_id,
            model=model,
            metadata=metadata,
            created_by_api_key=created_by_api_key,
            created_by_user_id=created_by_user_id,
            created_by_team_id=created_by_team_id,
            created_by_organization_id=created_by_organization_id,
            created_by_owner_account_id=created_by_owner_account_id,
            created_by_owner_snapshot_complete=created_by_owner_snapshot_complete,
            expires_at=expires_at,
            execution_mode=execution_mode,
            status=status,
            total_items=total_items,
            scheduler_version=scheduler_version,
            scheduling_model=scheduling_model,
            scheduling_model_group=scheduling_model_group,
            scheduling_endpoint=scheduling_endpoint,
            tenant_scope_type=tenant_scope_type,
            tenant_scope_id=tenant_scope_id,
            service_tier=service_tier,
            estimated_work_units=estimated_work_units,
            remaining_work_units=remaining_work_units,
            size_class=size_class,
            queue_entered_at=queue_entered_at,
            scheduler_debug=scheduler_debug,
            webhook_config_ciphertext=webhook_config_ciphertext,
            webhook_config_fingerprint=webhook_config_fingerprint,
            tenant_scope_preference=effective_tenant_scope_preference,
        )

    async def get_job(self, batch_id: str) -> BatchJobRecord | None:
        return await self.jobs.get_job(batch_id)

    async def get_job_for_update(self, batch_id: str) -> BatchJobRecord | None:
        return await self.jobs.get_job_for_update(batch_id)

    async def set_job_webhook_config_if_unset(
        self,
        *,
        batch_id: str,
        webhook_config_ciphertext: str,
        webhook_config_fingerprint: str,
    ) -> BatchJobRecord | None:
        return await self.jobs.set_webhook_config_if_unset(
            batch_id=batch_id,
            webhook_config_ciphertext=webhook_config_ciphertext,
            webhook_config_fingerprint=webhook_config_fingerprint,
        )

    async def acquire_scope_advisory_lock(self, *, scope_type: str, scope_id: str) -> None:
        await self.jobs.acquire_scope_advisory_lock(scope_type=scope_type, scope_id=scope_id)

    async def list_jobs(
        self,
        *,
        limit: int = 20,
        after: datetime | None = None,
        created_by_api_key: str | None = None,
        created_by_team_id: str | None = None,
        created_by_organization_id: str | None = None,
    ) -> list[BatchJobRecord]:
        return await self.jobs.list_jobs(
            limit=limit,
            after=after,
            created_by_api_key=created_by_api_key,
            created_by_team_id=created_by_team_id,
            created_by_organization_id=created_by_organization_id,
        )

    async def count_active_jobs_for_scope(
        self,
        *,
        created_by_api_key: str | None = None,
        created_by_team_id: str | None = None,
    ) -> int:
        return await self.jobs.count_active_jobs_for_scope(
            created_by_api_key=created_by_api_key,
            created_by_team_id=created_by_team_id,
        )

    async def summarize_runtime_statuses(self, *, now: datetime) -> dict[str, float]:
        job_summary = await self.jobs.summarize_runtime_statuses()
        scheduler_summary = await self.jobs.summarize_scheduler_queues(now=now)
        item_summary = await self.items.summarize_runtime_statuses(now=now)
        return {
            **job_summary,
            **item_summary,
            **scheduler_summary,
        }

    async def set_job_queued(self, batch_id: str, total_items: int) -> BatchJobRecord | None:
        return await self.jobs.set_job_queued(batch_id, total_items)

    async def request_cancel(self, batch_id: str) -> BatchJobRecord | None:
        return await self.jobs.request_cancel(batch_id)

    async def create_items(self, batch_id: str, items: list[BatchItemCreate]) -> int:
        return await self.items.create_items(batch_id, items)

    async def claim_next_job(
        self,
        *,
        worker_id: str,
        lease_seconds: int = 30,
        scheduler_mode: str = "fifo_v1",
    ) -> BatchJobRecord | None:
        return await self.jobs.claim_next_job(
            worker_id=worker_id,
            lease_seconds=lease_seconds,
            scheduler_mode=scheduler_mode,
        )

    async def claim_next_finalization(self, *, worker_id: str, lease_seconds: int = 30) -> BatchJobRecord | None:
        return await self.jobs.claim_next_finalization(worker_id=worker_id, lease_seconds=lease_seconds)

    async def claim_next_work(
        self,
        *,
        worker_id: str,
        max_items: int,
        max_work_units: int,
        lease_seconds: int,
        allowed_model_groups: list[str] | None = None,
        service_tier: str | None = None,
        legacy_only: bool = False,
        claim_order: str = "round_robin",
        capacity_model_group: str | None = None,
        capacity_service_tier: str | None = None,
        capacity_max_in_flight_items: int | None = None,
        capacity_max_in_flight_work_units: int | None = None,
        tenant_scope_type: str | None = None,
        tenant_scope_id: str | None = None,
        allow_oversized_first_item: bool = True,
        size_aware_scheduling_enabled: bool = False,
        aging_seconds_per_work_unit: int = 30,
        max_age_credit_work_units: int = 1_000,
        min_large_job_claim_interval_seconds: int = 30,
        small_job_max_work_units: int = 100,
        work_claim_min_items_for_microbatch: int = 4,
        scheduler_mode: str = "slice_v1",
    ) -> BatchWorkClaim | None:
        return await self.jobs.claim_next_work(
            worker_id=worker_id,
            max_items=max_items,
            max_work_units=max_work_units,
            lease_seconds=lease_seconds,
            allowed_model_groups=allowed_model_groups,
            service_tier=service_tier,
            legacy_only=legacy_only,
            claim_order=claim_order,
            capacity_model_group=capacity_model_group,
            capacity_service_tier=capacity_service_tier,
            capacity_max_in_flight_items=capacity_max_in_flight_items,
            capacity_max_in_flight_work_units=capacity_max_in_flight_work_units,
            tenant_scope_type=tenant_scope_type,
            tenant_scope_id=tenant_scope_id,
            allow_oversized_first_item=allow_oversized_first_item,
            size_aware_scheduling_enabled=size_aware_scheduling_enabled,
            aging_seconds_per_work_unit=aging_seconds_per_work_unit,
            max_age_credit_work_units=max_age_credit_work_units,
            min_large_job_claim_interval_seconds=min_large_job_claim_interval_seconds,
            small_job_max_work_units=small_job_max_work_units,
            work_claim_min_items_for_microbatch=work_claim_min_items_for_microbatch,
            scheduler_mode=scheduler_mode,
        )

    async def recommend_next_work(
        self,
        *,
        max_items: int,
        max_work_units: int,
        allowed_model_groups: list[str] | None = None,
        service_tier: str | None = None,
        legacy_only: bool = False,
        claim_order: str = "round_robin",
        capacity_model_group: str | None = None,
        capacity_service_tier: str | None = None,
        capacity_max_in_flight_items: int | None = None,
        capacity_max_in_flight_work_units: int | None = None,
        tenant_scope_type: str | None = None,
        tenant_scope_id: str | None = None,
        allow_oversized_first_item: bool = True,
        reason: str = "work_slice",
    ) -> BatchWorkRecommendation | None:
        return await self.jobs.recommend_next_work(
            max_items=max_items,
            max_work_units=max_work_units,
            allowed_model_groups=allowed_model_groups,
            service_tier=service_tier,
            legacy_only=legacy_only,
            claim_order=claim_order,
            capacity_model_group=capacity_model_group,
            capacity_service_tier=capacity_service_tier,
            capacity_max_in_flight_items=capacity_max_in_flight_items,
            capacity_max_in_flight_work_units=capacity_max_in_flight_work_units,
            tenant_scope_type=tenant_scope_type,
            tenant_scope_id=tenant_scope_id,
            allow_oversized_first_item=allow_oversized_first_item,
            reason=reason,
        )

    async def list_model_group_backlog(self) -> list[BatchModelBacklogRecord]:
        return await self.jobs.list_model_group_backlog()

    async def list_model_group_in_flight(self) -> list[BatchModelInFlightRecord]:
        return await self.jobs.list_model_group_in_flight()

    async def refresh_scheduler_flows(
        self,
        *,
        service_tier: str | None = None,
        model_group: str | None = None,
        base_quantum_work_units: int = 16,
        max_deficit_multiplier: int = 8,
        max_candidate_jobs_per_flow: int = 50,
        size_aware_scheduling_enabled: bool = False,
        aging_seconds_per_work_unit: int = 30,
        max_age_credit_work_units: int = 1_000,
        min_large_job_claim_interval_seconds: int = 30,
        small_job_max_work_units: int = 100,
    ) -> list[BatchSchedulerFlowRecord]:
        return await self.jobs.refresh_scheduler_flows(
            service_tier=service_tier,
            model_group=model_group,
            base_quantum_work_units=base_quantum_work_units,
            max_deficit_multiplier=max_deficit_multiplier,
            max_candidate_jobs_per_flow=max_candidate_jobs_per_flow,
            size_aware_scheduling_enabled=size_aware_scheduling_enabled,
            aging_seconds_per_work_unit=aging_seconds_per_work_unit,
            max_age_credit_work_units=max_age_credit_work_units,
            min_large_job_claim_interval_seconds=min_large_job_claim_interval_seconds,
            small_job_max_work_units=small_job_max_work_units,
        )

    async def list_scheduler_flows(
        self,
        *,
        service_tier: str | None = None,
        model_group: str | None = None,
        tenant_scope_type: str | None = None,
        active: bool | None = None,
        limit: int | None = None,
    ) -> list[BatchSchedulerFlowRecord]:
        return await self.jobs.list_scheduler_flows(
            service_tier=service_tier,
            model_group=model_group,
            tenant_scope_type=tenant_scope_type,
            active=active,
            limit=limit,
        )

    async def claim_next_fair_share_work(
        self,
        *,
        worker_id: str,
        service_tier: str,
        model_group: str,
        max_items: int,
        max_work_units: int,
        lease_seconds: int,
        capacity_max_in_flight_items: int | None = None,
        capacity_max_in_flight_work_units: int | None = None,
        base_quantum_work_units: int = 16,
        max_deficit_multiplier: int = 8,
        tenant_max_in_flight_work_units: int = 0,
        size_aware_scheduling_enabled: bool = False,
        aging_seconds_per_work_unit: int = 30,
        max_age_credit_work_units: int = 1_000,
        min_large_job_claim_interval_seconds: int = 30,
        small_job_max_work_units: int = 100,
        max_active_flows_per_decision: int = 100,
        max_candidate_jobs_per_flow: int = 50,
        work_claim_min_items_for_microbatch: int = 4,
        scheduler_mode: str = "fair_share_v1",
    ) -> BatchFairShareClaimResult:
        return await self.jobs.claim_next_fair_share_work(
            worker_id=worker_id,
            service_tier=service_tier,
            model_group=model_group,
            max_items=max_items,
            max_work_units=max_work_units,
            lease_seconds=lease_seconds,
            capacity_max_in_flight_items=capacity_max_in_flight_items,
            capacity_max_in_flight_work_units=capacity_max_in_flight_work_units,
            base_quantum_work_units=base_quantum_work_units,
            max_deficit_multiplier=max_deficit_multiplier,
            tenant_max_in_flight_work_units=tenant_max_in_flight_work_units,
            size_aware_scheduling_enabled=size_aware_scheduling_enabled,
            aging_seconds_per_work_unit=aging_seconds_per_work_unit,
            max_age_credit_work_units=max_age_credit_work_units,
            min_large_job_claim_interval_seconds=min_large_job_claim_interval_seconds,
            small_job_max_work_units=small_job_max_work_units,
            max_active_flows_per_decision=max_active_flows_per_decision,
            max_candidate_jobs_per_flow=max_candidate_jobs_per_flow,
            work_claim_min_items_for_microbatch=work_claim_min_items_for_microbatch,
            scheduler_mode=scheduler_mode,
        )

    async def recommend_next_fair_share_flow(
        self,
        *,
        service_tier: str,
        model_group: str,
        max_items: int,
        max_work_units: int,
        base_quantum_work_units: int = 16,
        max_deficit_multiplier: int = 8,
        tenant_max_in_flight_work_units: int = 0,
        size_aware_scheduling_enabled: bool = False,
        aging_seconds_per_work_unit: int = 30,
        max_age_credit_work_units: int = 1_000,
        min_large_job_claim_interval_seconds: int = 30,
        small_job_max_work_units: int = 100,
        max_active_flows_per_decision: int = 100,
        max_candidate_jobs_per_flow: int = 50,
    ) -> BatchFairShareClaimResult:
        return await self.jobs.recommend_next_fair_share_flow(
            service_tier=service_tier,
            model_group=model_group,
            max_items=max_items,
            max_work_units=max_work_units,
            base_quantum_work_units=base_quantum_work_units,
            max_deficit_multiplier=max_deficit_multiplier,
            tenant_max_in_flight_work_units=tenant_max_in_flight_work_units,
            size_aware_scheduling_enabled=size_aware_scheduling_enabled,
            aging_seconds_per_work_unit=aging_seconds_per_work_unit,
            max_age_credit_work_units=max_age_credit_work_units,
            min_large_job_claim_interval_seconds=min_large_job_claim_interval_seconds,
            small_job_max_work_units=small_job_max_work_units,
            max_active_flows_per_decision=max_active_flows_per_decision,
            max_candidate_jobs_per_flow=max_candidate_jobs_per_flow,
        )

    async def get_tenant_queued_work_units(
        self,
        *,
        tenant_scope_type: str,
        tenant_scope_id: str,
        created_by_api_key: str | None = None,
        created_by_team_id: str | None = None,
        created_by_organization_id: str | None = None,
        created_by_user_id: str | None = None,
    ) -> int:
        return await self.jobs.get_tenant_queued_work_units(
            tenant_scope_type=tenant_scope_type,
            tenant_scope_id=tenant_scope_id,
            created_by_api_key=created_by_api_key,
            created_by_team_id=created_by_team_id,
            created_by_organization_id=created_by_organization_id,
            created_by_user_id=created_by_user_id,
        )

    async def diagnose_empty_work_claim(self) -> str:
        return await self.jobs.diagnose_empty_work_claim()

    async def diagnose_empty_work_claim_context(self) -> BatchClaimDecisionDiagnostic:
        return await self.jobs.diagnose_empty_work_claim_context()

    async def diagnose_model_group_work_claim_empty(
        self,
        *,
        model_group: str,
        service_tier: str,
        max_work_units: int,
        capacity_max_in_flight_items: int | None = None,
        capacity_max_in_flight_work_units: int | None = None,
    ) -> str:
        return await self.jobs.diagnose_model_group_work_claim_empty(
            model_group=model_group,
            service_tier=service_tier,
            max_work_units=max_work_units,
            capacity_max_in_flight_items=capacity_max_in_flight_items,
            capacity_max_in_flight_work_units=capacity_max_in_flight_work_units,
        )

    async def diagnose_model_group_work_claim_empty_context(
        self,
        *,
        model_group: str,
        service_tier: str,
        max_work_units: int,
        capacity_max_in_flight_items: int | None = None,
        capacity_max_in_flight_work_units: int | None = None,
    ) -> BatchClaimDecisionDiagnostic:
        return await self.jobs.diagnose_model_group_work_claim_empty_context(
            model_group=model_group,
            service_tier=service_tier,
            max_work_units=max_work_units,
            capacity_max_in_flight_items=capacity_max_in_flight_items,
            capacity_max_in_flight_work_units=capacity_max_in_flight_work_units,
        )

    async def claim_items(
        self,
        *,
        batch_id: str,
        worker_id: str,
        limit: int = 20,
        lease_seconds: int = 60,
    ) -> list[BatchItemRecord]:
        return await self.items.claim_items(
            batch_id=batch_id,
            worker_id=worker_id,
            limit=limit,
            lease_seconds=lease_seconds,
        )

    async def list_items_by_ids(self, item_ids: list[str]) -> list[BatchItemRecord]:
        return await self.items.list_items_by_ids(item_ids)

    async def load_claim_items(self, item_ids: list[str]) -> list[BatchItemRecord]:
        return await self.items.list_items_by_ids(item_ids)

    async def release_claim_items(
        self,
        *,
        item_ids: list[str],
        worker_id: str,
    ) -> int:
        return await self.items.release_claim_items(item_ids=item_ids, worker_id=worker_id)

    async def mark_item_completed(
        self,
        *,
        item_id: str,
        worker_id: str | None,
        response_body: dict[str, Any],
        usage: dict[str, Any] | None,
        provider_cost: float,
        billed_cost: float,
        claim_epoch: int | None = None,
    ) -> bool:
        return await self.items.mark_item_completed(
            item_id=item_id,
            worker_id=worker_id,
            claim_epoch=claim_epoch,
            response_body=response_body,
            usage=usage,
            provider_cost=provider_cost,
            billed_cost=billed_cost,
        )

    async def mark_items_completed_bulk(
        self,
        *,
        items: list[dict[str, Any]],
        worker_id: str | None,
    ) -> bool:
        return await self.items.mark_items_completed_bulk(
            items=items,
            worker_id=worker_id,
        )

    async def mark_item_failed(
        self,
        *,
        item_id: str,
        worker_id: str | None,
        error_body: dict[str, Any],
        last_error: str,
        retryable: bool,
        retry_delay_seconds: int = 0,
        claim_epoch: int | None = None,
    ) -> bool:
        return await self.items.mark_item_failed(
            item_id=item_id,
            worker_id=worker_id,
            error_body=error_body,
            last_error=last_error,
            retryable=retryable,
            retry_delay_seconds=retry_delay_seconds,
            claim_epoch=claim_epoch,
        )

    async def refresh_job_progress(self, batch_id: str) -> BatchJobRecord | None:
        return await self.jobs.refresh_job_progress(batch_id)

    async def refresh_jobs_after_claim(self, batch_ids: list[str]) -> list[BatchJobRecord]:
        records: list[BatchJobRecord] = []
        seen: set[str] = set()
        for batch_id in batch_ids:
            if batch_id in seen:
                continue
            seen.add(batch_id)
            record = await self.refresh_job_progress(batch_id)
            if record is not None:
                records.append(record)
        return records

    async def renew_job_lease(self, *, batch_id: str, worker_id: str, lease_seconds: int) -> bool:
        return await self.jobs.renew_job_lease(batch_id=batch_id, worker_id=worker_id, lease_seconds=lease_seconds)

    async def reschedule_finalization(
        self,
        *,
        batch_id: str,
        worker_id: str,
        retry_delay_seconds: int,
    ) -> bool:
        return await self.jobs.reschedule_finalization(
            batch_id=batch_id,
            worker_id=worker_id,
            retry_delay_seconds=retry_delay_seconds,
        )

    async def release_job_lease(self, *, batch_id: str, worker_id: str) -> None:
        await self.jobs.release_job_lease(batch_id=batch_id, worker_id=worker_id)

    async def renew_item_lease(
        self,
        *,
        item_id: str,
        worker_id: str,
        lease_seconds: int,
        claim_epoch: int | None = None,
    ) -> bool:
        return await self.items.renew_item_lease(
            item_id=item_id,
            worker_id=worker_id,
            lease_seconds=lease_seconds,
            claim_epoch=claim_epoch,
        )

    async def release_items_for_retry(
        self,
        *,
        item_ids: list[str],
        worker_id: str,
        retry_delay_seconds: int = 0,
        error_body: dict[str, Any] | None = None,
        last_error: str | None = None,
        item_claim_epochs: dict[str, int] | None = None,
    ) -> list[str]:
        return await self.items.release_items_for_retry(
            item_ids=item_ids,
            worker_id=worker_id,
            retry_delay_seconds=retry_delay_seconds,
            error_body=error_body,
            last_error=last_error,
            item_claim_epochs=item_claim_epochs,
        )

    async def enqueue_completion_outbox_many(self, records: list[BatchCompletionOutboxCreate]) -> list[str]:
        return await self.completion_outbox.enqueue_many(records)

    async def claim_completion_outbox_due(
        self,
        *,
        worker_id: str,
        lease_seconds: int,
        limit: int = 25,
    ) -> list[BatchCompletionOutboxRecord]:
        return await self.completion_outbox.claim_due(
            worker_id=worker_id,
            lease_seconds=lease_seconds,
            limit=limit,
        )

    async def mark_completion_outbox_sent(self, completion_id: str, *, worker_id: str) -> bool:
        return await self.completion_outbox.mark_sent(completion_id, worker_id=worker_id)

    async def mark_completion_outbox_retry(
        self,
        completion_id: str,
        *,
        worker_id: str,
        error: str,
        next_attempt_at: datetime,
    ) -> bool:
        return await self.completion_outbox.mark_retry(
            completion_id,
            worker_id=worker_id,
            error=error,
            next_attempt_at=next_attempt_at,
        )

    async def mark_completion_outbox_failed(self, completion_id: str, *, worker_id: str, error: str) -> bool:
        return await self.completion_outbox.mark_failed(completion_id, worker_id=worker_id, error=error)

    async def renew_completion_outbox_lease(self, *, completion_id: str, worker_id: str, lease_seconds: int) -> bool:
        return await self.completion_outbox.renew_lease(
            completion_id,
            worker_id=worker_id,
            lease_seconds=lease_seconds,
        )

    async def list_completion_outbox_by_item_ids(self, item_ids: list[str]) -> list[BatchCompletionOutboxRecord]:
        return await self.completion_outbox.list_by_item_ids(item_ids)

    async def count_pending_completion_outbox(self) -> int:
        return await self.completion_outbox.count_pending()

    async def claim_webhook_outbox_due(
        self,
        *,
        worker_id: str,
        lease_seconds: int,
        limit: int,
    ) -> list[BatchWebhookOutboxRecord]:
        return await self.webhook_outbox.claim_due(
            worker_id=worker_id,
            lease_seconds=lease_seconds,
            limit=limit,
        )

    async def fail_exhausted_webhook_outbox_leases(
        self,
        *,
        limit: int = 100,
    ) -> list[BatchWebhookOutboxRecord]:
        return await self.webhook_outbox.fail_exhausted_expired_leases(limit=limit)

    async def list_webhook_outbox_by_batch_id(
        self,
        *,
        batch_id: str,
    ) -> list[BatchWebhookOutboxRecord]:
        return await self.webhook_outbox.list_by_batch_id(batch_id=batch_id)

    async def resolve_batch_organization_id(
        self,
        *,
        batch_id: str | None = None,
        created_by_team_id: str | None = None,
        created_by_organization_id: str | None = None,
    ) -> str | None:
        """Resolve the authoritative organization without exposing webhook material."""

        organization_id = str(created_by_organization_id or "").strip() or None
        team_id = str(created_by_team_id or "").strip() or None
        if organization_id is not None:
            return organization_id

        if batch_id and (team_id is None or organization_id is None):
            job = await self.get_job(batch_id)
            if job is not None:
                organization_id = str(job.created_by_organization_id or "").strip() or None
                team_id = team_id or (str(job.created_by_team_id or "").strip() or None)
                if organization_id is not None:
                    return organization_id

        if self.prisma is None or team_id is None:
            return None
        rows = await self.prisma.query_raw(
            "SELECT organization_id FROM deltallm_teamtable WHERE team_id = $1 LIMIT 1",
            team_id,
        )
        return str((rows[0] if rows else {}).get("organization_id") or "").strip() or None

    async def backfill_missing_webhook_ownership(
        self,
        *,
        batch_ids: list[str],
    ) -> int:
        return await self.webhook_outbox.backfill_missing_ownership_for_batches(
            batch_ids=batch_ids
        )

    async def replay_failed_webhook_outbox(
        self,
        *,
        batch_id: str,
        event_id: str,
    ) -> BatchWebhookReplayResult | None:
        return await self.webhook_outbox.replay_failed(
            batch_id=batch_id,
            event_id=event_id,
        )

    async def summarize_webhook_outbox(self) -> BatchWebhookQueueSummary:
        return await self.webhook_outbox.summarize()

    async def delete_terminal_webhook_outbox_before(
        self,
        *,
        cutoff: datetime,
        limit: int,
    ) -> dict[str, int]:
        return await self.webhook_outbox.delete_terminal_before(
            cutoff=cutoff,
            limit=limit,
        )

    async def renew_webhook_outbox_lease(
        self,
        *,
        event_id: str,
        worker_id: str,
        attempt_count: int,
        lease_seconds: int,
    ) -> bool:
        return await self.webhook_outbox.renew_lease(
            event_id,
            worker_id=worker_id,
            attempt_count=attempt_count,
            lease_seconds=lease_seconds,
        )

    async def mark_webhook_outbox_delivered(
        self,
        *,
        event_id: str,
        worker_id: str,
        attempt_count: int,
        status_code: int,
    ) -> bool:
        return await self.webhook_outbox.mark_delivered(
            event_id,
            worker_id=worker_id,
            attempt_count=attempt_count,
            status_code=status_code,
        )

    async def mark_webhook_outbox_retrying(
        self,
        *,
        event_id: str,
        worker_id: str,
        attempt_count: int,
        status_code: int | None,
        error: str,
        next_attempt_at: datetime,
    ) -> bool:
        return await self.webhook_outbox.mark_retrying(
            event_id,
            worker_id=worker_id,
            attempt_count=attempt_count,
            status_code=status_code,
            error=error,
            next_attempt_at=next_attempt_at,
        )

    async def mark_webhook_outbox_failed(
        self,
        *,
        event_id: str,
        worker_id: str,
        attempt_count: int,
        status_code: int | None,
        error: str,
    ) -> bool:
        return await self.webhook_outbox.mark_failed(
            event_id,
            worker_id=worker_id,
            attempt_count=attempt_count,
            status_code=status_code,
            error=error,
        )

    async def mark_pending_items_cancelled(self, batch_id: str) -> None:
        await self.items.mark_pending_items_cancelled(batch_id)

    async def list_items(self, batch_id: str) -> list[BatchItemRecord]:
        return await self.items.list_items(batch_id)

    async def list_items_page(
        self,
        *,
        batch_id: str,
        limit: int = 500,
        after_line_number: int | None = None,
    ) -> list[BatchItemRecord]:
        return await self.items.list_items_page(
            batch_id=batch_id,
            limit=limit,
            after_line_number=after_line_number,
        )

    async def requeue_expired_in_progress_items(self, batch_id: str) -> int:
        return await self.items.requeue_expired_in_progress_items(batch_id)

    async def fail_nonterminal_items(self, *, batch_id: str, reason: str) -> int:
        return await self.items.fail_nonterminal_items(batch_id=batch_id, reason=reason)

    async def _enqueue_webhook_for_terminal_job_in_current_transaction(
        self,
        job: BatchJobRecord,
    ) -> None:
        ciphertext = job.webhook_config_ciphertext
        fingerprint = job.webhook_config_fingerprint
        if ciphertext is None and fingerprint is None:
            return
        if ciphertext is None or fingerprint is None:
            raise BatchWebhookConfigurationConflictError(
                "batch webhook configuration is incomplete"
            )

        event_type = batch_webhook_event_type_for_status(job.status)
        event = build_batch_webhook_event(job)
        inserted = await self.webhook_outbox.insert_event(
            BatchWebhookOutboxCreate(
                event_id=event.event_id,
                batch_id=job.batch_id,
                event_type=event.event_type,
                created_by_team_id=job.created_by_team_id,
                created_by_organization_id=job.created_by_organization_id,
                target_config_ciphertext=ciphertext,
                payload_json=event.payload_json,
                payload_sha256=event.payload_sha256,
                max_attempts=self.webhook_max_attempts,
            )
        )
        if inserted is not None:
            return

        existing = await self.webhook_outbox.fill_missing_ownership(
            batch_id=job.batch_id,
            event_type=event_type,
            created_by_team_id=job.created_by_team_id,
            created_by_organization_id=job.created_by_organization_id,
        )
        if existing is None:
            existing = await self.webhook_outbox.get_by_batch_and_event_type(
                batch_id=job.batch_id,
                event_type=event_type,
            )
        if existing is None or not _webhook_outbox_matches_terminal_job(
            existing,
            job=job,
            event_type=event_type,
        ):
            raise BatchWebhookConfigurationConflictError(
                "batch webhook event conflicts with terminal outcome"
            )

    async def _attach_artifacts_and_enqueue_webhook_in_current_transaction(
        self,
        *,
        batch_id: str,
        output_file_id: str | None,
        error_file_id: str | None,
        final_status: BatchJobStatus,
        worker_id: str | None,
        terminal_provider_error: str | None,
    ) -> BatchJobRecord | None:
        finalized = await self.jobs.attach_artifacts_and_finalize(
            batch_id=batch_id,
            output_file_id=output_file_id,
            error_file_id=error_file_id,
            final_status=final_status,
            worker_id=worker_id,
            terminal_provider_error=terminal_provider_error,
        )
        if finalized is None:
            return None
        await self._enqueue_webhook_for_terminal_job_in_current_transaction(finalized)
        return finalized

    async def _reconcile_job_webhook_config_in_current_transaction(
        self,
        *,
        batch_id: str,
        webhook_config_ciphertext: str | None,
        webhook_config_fingerprint: str | None,
    ) -> BatchJobRecord | None:
        expected = (webhook_config_ciphertext, webhook_config_fingerprint)
        if (webhook_config_ciphertext is None) != (webhook_config_fingerprint is None):
            raise BatchWebhookConfigurationConflictError(
                "batch webhook configuration is incomplete"
            )

        job = await self.get_job_for_update(batch_id)
        if job is None:
            return None
        current = (
            job.webhook_config_ciphertext,
            job.webhook_config_fingerprint,
        )
        if current != expected:
            if current != (None, None) or expected == (None, None):
                raise BatchWebhookConfigurationConflictError(
                    "batch webhook configuration conflicts with existing job"
                )
            updated = await self.set_job_webhook_config_if_unset(
                batch_id=batch_id,
                webhook_config_ciphertext=str(webhook_config_ciphertext),
                webhook_config_fingerprint=str(webhook_config_fingerprint),
            )
            if updated is None:
                raise BatchWebhookConfigurationConflictError(
                    "batch webhook configuration conflicts with existing job"
                )
            job = updated

        if job.status in {
            BatchJobStatus.COMPLETED,
            BatchJobStatus.FAILED,
            BatchJobStatus.CANCELLED,
            BatchJobStatus.EXPIRED,
        }:
            await self._enqueue_webhook_for_terminal_job_in_current_transaction(job)
        return job

    async def reconcile_job_webhook_config(
        self,
        *,
        batch_id: str,
        webhook_config_ciphertext: str | None,
        webhook_config_fingerprint: str | None,
    ) -> BatchJobRecord | None:
        """Reconcile staged webhook state and heal a missing terminal event."""
        if _prisma_client_is_transaction(self.prisma):
            return await self._reconcile_job_webhook_config_in_current_transaction(
                batch_id=batch_id,
                webhook_config_ciphertext=webhook_config_ciphertext,
                webhook_config_fingerprint=webhook_config_fingerprint,
            )
        if self.prisma is not None and hasattr(self.prisma, "tx"):
            async with self.prisma.tx() as tx:
                transactional_repository = self.with_prisma(tx)
                return await (
                    transactional_repository._reconcile_job_webhook_config_in_current_transaction(
                        batch_id=batch_id,
                        webhook_config_ciphertext=webhook_config_ciphertext,
                        webhook_config_fingerprint=webhook_config_fingerprint,
                    )
                )

        existing = await self.get_job(batch_id)
        expected = (webhook_config_ciphertext, webhook_config_fingerprint)
        if existing is None:
            return None
        current = (
            existing.webhook_config_ciphertext,
            existing.webhook_config_fingerprint,
        )
        if current != expected:
            raise RuntimeError("batch webhook reconciliation requires transaction support")
        if (
            existing.status
            in {
                BatchJobStatus.COMPLETED,
                BatchJobStatus.FAILED,
                BatchJobStatus.CANCELLED,
                BatchJobStatus.EXPIRED,
            }
            and existing.webhook_config_ciphertext is not None
        ):
            raise RuntimeError("batch webhook reconciliation requires transaction support")
        return existing

    def publish_finalization_metric_after_commit(self, finalized: BatchJobRecord) -> None:
        try:
            self.jobs.observe_finalization(finalized)
        except Exception:
            logger.warning(
                "batch finalization metric publish failed batch_id=%s",
                finalized.batch_id,
                exc_info=True,
            )

    async def attach_artifacts_and_finalize(
        self,
        *,
        batch_id: str,
        output_file_id: str | None,
        error_file_id: str | None,
        final_status: str | BatchJobStatus,
        worker_id: str | None = None,
        terminal_provider_error: str | None = None,
    ) -> BatchJobRecord | None:
        """Commit terminal state and its webhook event as one aggregate operation.

        When this repository already wraps a transaction client, the caller owns
        the outer commit and must publish the returned record's metric afterward.
        """
        normalized_final_status = normalize_batch_job_status(final_status)
        batch_webhook_event_type_for_status(normalized_final_status)
        reusing_transaction = _prisma_client_is_transaction(self.prisma)

        if reusing_transaction:
            finalized = await self._attach_artifacts_and_enqueue_webhook_in_current_transaction(
                batch_id=batch_id,
                output_file_id=output_file_id,
                error_file_id=error_file_id,
                final_status=normalized_final_status,
                worker_id=worker_id,
                terminal_provider_error=terminal_provider_error,
            )
        elif self.prisma is not None and hasattr(self.prisma, "tx"):
            async with self.prisma.tx() as tx:
                transactional_repository = self.with_prisma(tx)
                finalized = (
                    await transactional_repository._attach_artifacts_and_enqueue_webhook_in_current_transaction(
                        batch_id=batch_id,
                        output_file_id=output_file_id,
                        error_file_id=error_file_id,
                        final_status=normalized_final_status,
                        worker_id=worker_id,
                        terminal_provider_error=terminal_provider_error,
                    )
                )
        else:
            existing = await self.get_job(batch_id)
            if existing is not None and (
                existing.webhook_config_ciphertext is not None
                or existing.webhook_config_fingerprint is not None
            ):
                raise RuntimeError("batch webhook finalization requires transaction support")
            finalized = await self._attach_artifacts_and_enqueue_webhook_in_current_transaction(
                batch_id=batch_id,
                output_file_id=output_file_id,
                error_file_id=error_file_id,
                final_status=normalized_final_status,
                worker_id=worker_id,
                terminal_provider_error=terminal_provider_error,
            )

        # An outer transaction owns the commit when this repository already wraps
        # a transaction client. Its caller must publish after that commit succeeds.
        if finalized is not None and not reusing_transaction:
            self.publish_finalization_metric_after_commit(finalized)
        return finalized

    async def retry_finalization_now(self, batch_id: str) -> BatchJobRecord | None:
        return await self.jobs.retry_finalization_now(batch_id)

    async def complete_item_with_outbox(
        self,
        *,
        item_id: str,
        worker_id: str | None,
        response_body: dict[str, Any],
        usage: dict[str, Any] | None,
        provider_cost: float,
        billed_cost: float,
        outbox_payload: dict[str, Any],
        outbox_max_attempts: int = 5,
        claim_epoch: int | None = None,
    ) -> Literal["completed", "already_completed", "not_owned"]:
        return await self.complete_items_with_outbox_bulk(
            items=[
                {
                    "item_id": item_id,
                    "claim_epoch": claim_epoch,
                    "response_body": response_body,
                    "usage": usage,
                    "provider_cost": provider_cost,
                    "billed_cost": billed_cost,
                    "outbox_payload": outbox_payload,
                    "outbox_max_attempts": outbox_max_attempts,
                }
            ],
            worker_id=worker_id,
        )

    async def complete_items_with_outbox_bulk(
        self,
        *,
        items: list[dict[str, Any]],
        worker_id: str | None,
    ) -> Literal["completed", "already_completed", "not_owned"]:
        if not items:
            return "completed"

        async def _run_in_current_repo(repo: BatchRepository) -> Literal["completed", "already_completed", "not_owned"]:
            updated = await repo.mark_items_completed_bulk(
                items=[
                    {
                        "item_id": item["item_id"],
                        "response_body": item["response_body"],
                        "usage": item.get("usage"),
                        "provider_cost": item["provider_cost"],
                        "billed_cost": item["billed_cost"],
                        "claim_epoch": item.get("claim_epoch"),
                    }
                    for item in items
                ],
                worker_id=worker_id,
            )
            if updated:
                completion_ids = await repo.enqueue_completion_outbox_many(
                    [
                        BatchCompletionOutboxCreate(
                            batch_id=str(item["outbox_payload"]["batch_id"]),
                            item_id=str(item["item_id"]),
                            payload_json=dict(item["outbox_payload"]),
                            status=BatchCompletionOutboxStatus.QUEUED,
                            max_attempts=int(item.get("outbox_max_attempts") or 5),
                        )
                        for item in items
                    ]
                )
                if len(completion_ids) != len(items):
                    raise RuntimeError("failed to enqueue one or more batch completion outbox rows")
                return "completed"

            item_ids = [str(item["item_id"]) for item in items]
            existing_items = await repo.list_items_by_ids(item_ids)
            existing_outbox = await repo.list_completion_outbox_by_item_ids(item_ids)
            completed_item_ids = {item.item_id for item in existing_items if item.status == "completed"}
            outbox_item_ids = {record.item_id for record in existing_outbox}
            if completed_item_ids == set(item_ids) and outbox_item_ids == set(item_ids):
                return "already_completed"
            increment_batch_duplicate_completion_rejection(reason="not_owned", count=len(item_ids))
            return "not_owned"

        if self.prisma is not None and hasattr(self.prisma, "tx"):
            async with self.prisma.tx() as tx:
                return await _run_in_current_repo(self.with_prisma(tx))
        return await _run_in_current_repo(self)

    async def set_provider_error(self, *, batch_id: str, provider_error: str | None) -> BatchJobRecord | None:
        return await self.jobs.set_provider_error(batch_id=batch_id, provider_error=provider_error)

    async def cleanup_next_expired_terminal_job(self, *, now: datetime) -> bool:
        """Delete at most one expired job in its own transaction."""

        async def _cleanup_one(repo: BatchRepository) -> int:
            batch_ids = await repo.maintenance.claim_expired_terminal_job_ids(
                now=now,
                limit=1,
            )
            if not batch_ids:
                return 0
            await repo.webhook_outbox.backfill_missing_ownership_for_batches(
                batch_ids=batch_ids
            )
            await repo.webhook_outbox.assert_ownership_matches_jobs_for_batches(
                batch_ids=batch_ids
            )
            deleted = await repo.maintenance.delete_job_metadata(batch_ids[0])
            return int(deleted)

        if self.prisma is None:
            return False
        if _prisma_client_is_transaction(self.prisma):
            return bool(await _cleanup_one(self))
        if hasattr(self.prisma, "tx"):
            async with self.prisma.tx() as tx:
                return bool(await _cleanup_one(self.with_prisma(tx)))
        raise RuntimeError("expired batch cleanup requires transaction support")

    async def count_expired_terminal_job_ownership_conflicts(
        self,
        *,
        now: datetime,
        limit: int = 100,
    ) -> int:
        return await self.maintenance.count_expired_terminal_job_ownership_conflicts(
            now=now,
            limit=limit,
        )

    async def backfill_scheduler_dimensions(
        self,
        *,
        limit: int = 500,
        service_tier: str | None = None,
        model_group: str | None = None,
    ) -> dict[str, int]:
        return await self.maintenance.backfill_scheduler_dimensions(
            limit=limit,
            service_tier=service_tier,
            model_group=model_group,
        )

    async def sweep_expired_batch_leases(
        self,
        *,
        now: datetime,
        page_size: int = 100,
        max_rows_per_run: int = 500,
    ) -> dict[str, int]:
        return await self.maintenance.sweep_expired_batch_leases(
            now=now,
            page_size=page_size,
            max_rows_per_run=max_rows_per_run,
        )

    async def list_expired_unreferenced_files(self, *, now: datetime, limit: int = 100) -> list[BatchFileRecord]:
        return await self.files.list_expired_unreferenced_files(now=now, limit=limit)

    async def delete_file(self, file_id: str) -> None:
        await self.files.delete_file(file_id)
