from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import time
from datetime import UTC, datetime
from typing import Any

from src.batch.models import BatchJobStatus
from src.batch.repository import BatchRepository
from src.batch.storage import BatchArtifactStorage
from src.batch.worker_artifacts import BatchArtifactFinalizer
from src.batch.worker_execution import BatchExecutionEngine
from src.batch.worker_types import (
    BatchArtifactValidationError,
    BatchWorkerConfig,
    _PreparedChatItem,
    _PreparedEmbeddingItem,
    _RequestShim,
)
from src.chat.executor import execute_chat
from src.metrics import (
    increment_batch_claim_empty_job,
    increment_batch_finalization_claim,
    increment_batch_scheduler_shadow_decision,
    increment_batch_work_claim,
    observe_batch_item_execution_latency,
    observe_batch_scheduler_shadow_share_ratio,
    observe_batch_work_claim_items,
    observe_batch_work_claim_latency,
    observe_batch_work_claim_units,
    publish_batch_runtime_summary,
    set_batch_worker_saturation,
)
from src.router.usage import record_router_usage
from src.routers.embeddings import _execute_embedding

logger = logging.getLogger(__name__)

_FAIR_SHARE_MODEL_FALLBACK_RESULTS = frozenset(
    {
        "no_active_flow",
        "transaction_unavailable",
    }
)

__all__ = [
    "BatchArtifactValidationError",
    "BatchExecutorWorker",
    "BatchWorkerConfig",
    "_PreparedEmbeddingItem",
    "_PreparedChatItem",
    "_RequestShim",
]


