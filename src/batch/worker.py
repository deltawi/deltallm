from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from time import perf_counter
from typing import Any, AsyncIterator

import httpx

from src.batch.models import BatchJobStatus
from src.batch.models import is_operator_failed_reason
from src.batch.repository import BatchRepository
from src.batch.storage import BatchArtifactStorage
from src.billing.cost import ModelPricing, completion_cost
from src.metrics import (
    increment_batch_artifact_failure,
    increment_batch_finalization_retry,
    observe_batch_finalize_latency,
    observe_batch_item_execution_latency,
    publish_batch_runtime_summary,
    set_batch_worker_saturation,
    increment_request,
    increment_spend,
    increment_usage,
)
from src.models.requests import EmbeddingRequest
from src.providers.resolution import resolve_provider
from src.router.usage import record_router_usage
from src.routers.routing_decision import route_failover_kwargs
from src.routers.embeddings import _execute_embedding

logger = logging.getLogger(__name__)


@dataclass
class BatchWorkerConfig:
    worker_id: str
    poll_interval_seconds: float = 1.0
    heartbeat_interval_seconds: float = 15.0
    job_lease_seconds: int = 120
    item_lease_seconds: int = 360
    finalization_retry_delay_seconds: int = 60
    worker_concurrency: int = 4
    item_buffer_multiplier: int = 2
    finalization_page_size: int = 500
    item_claim_limit: int = 20
    max_attempts: int = 3
    completed_artifact_retention_days: int = 7
    failed_artifact_retention_days: int = 14


@dataclass
class _RequestShim:
    app: Any


