from __future__ import annotations

import asyncio
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
from src.metrics import increment_request, increment_spend, increment_usage, infer_provider
from src.models.requests import EmbeddingRequest
from src.routers.routing_decision import route_failover_kwargs
from src.routers.embeddings import _execute_embedding

logger = logging.getLogger(__name__)


@dataclass
class BatchWorkerConfig:
    worker_id: str
    poll_interval_seconds: float = 1.0
    job_lease_seconds: int = 30
    item_lease_seconds: int = 120
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
            did_work = await self.process_once()
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
        try:
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
            if refreshed and refreshed.status in {
                BatchJobStatus.COMPLETED,
                BatchJobStatus.CANCELLED,
                BatchJobStatus.FAILED,
            }:
                await self._finalize_artifacts(refreshed)
            return True
        finally:
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
        try:
            data, served_deployment = await self.app.state.failover_manager.execute_with_failover(
                primary_deployment=primary,
                model_group=model_group,
                execute=lambda dep: _execute_embedding(request_shim, payload, dep),
                return_deployment=True,
                **failover_kwargs,
            )
            api_provider = infer_provider(served_deployment.deltallm_params.get("model"))
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
            await self.repository.mark_item_completed(
                item_id=item.item_id,
                response_body=data,
                usage=usage,
                provider_cost=provider_cost,
                billed_cost=billed_cost,
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
                batch_id=batch_id,
            )
            return
        except Exception as exc:
            retryable = self._is_retryable(exc) and item.attempts < self.config.max_attempts
            error_payload = {"message": str(exc), "type": exc.__class__.__name__}
            retry_delay = min(30, max(1, item.attempts * 2))
            await self.repository.mark_item_failed(
                item_id=item.item_id,
                error_body=error_payload,
                last_error=str(exc),
                retryable=retryable,
                retry_delay_seconds=retry_delay,
            )
            await self.repository.refresh_job_progress(batch_id)

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

    async def _finalize_artifacts(self, job) -> None:
        items = await self.repository.list_items(job.batch_id)
        output_lines: list[str] = []
        error_lines: list[str] = []
        for item in items:
            if item.status == "completed":
                output_lines.append(json.dumps({"custom_id": item.custom_id, "response": item.response_body or {}}))
            elif item.status in {"failed", "cancelled"}:
                error_lines.append(json.dumps({"custom_id": item.custom_id, "error": item.error_body or {}}))

        output_file_id: str | None = None
        error_file_id: str | None = None
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
                storage_backend="local",
                storage_key=key,
                checksum=checksum,
                created_by_api_key=job.created_by_api_key,
                created_by_user_id=job.created_by_user_id,
                created_by_team_id=job.created_by_team_id,
                expires_at=datetime.now(tz=UTC) + timedelta(days=self.config.completed_artifact_retention_days),
            )
            output_file_id = file_record.file_id if file_record is not None else None

        if error_lines:
            key, size, checksum = await self.storage.write_lines(
                purpose="batch_error",
                filename=f"{job.batch_id}-error.jsonl",
                lines=error_lines,
            )
            retention_days = (
                self.config.failed_artifact_retention_days
                if job.status in {BatchJobStatus.FAILED, BatchJobStatus.CANCELLED}
                else self.config.completed_artifact_retention_days
            )
            file_record = await self.repository.create_file(
                purpose="batch_error",
                filename=f"{job.batch_id}-error.jsonl",
                bytes_size=size,
                storage_backend="local",
                storage_key=key,
                checksum=checksum,
                created_by_api_key=job.created_by_api_key,
                created_by_user_id=job.created_by_user_id,
                created_by_team_id=job.created_by_team_id,
                expires_at=datetime.now(tz=UTC) + timedelta(days=retention_days),
            )
            error_file_id = file_record.file_id if file_record is not None else None

        final_status = job.status
        if final_status == BatchJobStatus.COMPLETED and job.completed_items == 0 and job.failed_items > 0:
            final_status = BatchJobStatus.FAILED
        await self.repository.attach_artifacts_and_finalize(
            batch_id=job.batch_id,
            output_file_id=output_file_id,
            error_file_id=error_file_id,
            final_status=final_status,
        )
        logger.info(
            "batch finalized id=%s status=%s completed=%s failed=%s",
            job.batch_id,
            final_status,
            job.completed_items,
            job.failed_items,
        )
