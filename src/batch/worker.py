from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from src.batch.models import BatchJobStatus
from src.batch.repository import BatchRepository
from src.batch.storage import BatchArtifactStorage
from src.billing.cost import ModelPricing, completion_cost
from src.metrics import increment_request, increment_spend, increment_usage
from src.models.requests import EmbeddingRequest
from src.providers.resolution import resolve_provider
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

    async def process_once(self) -> bool:
        job = await self.repository.claim_next_job(
            worker_id=self.config.worker_id,
            lease_seconds=self.config.job_lease_seconds,
        )
        if job is None:
            return False
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
                limit=self.config.item_claim_limit,
                lease_seconds=self.config.item_lease_seconds,
            )
            for item in items:
                await self._process_item(job, item)

            refreshed = await self.repository.refresh_job_progress(job.batch_id)
            if refreshed and refreshed.status == BatchJobStatus.FINALIZING:
                await self._finalize_with_retry(refreshed)
            return True
        finally:
            await self._stop_heartbeat(job_heartbeat)
            await self.repository.release_job_lease(batch_id=job.batch_id, worker_id=self.config.worker_id)

    async def _process_item(self, job, item) -> None:
        batch_id = job.batch_id
        payload = EmbeddingRequest.model_validate(item.request_body)
        request_shim = _RequestShim(app=self.app)
        app_router = self.app.state.router
        request_context = {"metadata": {}, "user_id": "batch-worker"}
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
        try:
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
        finally:
            await self._stop_heartbeat(item_heartbeat)

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
                    organization_id=None,
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
        while self._running:
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
        if job.completed_items == 0 and job.failed_items > 0:
            return BatchJobStatus.FAILED
        return BatchJobStatus.COMPLETED

    async def _finalize_with_retry(self, job) -> None:
        try:
            await self._finalize_artifacts(job)
        except Exception as exc:
            logger.warning("batch finalization failed batch_id=%s error=%s", job.batch_id, exc, exc_info=True)
            rescheduled = await self.repository.reschedule_finalization(
                batch_id=job.batch_id,
                worker_id=self.config.worker_id,
                retry_delay_seconds=self.config.finalization_retry_delay_seconds,
            )
            if not rescheduled:
                logger.warning("batch finalization retry skipped after lease loss batch_id=%s", job.batch_id)

    async def _cleanup_unattached_artifacts(self, artifacts: list[tuple[str, str]]) -> None:
        for file_id, storage_key in artifacts:
            with contextlib.suppress(Exception):
                await self.storage.delete(storage_key)
            with contextlib.suppress(Exception):
                await self.repository.delete_file(file_id)

    async def _finalize_artifacts(self, job) -> None:
        items = await self.repository.list_items(job.batch_id)
        output_lines: list[str] = []
        error_lines: list[str] = []
        for item in items:
            if item.status == "completed":
                output_lines.append(json.dumps({"custom_id": item.custom_id, "response": item.response_body or {}}))
            elif item.status in {"failed", "cancelled"}:
                error_lines.append(json.dumps({"custom_id": item.custom_id, "error": item.error_body or {}}))

        storage_backend = getattr(self.storage, "backend_name", "local")
        created_artifacts: list[tuple[str, str]] = []
        output_file_id: str | None = None
        error_file_id: str | None = None
        final_status = self._resolve_final_status(job)
        try:
            if output_lines:
                key, size, checksum = await self.storage.write_lines(
                    purpose="batch_output",
                    filename=f"{job.batch_id}-output.jsonl",
                    lines=output_lines,
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
                    expires_at=datetime.now(tz=UTC) + timedelta(days=self.config.completed_artifact_retention_days),
                )
                if file_record is None:
                    raise RuntimeError(f"Failed to create output artifact record for batch {job.batch_id}")
                created_artifacts.append((file_record.file_id, key))
                output_file_id = file_record.file_id

            if error_lines:
                key, size, checksum = await self.storage.write_lines(
                    purpose="batch_error",
                    filename=f"{job.batch_id}-error.jsonl",
                    lines=error_lines,
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
                    expires_at=datetime.now(tz=UTC) + timedelta(days=retention_days),
                )
                if file_record is None:
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
            await self._cleanup_unattached_artifacts(created_artifacts)
            raise
        logger.info(
            "batch finalized id=%s status=%s completed=%s failed=%s",
            job.batch_id,
            final_status,
            job.completed_items,
            job.failed_items,
        )