class BatchExecutorWorker:
    def __init__(
        self,
        *,
        app: Any,
        repository: BatchRepository,
        storage: BatchArtifactStorage,
        config: BatchWorkerConfig,
    ) -> None:
        self.app = app
        self.repository = repository
        self.storage = storage
        self.config = config
        self._running = False

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

    async def _refresh_batch_runtime_metrics(self) -> None:
        try:
            summary = await self.repository.summarize_runtime_statuses(now=datetime.now(tz=UTC))
            publish_batch_runtime_summary(summary)
        except Exception:
            logger.debug("batch worker runtime metrics refresh failed", exc_info=True)
            return

    async def process_once(self) -> bool:
        job = await self.repository.claim_next_job(
            worker_id=self.config.worker_id,
            lease_seconds=self.config.job_lease_seconds,
        )
        if job is None:
            set_batch_worker_saturation(worker_id=self.config.worker_id, active=0, capacity=self.config.worker_concurrency)
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
            set_batch_worker_saturation(worker_id=self.config.worker_id, active=0, capacity=self.config.worker_concurrency)
            await self._stop_heartbeat(job_heartbeat)
            await self.repository.release_job_lease(batch_id=job.batch_id, worker_id=self.config.worker_id)

    async def _process_item(self, job, item) -> None:
        batch_id = job.batch_id
        started = perf_counter()
        request_body = item.request_body if isinstance(item.request_body, dict) else {}
        model_name = str(request_body.get("model") or job.model or "")
        payload: EmbeddingRequest | None = None
        primary = None
        item_heartbeat: asyncio.Task[None] | None = None
        request_shim = _RequestShim(app=self.app)
        app_router = self.app.state.router
        request_context = {"metadata": {}, "user_id": "batch-worker"}
        budget_service = getattr(self.app.state, "budget_service", None)
        try:
            payload = EmbeddingRequest.model_validate(item.request_body)
            model_name = payload.model
            if budget_service is not None:
                await budget_service.check_budgets(
                    api_key=job.created_by_api_key,
                    user_id=job.created_by_user_id,
                    team_id=job.created_by_team_id,
                    organization_id=job.created_by_organization_id,
                    model=payload.model,
                )
            model_group = app_router.resolve_model_group(payload.model)
            primary = app_router.require_deployment(
                model_group=model_group,
                deployment=await app_router.select_deployment(model_group, request_context),
            )
            failover_kwargs = route_failover_kwargs(request_context)
            item_heartbeat = self._start_heartbeat(
                renew=lambda: self.repository.renew_item_lease(
                    item_id=item.item_id,
                    worker_id=self.config.worker_id,
                    lease_seconds=self.config.item_lease_seconds,
                ),
                label=f"item:{item.item_id}",
            )
            data, served_deployment = await self.app.state.failover_manager.execute_with_failover(
                primary_deployment=primary,
                model_group=model_group,
                execute=lambda dep: _execute_embedding(request_shim, payload, dep),
                return_deployment=True,
                **failover_kwargs,
            )
            api_provider = resolve_provider(served_deployment.deltallm_params)
            usage = data.get("usage") or {}
            deployment_pricing = None
            if served_deployment.input_cost_per_token or served_deployment.output_cost_per_token:
                deployment_pricing = ModelPricing(
                    input_cost_per_token=served_deployment.input_cost_per_token,
                    output_cost_per_token=served_deployment.output_cost_per_token,
                )
            model_info = served_deployment.model_info or {}
            billed_cost = completion_cost(
                model=payload.model,
                usage=usage,
                cache_hit=False,
                custom_pricing=deployment_pricing,
                pricing_tier="batch",
                model_info=model_info,
            )
            provider_cost = completion_cost(
                model=payload.model,
                usage=usage,
                cache_hit=False,
                custom_pricing=deployment_pricing,
                pricing_tier="sync",
                model_info=model_info,
            )
            data.pop("_api_latency_ms", None)
            data.pop("_api_base", None)
            data.pop("_deployment_model", None)
            data["_provider"] = api_provider
            served_deployment_id = str(
                getattr(served_deployment, "deployment_id", None)
                or getattr(primary, "deployment_id", None)
                or ""
            )
            updated = await self.repository.mark_item_completed(
                item_id=item.item_id,
                worker_id=self.config.worker_id,
                response_body=data,
                usage=usage,
                provider_cost=provider_cost,
                billed_cost=billed_cost,
            )
            if not updated:
                logger.warning("batch item completion skipped after lease loss batch_id=%s item_id=%s", batch_id, item.item_id)
                return
            await self._record_success_runtime_hooks(
                batch_id=batch_id,
                item_id=item.item_id,
                deployment_id=served_deployment_id,
                usage=usage,
            )
            await self._record_success_side_effects(
                job=job,
                item=item,
                payload=payload,
                usage=usage,
                api_provider=api_provider,
                billed_cost=billed_cost,
                provider_cost=provider_cost,
                api_base=served_deployment.deltallm_params.get("api_base"),
                deployment_model=str(served_deployment.deltallm_params.get("model") or "") or None,
                batch_id=batch_id,
            )
            observe_batch_item_execution_latency(status="success", latency_seconds=perf_counter() - started)
            return
        except Exception as exc:
            retryable = self._is_retryable(exc) and item.attempts < self.config.max_attempts
            error_payload = {"message": str(exc), "type": exc.__class__.__name__}
            retry_delay = min(30, max(1, item.attempts * 2))
            updated = await self.repository.mark_item_failed(
                item_id=item.item_id,
                worker_id=self.config.worker_id,
                error_body=error_payload,
                last_error=str(exc),
                retryable=retryable,
                retry_delay_seconds=retry_delay,
            )
            if not updated:
                logger.warning("batch item failure update skipped after lease loss batch_id=%s item_id=%s", batch_id, item.item_id)
                return
            await self.repository.refresh_job_progress(batch_id)
            await self._record_failure_runtime_hooks(
                job=job,
                item=item,
                batch_id=batch_id,
                model_name=model_name,
                exc=exc,
                deployment_id=primary.deployment_id if primary is not None else None,
            )
            observe_batch_item_execution_latency(status="error", latency_seconds=perf_counter() - started)
        finally:
            if item_heartbeat is not None:
                await self._stop_heartbeat(item_heartbeat)

    async def _process_items(self, job, items) -> None:
        if not items:
            set_batch_worker_saturation(worker_id=self.config.worker_id, active=0, capacity=self.config.worker_concurrency)
            return
        semaphore = asyncio.Semaphore(self.config.worker_concurrency)
        active = 0

        async def _runner(item) -> None:  # noqa: ANN001
            nonlocal active
            async with semaphore:
                active += 1
                set_batch_worker_saturation(worker_id=self.config.worker_id, active=active, capacity=self.config.worker_concurrency)
                try:
                    await self._process_item(job, item)
                finally:
                    active -= 1
                    set_batch_worker_saturation(worker_id=self.config.worker_id, active=active, capacity=self.config.worker_concurrency)

        async with asyncio.TaskGroup() as task_group:
            for item in items:
                task_group.create_task(_runner(item))

    async def _record_success_runtime_hooks(
        self,
        *,
        batch_id: str,
        item_id: str,
        deployment_id: str,
        usage: dict[str, Any],
    ) -> None:
        passive_health_tracker = getattr(self.app.state, "passive_health_tracker", None)
        if passive_health_tracker is not None and deployment_id:
            try:
                await passive_health_tracker.record_request_outcome(deployment_id, success=True)
            except Exception as exc:
                logger.warning(
                    "batch passive health success hook failed batch_id=%s item_id=%s deployment_id=%s error=%s",
                    batch_id,
                    item_id,
                    deployment_id,
                    exc,
                )
        router_state_backend = getattr(self.app.state, "router_state_backend", None)
        if router_state_backend is not None and deployment_id:
            try:
                await record_router_usage(
                    router_state_backend,
                    deployment_id,
                    mode="embedding",
                    usage=usage,
                )
            except Exception as exc:
                logger.warning(
                    "batch router usage hook failed batch_id=%s item_id=%s deployment_id=%s error=%s",
                    batch_id,
                    item_id,
                    deployment_id,
                    exc,
                )

    async def _record_failure_runtime_hooks(
        self,
        *,
        job,
        item,
        batch_id: str,
        model_name: str,
        exc: Exception,
        deployment_id: str | None,
    ) -> None:
        passive_health_tracker = getattr(self.app.state, "passive_health_tracker", None)
        if passive_health_tracker is not None and deployment_id is not None:
            try:
                await passive_health_tracker.record_request_outcome(deployment_id, success=False, error=str(exc))
            except Exception as hook_exc:
                logger.warning(
                    "batch passive health failure hook failed batch_id=%s item_id=%s deployment_id=%s error=%s",
                    batch_id,
                    item.item_id,
                    deployment_id,
                    hook_exc,
                )
        spend_tracking_service = getattr(self.app.state, "spend_tracking_service", None)
        if job.created_by_api_key and spend_tracking_service is not None:
            try:
                await spend_tracking_service.log_request_failure(
                    request_id=f"batch:{batch_id}:{item.item_id}",
                    api_key=job.created_by_api_key,
                    user_id=job.created_by_user_id,
                    team_id=job.created_by_team_id,
                    organization_id=job.created_by_organization_id,
                    end_user_id=None,
                    model=model_name,
                    call_type="embedding_batch",
                    metadata={"batch_id": batch_id, "batch_item_id": item.item_id},
                    cache_hit=False,
                    start_time=None,
                    end_time=datetime.now(tz=UTC),
                    exc=exc,
                )
            except Exception as hook_exc:
                logger.warning(
                    "batch failure logging hook failed batch_id=%s item_id=%s error=%s",
                    batch_id,
                    item.item_id,
                    hook_exc,
                )

    async def _record_success_side_effects(
        self,
        *,
        job,
        item,
        payload: EmbeddingRequest,
        usage: dict[str, Any],
        api_provider: str,
        billed_cost: float,
        provider_cost: float,
        api_base: str | None,
        deployment_model: str | None,
        batch_id: str,
    ) -> None:
        try:
            increment_request(
                model=payload.model,
                api_provider=api_provider,
                api_key=job.created_by_api_key,
                user=job.created_by_user_id,
                team=job.created_by_team_id,
                status_code=200,
            )
            increment_usage(
                model=payload.model,
                api_provider=api_provider,
                api_key=job.created_by_api_key,
                user=job.created_by_user_id,
                team=job.created_by_team_id,
                prompt_tokens=int(usage.get("prompt_tokens", 0) or 0),
                completion_tokens=int(usage.get("completion_tokens", 0) or 0),
            )
            increment_spend(
                model=payload.model,
                api_provider=api_provider,
                api_key=job.created_by_api_key,
                user=job.created_by_user_id,
                team=job.created_by_team_id,
                spend=billed_cost,
            )
            if job.created_by_api_key:
                await self.app.state.spend_tracking_service.log_spend(
                    request_id=f"batch:{batch_id}:{item.item_id}",
                    api_key=job.created_by_api_key,
                    user_id=job.created_by_user_id,
                    team_id=job.created_by_team_id,
                    organization_id=job.created_by_organization_id,
                    end_user_id=None,
                    model=payload.model,
                    call_type="embedding_batch",
                    usage={
                        "prompt_tokens": int(usage.get("prompt_tokens", 0) or 0),
                        "completion_tokens": int(usage.get("completion_tokens", 0) or 0),
                        "total_tokens": int(usage.get("total_tokens", 0) or 0),
                    },
                    cost=billed_cost,
                    metadata={
                        "api_base": api_base,
                        "provider": api_provider,
                        "deployment_model": deployment_model,
                        "batch_id": batch_id,
                        "batch_item_id": item.item_id,
                        "execution_mode": job.execution_mode,
                        "provider_cost": provider_cost,
                        "pricing_tier": "batch",
                    },
                    cache_hit=False,
                    start_time=None,
                    end_time=datetime.now(tz=UTC),
                )
        except Exception as exc:
            logger.warning(
                "batch side effects failed batch_id=%s item_id=%s error=%s",
                batch_id,
                item.item_id,
                exc,
            )

    def _is_retryable(self, exc: Exception) -> bool:
        if isinstance(exc, httpx.TimeoutException):
            return True
        if isinstance(exc, httpx.HTTPStatusError):
            status_code = exc.response.status_code
            return status_code == 429 or status_code >= 500
        return False

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

    def _resolve_final_status(self, job) -> str:
        if job.cancel_requested_at is not None:
            return BatchJobStatus.CANCELLED
        if is_operator_failed_reason(job.provider_error):
            return BatchJobStatus.FAILED
        if job.completed_items == 0 and job.failed_items > 0:
            return BatchJobStatus.FAILED
        return BatchJobStatus.COMPLETED

    async def _finalize_with_retry(self, job) -> None:
        started = perf_counter()
        try:
            await self._finalize_artifacts(job)
            observe_batch_finalize_latency(status="success", latency_seconds=perf_counter() - started)
        except Exception as exc:
            logger.warning("batch finalization failed batch_id=%s error=%s", job.batch_id, exc, exc_info=True)
            rescheduled = await self.repository.reschedule_finalization(
                batch_id=job.batch_id,
                worker_id=self.config.worker_id,
                retry_delay_seconds=self.config.finalization_retry_delay_seconds,
            )
            if not rescheduled:
                logger.warning("batch finalization retry skipped after lease loss batch_id=%s", job.batch_id)
                increment_batch_finalization_retry(result="lease_lost")
            else:
                logger.info(
                    "batch finalization retry scheduled batch_id=%s worker_id=%s delay_seconds=%s",
                    job.batch_id,
                    self.config.worker_id,
                    self.config.finalization_retry_delay_seconds,
                )
                increment_batch_finalization_retry(result="scheduled")
            observe_batch_finalize_latency(status="error", latency_seconds=perf_counter() - started)

    async def _cleanup_unattached_artifacts(self, artifacts: list[tuple[str, str]]) -> None:
        for file_id, storage_key in artifacts:
            with contextlib.suppress(Exception):
                await self.storage.delete(storage_key)
            with contextlib.suppress(Exception):
                await self.repository.delete_file(file_id)

    async def _iter_batch_items(self, batch_id: str) -> AsyncIterator[Any]:
        if hasattr(self.repository, "list_items_page"):
            after_line_number: int | None = None
            while True:
                page = await self.repository.list_items_page(
                    batch_id=batch_id,
                    limit=self.config.finalization_page_size,
                    after_line_number=after_line_number,
                )
                if not page:
                    break
                for item in page:
                    yield item
                after_line_number = page[-1].line_number
            return

        for item in await self.repository.list_items(batch_id):
            yield item

    async def _iter_output_lines(self, batch_id: str) -> AsyncIterator[str]:
        async for item in self._iter_batch_items(batch_id):
            if item.status == "completed":
                yield json.dumps({"custom_id": item.custom_id, "response": item.response_body or {}})

    async def _iter_error_lines(self, batch_id: str) -> AsyncIterator[str]:
        async for item in self._iter_batch_items(batch_id):
            if item.status in {"failed", "cancelled"}:
                yield json.dumps({"custom_id": item.custom_id, "error": item.error_body or {}})

    async def _finalize_artifacts(self, job) -> None:
        storage_backend = getattr(self.storage, "backend_name", "local")
        created_artifacts: list[tuple[str, str]] = []
        output_file_id: str | None = None
        error_file_id: str | None = None
        final_status = self._resolve_final_status(job)
        try:
            if job.completed_items > 0:
                key, size, checksum = await self.storage.write_lines_stream(
                    purpose="batch_output",
                    filename=f"{job.batch_id}-output.jsonl",
                    lines=self._iter_output_lines(job.batch_id),
                )
                file_record = await self.repository.create_file(
                    purpose="batch_output",
                    filename=f"{job.batch_id}-output.jsonl",
                    bytes_size=size,
                    storage_backend=storage_backend,
                    storage_key=key,
                    checksum=checksum,
                    created_by_api_key=job.created_by_api_key,
                    created_by_user_id=job.created_by_user_id,
                    created_by_team_id=job.created_by_team_id,
                    created_by_organization_id=job.created_by_organization_id,
                    expires_at=datetime.now(tz=UTC) + timedelta(days=self.config.completed_artifact_retention_days),
                )
                if file_record is None:
                    increment_batch_artifact_failure(operation="create_record", backend=storage_backend)
                    raise RuntimeError(f"Failed to create output artifact record for batch {job.batch_id}")
                created_artifacts.append((file_record.file_id, key))
                output_file_id = file_record.file_id

            if job.failed_items > 0 or job.cancelled_items > 0:
                key, size, checksum = await self.storage.write_lines_stream(
                    purpose="batch_error",
                    filename=f"{job.batch_id}-error.jsonl",
                    lines=self._iter_error_lines(job.batch_id),
                )
                retention_days = (
                    self.config.failed_artifact_retention_days
                    if final_status in {BatchJobStatus.FAILED, BatchJobStatus.CANCELLED}
                    else self.config.completed_artifact_retention_days
                )
                file_record = await self.repository.create_file(
                    purpose="batch_error",
                    filename=f"{job.batch_id}-error.jsonl",
                    bytes_size=size,
                    storage_backend=storage_backend,
                    storage_key=key,
                    checksum=checksum,
                    created_by_api_key=job.created_by_api_key,
                    created_by_user_id=job.created_by_user_id,
                    created_by_team_id=job.created_by_team_id,
                    created_by_organization_id=job.created_by_organization_id,
                    expires_at=datetime.now(tz=UTC) + timedelta(days=retention_days),
                )
                if file_record is None:
                    increment_batch_artifact_failure(operation="create_record", backend=storage_backend)
                    raise RuntimeError(f"Failed to create error artifact record for batch {job.batch_id}")
                created_artifacts.append((file_record.file_id, key))
                error_file_id = file_record.file_id

            finalized = await self.repository.attach_artifacts_and_finalize(
                batch_id=job.batch_id,
                output_file_id=output_file_id,
                error_file_id=error_file_id,
                final_status=final_status,
                worker_id=self.config.worker_id,
            )
            if finalized is None:
                raise RuntimeError(f"Failed to finalize batch {job.batch_id}")
        except Exception:
            increment_batch_artifact_failure(operation="write_or_finalize", backend=storage_backend)
            await self._cleanup_unattached_artifacts(created_artifacts)
            raise
        logger.info(
            "batch finalized id=%s status=%s completed=%s failed=%s",
            job.batch_id,
            final_status,
            job.completed_items,
            job.failed_items,
        )