class BatchExecutorWorker:
    def __init__(
        self,
        *,
        app: Any,
        repository: BatchRepository,
        storage: BatchArtifactStorage,
        config: BatchWorkerConfig,
        model_capacity_resolver: Any | None = None,
    ) -> None:
        self.app = app
        self.repository = repository
        self.storage = storage
        self.config = config
        self.model_capacity_resolver = model_capacity_resolver
        self._running = False
        self._artifact_finalizer = BatchArtifactFinalizer(
            repository=repository,
            storage=storage,
            config=config,
        )
        self._execution_engine = BatchExecutionEngine(
            app=app,
            repository=repository,
            config=config,
            normalize_persisted_embedding_response_body=self._artifact_finalizer.normalize_persisted_embedding_response_body,
            execute_embedding=self._call_execute_embedding,
            execute_chat=self._call_execute_chat,
            record_router_usage=self._call_record_router_usage,
            observe_item_execution_latency=self._call_observe_item_execution_latency,
            start_heartbeat=self._call_start_heartbeat,
            stop_heartbeat=self._call_stop_heartbeat,
        )

    def _sync_dependencies(self) -> None:
        self._artifact_finalizer.repository = self.repository
        self._artifact_finalizer.storage = self.storage
        self._artifact_finalizer.config = self.config
        self._execution_engine.app = self.app
        self._execution_engine.repository = self.repository
        self._execution_engine.config = self.config

    async def _call_execute_embedding(self, request, payload, deployment):  # noqa: ANN001, ANN201
        return await _execute_embedding(request, payload, deployment)

    async def _call_execute_chat(self, request, payload, deployment, *, record_usage: bool = True):  # noqa: ANN001, ANN201
        return await execute_chat(request, payload, deployment, record_usage=record_usage)

    async def _call_record_router_usage(self, router_state_backend, deployment_id: str, *, mode: str, usage: dict) -> None:
        await record_router_usage(
            router_state_backend,
            deployment_id,
            mode=mode,
            usage=usage,
        )

    def _call_observe_item_execution_latency(self, *, status: str, latency_seconds: float) -> None:
        observe_batch_item_execution_latency(status=status, latency_seconds=latency_seconds)

    def _call_start_heartbeat(self, *, renew, label: str) -> asyncio.Task[None]:  # noqa: ANN001
        return self._start_heartbeat(renew=renew, label=label)

    async def _call_stop_heartbeat(self, task: asyncio.Task[None]) -> None:
        await self._stop_heartbeat(task)

    async def run(self) -> None:
        self._running = True
        while self._running:
            try:
                did_work = await self.process_once()
            except Exception:
                logger.exception("batch worker iteration failed")
                await asyncio.sleep(self.config.poll_interval_seconds)
                continue
            if not did_work:
                await asyncio.sleep(self.config.poll_interval_seconds)

    def stop(self) -> None:
        self._running = False

    def _claim_limit(self) -> int:
        return max(
            self.config.item_claim_limit,
            self.config.worker_concurrency * self.config.item_buffer_multiplier,
        )

    def _work_claim_max_items(self) -> int:
        configured = int(self.config.work_claim_max_items or 0)
        if configured > 0:
            return max(1, min(configured, 200))
        microbatch_floor = max(1, min(int(self.config.work_claim_min_items_for_microbatch or 1), 200))
        return max(microbatch_floor, min(self._claim_limit(), 200))

    def _work_claim_max_work_units(self) -> int:
        configured = int(self.config.work_claim_max_work_units or 0)
        if configured > 0:
            return configured
        return self._work_claim_max_items() * 4

    async def _refresh_batch_runtime_metrics(self) -> None:
        try:
            summary = await self.repository.summarize_runtime_statuses(now=datetime.now(tz=UTC))
            publish_batch_runtime_summary(summary)
        except Exception:
            logger.debug("batch worker runtime metrics refresh failed", exc_info=True)
            return

    async def process_once(self) -> bool:
        if self.config.scheduler_claim_mode == "work_slice":
            return await self._process_once_work_slice()
        return await self._process_once_job_fifo()

    async def _process_once_job_fifo(self) -> bool:
        job = await self.repository.claim_next_job(
            worker_id=self.config.worker_id,
            lease_seconds=self.config.job_lease_seconds,
        )
        if job is None:
            set_batch_worker_saturation(
                worker_id=self.config.worker_id,
                active=0,
                capacity=self.config.worker_concurrency,
            )
            return False
        logger.info("batch claimed id=%s status=%s", job.batch_id, job.status)
        job_heartbeat = self._start_heartbeat(
            renew=lambda: self.repository.renew_job_lease(
                batch_id=job.batch_id,
                worker_id=self.config.worker_id,
                lease_seconds=self.config.job_lease_seconds,
            ),
            label=f"job:{job.batch_id}",
        )
        try:
            if job.status == BatchJobStatus.FINALIZING:
                await self._finalize_with_retry(job)
                return True

            if job.cancel_requested_at is not None:
                await self.repository.mark_pending_items_cancelled(job.batch_id)

            items = await self.repository.claim_items(
                batch_id=job.batch_id,
                worker_id=self.config.worker_id,
                limit=self._claim_limit(),
                lease_seconds=self.config.item_lease_seconds,
            )
            logger.info("batch items claimed id=%s count=%s", job.batch_id, len(items))
            await self._process_items(job, items)

            refreshed = await self.repository.refresh_job_progress(job.batch_id)
            if refreshed and refreshed.status == BatchJobStatus.FINALIZING:
                await self._finalize_with_retry(refreshed)
            await self._refresh_batch_runtime_metrics()
            return True
        finally:
            set_batch_worker_saturation(
                worker_id=self.config.worker_id,
                active=0,
                capacity=self.config.worker_concurrency,
            )
            await self._stop_heartbeat(job_heartbeat)
            await self.repository.release_job_lease(batch_id=job.batch_id, worker_id=self.config.worker_id)

    async def _process_finalization_job(self, job) -> None:  # noqa: ANN001
        logger.info("batch finalization claimed id=%s", job.batch_id)
        job_heartbeat = self._start_heartbeat(
            renew=lambda: self.repository.renew_job_lease(
                batch_id=job.batch_id,
                worker_id=self.config.worker_id,
                lease_seconds=self.config.job_lease_seconds,
            ),
            label=f"finalization:{job.batch_id}",
        )
        try:
            await self._finalize_with_retry(job)
            await self._refresh_batch_runtime_metrics()
        finally:
            set_batch_worker_saturation(
                worker_id=self.config.worker_id,
                active=0,
                capacity=self.config.worker_concurrency,
            )
            await self._stop_heartbeat(job_heartbeat)
            await self.repository.release_job_lease(batch_id=job.batch_id, worker_id=self.config.worker_id)

    async def _try_process_finalization_claim(self) -> bool:
        job = await self.repository.claim_next_finalization(
            worker_id=self.config.worker_id,
            lease_seconds=self.config.job_lease_seconds,
        )
        if job is None:
            increment_batch_finalization_claim(result="empty")
            return False
        increment_batch_finalization_claim(result="claimed")
        await self._process_finalization_job(job)
        return True

    async def _process_once_work_slice(self) -> bool:
        if self.config.finalization_first and await self._try_process_finalization_claim():
            return True

        claim_started = time.perf_counter()
        claim = await self._claim_next_work_slice()
        observe_batch_work_claim_latency(
            claim_mode=self.config.scheduler_claim_mode,
            latency_seconds=time.perf_counter() - claim_started,
        )
        if claim is None:
            increment_batch_work_claim(result="empty", claim_mode=self.config.scheduler_claim_mode)
            set_batch_worker_saturation(
                worker_id=self.config.worker_id,
                active=0,
                capacity=self.config.worker_concurrency,
            )
            # Try finalization fallback first to avoid running the empty-claim
            # diagnostic when there's actually work to do; the diagnostic is a
            # multi-EXISTS query worth saving on every successful fallback.
            if not self.config.finalization_first and await self._try_process_finalization_claim():
                return True
            increment_batch_claim_empty_job(reason=await self._empty_work_claim_reason())
            return False

        increment_batch_work_claim(result="claimed", claim_mode=self.config.scheduler_claim_mode)
        observe_batch_work_claim_items(
            claim_mode=self.config.scheduler_claim_mode,
            count=len(claim.item_ids),
        )
        observe_batch_work_claim_units(
            claim_mode=self.config.scheduler_claim_mode,
            work_units=claim.claimed_work_units,
        )
        logger.info(
            "batch work slice claimed id=%s claim_id=%s count=%s work_units=%s",
            claim.batch_id,
            claim.claim_id,
            len(claim.item_ids),
            claim.claimed_work_units,
        )

        # Cancel branch already releases items explicitly; skip the redundant
        # finally release in that case to save one DB round trip per cancel.
        needs_finally_release = True
        try:
            job = await self.repository.get_job(claim.batch_id)
            if job is None:
                logger.warning("batch work slice skipped after missing job id=%s", claim.batch_id)
                increment_batch_work_claim(result="missing_job", claim_mode=self.config.scheduler_claim_mode)
                return True

            if job.cancel_requested_at is not None:
                await self.repository.release_claim_items(
                    item_ids=claim.item_ids,
                    worker_id=self.config.worker_id,
                )
                await self.repository.mark_pending_items_cancelled(job.batch_id)
                needs_finally_release = False
            else:
                items = await self.repository.load_claim_items(claim.item_ids)
                if not items:
                    logger.warning("batch work slice skipped after missing claimed items id=%s", claim.batch_id)
                    increment_batch_work_claim(result="missing_items", claim_mode=self.config.scheduler_claim_mode)
                else:
                    await self._process_items(job, items)
        finally:
            if needs_finally_release:
                released = await self.repository.release_claim_items(
                    item_ids=claim.item_ids,
                    worker_id=self.config.worker_id,
                )
                if released:
                    logger.info(
                        "batch work slice released unfinished items id=%s claim_id=%s count=%s",
                        claim.batch_id,
                        claim.claim_id,
                        released,
                    )
            set_batch_worker_saturation(
                worker_id=self.config.worker_id,
                active=0,
                capacity=self.config.worker_concurrency,
            )

        refreshed_jobs = await self.repository.refresh_jobs_after_claim([claim.batch_id])
        for refreshed in refreshed_jobs:
            if refreshed.status == BatchJobStatus.FINALIZING:
                await self._try_process_finalization_claim()
                break
        await self._refresh_batch_runtime_metrics()
        return True

    async def _claim_next_work_slice(self):
        max_items = self._work_claim_max_items()
        max_work_units = self._work_claim_max_work_units()
        resolver = self.model_capacity_resolver if self.config.model_capacity_enabled else None
        if resolver is None:
            return await self.repository.claim_next_work(
                worker_id=self.config.worker_id,
                max_items=max_items,
                max_work_units=max_work_units,
                lease_seconds=self.config.item_lease_seconds,
            )

        select_model_groups = getattr(resolver, "select_model_groups", None)
        if callable(select_model_groups):
            selections = await select_model_groups(max_items=max_items, max_work_units=max_work_units)
        else:
            selection = await resolver.select_model_group(max_items=max_items, max_work_units=max_work_units)
            selections = [selection] if selection is not None else []

        size_aware_shadow_enabled = (
            self.config.scheduler_shadow_enabled and self.config.size_aware_scheduling_enabled
        )
        size_aware_active_enabled = (
            self.config.size_aware_scheduling_enabled and not size_aware_shadow_enabled
        )
        for selection in selections:
            snapshot = selection.snapshot
            record_selection = getattr(resolver, "record_selection", None)
            if callable(record_selection):
                record_selection(snapshot)
            capacity_max_in_flight_items, capacity_max_in_flight_work_units = self._capacity_claim_caps(
                snapshot,
                max_items=selection.max_items,
                max_work_units=selection.max_work_units,
            )
            fair_share_model_enabled = self._tenant_fair_share_model_enabled(snapshot.model_group)
            if self.config.tenant_fair_share_enabled and fair_share_model_enabled:
                shadow_recommendation = None
                if size_aware_shadow_enabled:
                    shadow_recommendation = await self.repository.recommend_next_fair_share_flow(
                        service_tier=snapshot.service_tier,
                        model_group=snapshot.model_group,
                        max_items=selection.max_items,
                        max_work_units=selection.max_work_units,
                        base_quantum_work_units=self.config.tenant_fair_share_base_quantum_work_units,
                        max_deficit_multiplier=self.config.tenant_fair_share_max_deficit_multiplier,
                        tenant_max_in_flight_work_units=self.config.tenant_max_in_flight_work_units,
                        size_aware_scheduling_enabled=True,
                        aging_seconds_per_work_unit=self.config.aging_seconds_per_work_unit,
                        max_age_credit_work_units=self.config.max_age_credit_work_units,
                        min_large_job_claim_interval_seconds=(
                            self.config.min_large_job_claim_interval_seconds
                        ),
                        small_job_max_work_units=self.config.small_job_max_work_units,
                    )
                fair_share_result = await self.repository.claim_next_fair_share_work(
                    worker_id=self.config.worker_id,
                    service_tier=snapshot.service_tier,
                    model_group=snapshot.model_group,
                    max_items=selection.max_items,
                    max_work_units=selection.max_work_units,
                    lease_seconds=self.config.item_lease_seconds,
                    capacity_max_in_flight_items=capacity_max_in_flight_items,
                    capacity_max_in_flight_work_units=capacity_max_in_flight_work_units,
                    base_quantum_work_units=self.config.tenant_fair_share_base_quantum_work_units,
                    max_deficit_multiplier=self.config.tenant_fair_share_max_deficit_multiplier,
                    tenant_max_in_flight_work_units=self.config.tenant_max_in_flight_work_units,
                    size_aware_scheduling_enabled=size_aware_active_enabled,
                    aging_seconds_per_work_unit=self.config.aging_seconds_per_work_unit,
                    max_age_credit_work_units=self.config.max_age_credit_work_units,
                    min_large_job_claim_interval_seconds=(
                        self.config.min_large_job_claim_interval_seconds
                    ),
                    small_job_max_work_units=self.config.small_job_max_work_units,
                    work_claim_min_items_for_microbatch=(
                        self.config.work_claim_min_items_for_microbatch
                    ),
                )
                claim = fair_share_result.claim
                result = fair_share_result.result
                if claim is None and result in _FAIR_SHARE_MODEL_FALLBACK_RESULTS:
                    claim = await self._claim_model_capacity_work_slice(
                        snapshot=snapshot,
                        selection=selection,
                        capacity_max_in_flight_items=capacity_max_in_flight_items,
                        capacity_max_in_flight_work_units=capacity_max_in_flight_work_units,
                    )
                    if claim is None:
                        result = await self._capacity_empty_claim_result(
                            snapshot=snapshot,
                            max_items=selection.max_items,
                            max_work_units=selection.max_work_units,
                        )
                    else:
                        result = "claimed"
                if shadow_recommendation is not None:
                    self._record_shadow_fair_share_decision(
                        recommendation=shadow_recommendation,
                        claim=claim,
                        model_group=snapshot.model_group,
                        service_tier=snapshot.service_tier,
                    )
            elif self.config.scheduler_shadow_enabled and fair_share_model_enabled:
                fair_share_result = await self.repository.recommend_next_fair_share_flow(
                    service_tier=snapshot.service_tier,
                    model_group=snapshot.model_group,
                    max_items=selection.max_items,
                    max_work_units=selection.max_work_units,
                    base_quantum_work_units=self.config.tenant_fair_share_base_quantum_work_units,
                    max_deficit_multiplier=self.config.tenant_fair_share_max_deficit_multiplier,
                    tenant_max_in_flight_work_units=self.config.tenant_max_in_flight_work_units,
                    size_aware_scheduling_enabled=self.config.size_aware_scheduling_enabled,
                    aging_seconds_per_work_unit=self.config.aging_seconds_per_work_unit,
                    max_age_credit_work_units=self.config.max_age_credit_work_units,
                    min_large_job_claim_interval_seconds=(
                        self.config.min_large_job_claim_interval_seconds
                    ),
                    small_job_max_work_units=self.config.small_job_max_work_units,
                )
                claim = await self._claim_model_capacity_work_slice(
                    snapshot=snapshot,
                    selection=selection,
                    capacity_max_in_flight_items=capacity_max_in_flight_items,
                    capacity_max_in_flight_work_units=capacity_max_in_flight_work_units,
                )
                self._record_shadow_fair_share_decision(
                    recommendation=fair_share_result,
                    claim=claim,
                    model_group=snapshot.model_group,
                    service_tier=snapshot.service_tier,
                )
                result = "claimed"
                if claim is None:
                    result = await self._capacity_empty_claim_result(
                        snapshot=snapshot,
                        max_items=selection.max_items,
                        max_work_units=selection.max_work_units,
                    )
            else:
                claim = await self._claim_model_capacity_work_slice(
                    snapshot=snapshot,
                    selection=selection,
                    capacity_max_in_flight_items=capacity_max_in_flight_items,
                    capacity_max_in_flight_work_units=capacity_max_in_flight_work_units,
                )
                result = "claimed"
                if claim is None:
                    result = await self._capacity_empty_claim_result(
                        snapshot=snapshot,
                        max_items=selection.max_items,
                        max_work_units=selection.max_work_units,
                    )
            resolver.record_claim_result(
                model_group=snapshot.model_group,
                result=result,
            )
            if claim is not None:
                return claim

        return await self._claim_legacy_work_slice(
            max_items=max_items,
            max_work_units=max_work_units,
            resolver=resolver,
        )

    def _tenant_fair_share_model_enabled(self, model_group: object) -> bool:
        normalized_model_group = str(model_group or "").strip()
        if not normalized_model_group:
            return True
        disabled = {
            str(disabled_model_group or "").strip()
            for disabled_model_group in self.config.tenant_fair_share_disabled_model_groups
            if str(disabled_model_group or "").strip()
        }
        return normalized_model_group not in disabled

    @staticmethod
    def _record_shadow_fair_share_decision(
        *,
        recommendation: Any,
        claim: Any | None,
        model_group: str,
        service_tier: str,
    ) -> None:
        flow = getattr(recommendation, "flow", None)
        if flow is None:
            increment_batch_scheduler_shadow_decision(
                model_group=model_group,
                service_tier=service_tier,
                result="no_recommendation",
            )
            return
        if claim is None:
            increment_batch_scheduler_shadow_decision(
                model_group=model_group,
                service_tier=service_tier,
                result="actual_empty",
            )
            BatchExecutorWorker._log_shadow_fair_share_job_decision(
                recommendation=recommendation,
                claim=None,
                model_group=model_group,
                service_tier=service_tier,
                result="actual_empty",
                claim_matches_recommended_flow=False,
            )
            BatchExecutorWorker._observe_shadow_fair_share_ratio(
                recommendation=recommendation,
                claim=None,
                claim_matches_recommended_flow=False,
                model_group=model_group,
                service_tier=service_tier,
            )
            return
        claim_matches_flow = BatchExecutorWorker._shadow_claim_matches_flow(claim=claim, flow=flow)
        increment_batch_scheduler_shadow_decision(
            model_group=model_group,
            service_tier=service_tier,
            result="match" if claim_matches_flow else "mismatch",
        )
        recommended_batch_id = str(getattr(recommendation, "recommended_batch_id", "") or "")
        if recommended_batch_id and claim_matches_flow:
            increment_batch_scheduler_shadow_decision(
                model_group=model_group,
                service_tier=service_tier,
                result=(
                    "job_match"
                    if str(getattr(claim, "batch_id", "") or "") == recommended_batch_id
                    else "job_mismatch"
                ),
            )
        if recommended_batch_id:
            BatchExecutorWorker._log_shadow_fair_share_job_decision(
                recommendation=recommendation,
                claim=claim,
                model_group=model_group,
                service_tier=service_tier,
                result=(
                    "job_match"
                    if claim_matches_flow
                    and str(getattr(claim, "batch_id", "") or "") == recommended_batch_id
                    else "job_mismatch"
                    if claim_matches_flow
                    else "flow_mismatch"
                ),
                claim_matches_recommended_flow=claim_matches_flow,
            )
        BatchExecutorWorker._observe_shadow_fair_share_ratio(
            recommendation=recommendation,
            claim=claim,
            claim_matches_recommended_flow=claim_matches_flow,
            model_group=model_group,
            service_tier=service_tier,
        )

    @staticmethod
    def _shadow_claim_matches_flow(*, claim: Any, flow: Any) -> bool:
        return (
            str(getattr(claim, "model_group", "") or "") == str(getattr(flow, "model_group", "") or "")
            and str(getattr(claim, "service_tier", "") or "")
            == str(getattr(flow, "service_tier", "") or "")
            and str(getattr(claim, "tenant_scope_type", "") or "")
            == str(getattr(flow, "tenant_scope_type", "") or "")
            and str(getattr(claim, "tenant_scope_id", "") or "")
            == str(getattr(flow, "tenant_scope_id", "") or "")
        )

    @staticmethod
    def _log_shadow_fair_share_job_decision(
        *,
        recommendation: Any,
        claim: Any | None,
        model_group: str,
        service_tier: str,
        result: str,
        claim_matches_recommended_flow: bool,
    ) -> None:
        flow = getattr(recommendation, "flow", None)
        if flow is None:
            return
        logger.info(
            "batch scheduler shadow job decision",
            extra={
                "shadow_model_group": model_group,
                "shadow_service_tier": service_tier,
                "shadow_tenant_scope_type": str(getattr(flow, "tenant_scope_type", "") or ""),
                "shadow_tenant_scope_id": BatchExecutorWorker._shadow_tenant_scope_label(flow),
                "shadow_recommended_batch_id": str(
                    getattr(recommendation, "recommended_batch_id", "") or ""
                ),
                "shadow_actual_batch_id": str(getattr(claim, "batch_id", "") or ""),
                "shadow_recommended_size_class": str(
                    getattr(recommendation, "recommended_size_class", "") or ""
                ),
                "shadow_recommended_scheduler_rank": getattr(
                    recommendation,
                    "recommended_scheduler_rank",
                    None,
                ),
                "shadow_recommended_age_credit_work_units": getattr(
                    recommendation,
                    "recommended_age_credit_work_units",
                    None,
                ),
                "shadow_recommended_policy_reason": str(
                    getattr(recommendation, "recommended_policy_reason", "") or ""
                ),
                "shadow_result": result,
                "shadow_flow_match": bool(claim_matches_recommended_flow),
            },
        )

    @staticmethod
    def _shadow_tenant_scope_label(flow: Any) -> str:
        scope_type = str(getattr(flow, "tenant_scope_type", "") or "").strip()
        scope_id = str(getattr(flow, "tenant_scope_id", "") or "").strip()
        if not scope_id:
            return ""
        if scope_type == "api_key":
            return "api_key"
        digest = hashlib.sha256(scope_id.encode("utf-8")).hexdigest()
        return f"{scope_type or 'tenant'}:{digest[:12]}"

    @staticmethod
    def _observe_shadow_fair_share_ratio(
        *,
        recommendation: Any,
        claim: Any | None,
        claim_matches_recommended_flow: bool,
        model_group: str,
        service_tier: str,
    ) -> None:
        expected_share = getattr(recommendation, "expected_share", None)
        if expected_share is None:
            return
        try:
            bounded_expected_share = float(expected_share)
        except (TypeError, ValueError):
            return
        if bounded_expected_share <= 0:
            return
        flow = getattr(recommendation, "flow", None)
        if flow is None:
            return
        try:
            selected_in_flight_work_units = max(0, int(getattr(flow, "in_flight_work_units", 0) or 0))
            total_in_flight_work_units = max(
                0,
                int(getattr(recommendation, "total_in_flight_work_units", 0) or 0),
            )
            claimed_work_units = max(1, int(getattr(claim, "claimed_work_units", 1) or 1))
        except (TypeError, ValueError):
            return
        if claim is None:
            claimed_work_units = 0
        if claim_matches_recommended_flow:
            selected_in_flight_work_units += claimed_work_units
        total_work_units = total_in_flight_work_units + claimed_work_units
        if total_work_units <= 0:
            actual_share = 0.0
        else:
            actual_share = float(selected_in_flight_work_units) / float(total_work_units)
        observe_batch_scheduler_shadow_share_ratio(
            model_group=model_group,
            service_tier=service_tier,
            ratio=actual_share / bounded_expected_share,
        )

    async def _claim_model_capacity_work_slice(
        self,
        *,
        snapshot: Any,
        selection: Any,
        capacity_max_in_flight_items: int,
        capacity_max_in_flight_work_units: int | None,
    ):
        return await self.repository.claim_next_work(
            worker_id=self.config.worker_id,
            max_items=selection.max_items,
            max_work_units=selection.max_work_units,
            lease_seconds=self.config.item_lease_seconds,
            allowed_model_groups=[snapshot.model_group],
            service_tier=snapshot.service_tier,
            claim_order="fifo",
            capacity_model_group=snapshot.model_group,
            capacity_service_tier=snapshot.service_tier,
            capacity_max_in_flight_items=capacity_max_in_flight_items,
            capacity_max_in_flight_work_units=capacity_max_in_flight_work_units,
            allow_oversized_first_item=False,
        )

    @staticmethod
    def _capacity_claim_caps(snapshot: Any, *, max_items: int, max_work_units: int) -> tuple[int, int | None]:
        in_flight_items = max(0, int(getattr(snapshot, "in_flight_items", 0) or 0))
        available_in_flight_items = max(
            0,
            int(getattr(snapshot, "available_in_flight_items", max_items) or 0),
        )
        in_flight_work_units = max(0, int(getattr(snapshot, "in_flight_work_units", 0) or 0))
        tpm_remaining = getattr(snapshot, "tpm_remaining", None)
        work_unit_cap = (
            in_flight_work_units + max(0, int(tpm_remaining))
            if tpm_remaining is not None
            else None
        )
        return in_flight_items + available_in_flight_items, work_unit_cap

    async def _capacity_empty_claim_result(self, *, snapshot: Any, max_items: int, max_work_units: int) -> str:
        diagnose_model_group_work_claim_empty = getattr(
            self.repository,
            "diagnose_model_group_work_claim_empty",
            None,
        )
        if not callable(diagnose_model_group_work_claim_empty):
            return "empty_after_selection"
        capacity_max_in_flight_items, capacity_max_in_flight_work_units = self._capacity_claim_caps(
            snapshot,
            max_items=max_items,
            max_work_units=max_work_units,
        )
        try:
            return str(
                await diagnose_model_group_work_claim_empty(
                    model_group=snapshot.model_group,
                    service_tier=snapshot.service_tier,
                    max_work_units=max_work_units,
                    capacity_max_in_flight_items=capacity_max_in_flight_items,
                    capacity_max_in_flight_work_units=capacity_max_in_flight_work_units,
                )
                or "empty_after_selection"
            )
        except Exception:
            logger.warning("batch capacity empty claim diagnostic failed", exc_info=True)
            return "empty_after_selection"

    async def _claim_legacy_work_slice(self, *, max_items: int, max_work_units: int, resolver: Any):
        claim = await self.repository.claim_next_work(
            worker_id=self.config.worker_id,
            max_items=max_items,
            max_work_units=max_work_units,
            lease_seconds=self.config.item_lease_seconds,
            legacy_only=True,
            claim_order="fifo",
        )
        resolver.record_claim_result(
            model_group="legacy",
            result="claimed" if claim is not None else "empty",
        )
        return claim

    async def _empty_work_claim_reason(self) -> str:
        diagnose_empty_work_claim = getattr(self.repository, "diagnose_empty_work_claim", None)
        if not callable(diagnose_empty_work_claim):
            return "no_available_work"
        try:
            reason = await diagnose_empty_work_claim()
        except Exception:
            logger.warning("batch work claim empty diagnostic failed", exc_info=True)
            return "no_available_work"
        return str(reason or "no_available_work")

    async def _prepare_item_for_execution(self, job, item) -> _PreparedEmbeddingItem | _PreparedChatItem:  # noqa: ANN001
        self._sync_dependencies()
        return await self._execution_engine.prepare_item_for_execution(job, item)

    async def _process_item(self, job, item) -> None:  # noqa: ANN001
        self._sync_dependencies()
        await self._execution_engine.process_item(job, item, prepare_item=self._prepare_item_for_execution)

    async def _process_items(self, job, items) -> None:  # noqa: ANN001
        self._sync_dependencies()
        await self._execution_engine.process_items(
            job,
            items,
            prepare_item=self._prepare_item_for_execution,
            process_item=self._process_item,
        )

    def _resolve_final_status(self, job) -> str:  # noqa: ANN001
        self._sync_dependencies()
        return self._artifact_finalizer.resolve_final_status(job)

    async def _finalize_with_retry(self, job) -> None:  # noqa: ANN001
        self._sync_dependencies()
        await self._artifact_finalizer.finalize_with_retry(job, finalize_artifacts=self._finalize_artifacts)

    async def _iter_output_lines(self, batch_id: str, *, endpoint: str = "/v1/embeddings"):  # noqa: ANN201
        self._sync_dependencies()
        async for line in self._artifact_finalizer.iter_output_lines(batch_id, endpoint=endpoint):
            yield line

    async def _iter_error_lines(self, batch_id: str):  # noqa: ANN201
        self._sync_dependencies()
        async for line in self._artifact_finalizer.iter_error_lines(batch_id):
            yield line

    async def _finalize_artifacts(self, job) -> None:  # noqa: ANN001
        self._sync_dependencies()
        await self._artifact_finalizer.finalize_artifacts(job)

    def _start_heartbeat(self, *, renew, label: str) -> asyncio.Task[None]:
        return asyncio.create_task(self._run_heartbeat(renew=renew, label=label))

    async def _run_heartbeat(self, *, renew, label: str) -> None:
        # Loop until cancelled by _stop_heartbeat in the owning finally block.
        # Intentionally NOT gated on self._running: graceful shutdown flips that
        # flag while in-flight work is still holding the lease, and dropping
        # the heartbeat there would let the lease expire mid-item and allow
        # another worker to reclaim the same item.
        while True:
            await asyncio.sleep(self.config.heartbeat_interval_seconds)
            try:
                renewed = await renew()
            except Exception:
                logger.warning("batch lease heartbeat failed target=%s", label, exc_info=True)
                continue
            if not renewed:
                logger.warning("batch lease heartbeat lost ownership target=%s", label)
                return

    async def _stop_heartbeat(self, task: asyncio.Task[None]) -> None:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
