from __future__ import annotations

import asyncio
from collections import deque
import contextlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from time import perf_counter
from typing import Any, AsyncIterator

import httpx

from src.batch.embedding_microbatch import (
    _ExecutionSignature,
    allocate_embedding_usage,
    build_embedding_execution_signature,
    classify_embedding_microbatch_request,
    estimate_embedding_microbatch_weight,
    resolve_effective_upstream_max_batch_inputs,
)
from src.batch.models import BatchJobStatus
from src.batch.models import is_operator_failed_reason
from src.batch.repository import BatchRepository
from src.batch.storage import BatchArtifactStorage
from src.billing.cost import ModelPricing, completion_cost
from src.metrics import (
    increment_batch_artifact_failure,
    increment_batch_finalization_retry,
    increment_batch_microbatch_inputs,
    increment_batch_microbatch_ineligible_item,
    increment_batch_microbatch_isolation_fallback,
    increment_batch_microbatch_requests,
    observe_batch_finalize_latency,
    observe_batch_item_execution_latency,
    observe_batch_microbatch_size,
    publish_batch_runtime_summary,
    set_batch_worker_saturation,
)
from src.models.requests import EmbeddingRequest
from src.providers.resolution import resolve_provider
from src.router.usage import record_router_usage
from src.routers.routing_decision import route_failover_kwargs
from src.routers.embeddings import _execute_embedding

logger = logging.getLogger(__name__)
_COMPLETION_OUTBOX_MAX_ATTEMPTS = 5


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


@dataclass(slots=True)
class _PreparedEmbeddingItem:
    item: Any
    started_at_monotonic: float
    payload: EmbeddingRequest
    model_name: str
    model_group: str
    primary_deployment: Any
    request_context: dict[str, Any]
    failover_kwargs: dict[str, Any]
    request_shim: _RequestShim
    effective_upstream_max_batch_inputs: int
    microbatch_eligible: bool
    microbatch_ineligible_reason: str | None
    microbatch_weight: int | None
    execution_signature: _ExecutionSignature


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
        self._prepared_item_overrides: dict[str, _PreparedEmbeddingItem] = {}

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

    async def _prepare_item_for_execution(self, job, item) -> _PreparedEmbeddingItem:
        started_at_monotonic = perf_counter()
        payload = EmbeddingRequest.model_validate(item.request_body)
        budget_service = getattr(self.app.state, "budget_service", None)
        if budget_service is not None:
            await budget_service.check_budgets(
                api_key=job.created_by_api_key,
                user_id=job.created_by_user_id,
                team_id=job.created_by_team_id,
                organization_id=job.created_by_organization_id,
                model=payload.model,
            )

        request_context: dict[str, Any] = {"metadata": {}, "user_id": "batch-worker"}
        app_router = self.app.state.router
        model_group = app_router.resolve_model_group(payload.model)
        primary_deployment = app_router.require_deployment(
            model_group=model_group,
            deployment=await app_router.select_deployment(model_group, request_context),
        )
        input_kind, microbatch_eligible, microbatch_ineligible_reason = classify_embedding_microbatch_request(payload)
        primary_deployment_id = str(getattr(primary_deployment, "deployment_id", None) or "")
        return _PreparedEmbeddingItem(
            item=item,
            started_at_monotonic=started_at_monotonic,
            payload=payload,
            model_name=payload.model,
            model_group=model_group,
            primary_deployment=primary_deployment,
            request_context=request_context,
            failover_kwargs=route_failover_kwargs(request_context),
            request_shim=_RequestShim(app=self.app),
            effective_upstream_max_batch_inputs=resolve_effective_upstream_max_batch_inputs(
                getattr(primary_deployment, "model_info", None)
            ),
            microbatch_eligible=microbatch_eligible,
            microbatch_ineligible_reason=microbatch_ineligible_reason,
            microbatch_weight=estimate_embedding_microbatch_weight(payload) if microbatch_eligible else None,
            execution_signature=build_embedding_execution_signature(
                payload=payload,
                model_group=model_group,
                primary_deployment_id=primary_deployment_id,
                input_kind=input_kind,
            ),
        )

    def _deployment_pricing(self, deployment) -> ModelPricing | None:  # noqa: ANN001
        if deployment.input_cost_per_token or deployment.output_cost_per_token:
            return ModelPricing(
                input_cost_per_token=deployment.input_cost_per_token,
                output_cost_per_token=deployment.output_cost_per_token,
            )
        return None

    def _sanitize_embedding_response(self, data: dict[str, Any]) -> tuple[dict[str, Any], str | None, str | None]:
        response_body = dict(data)
        response_body.pop("_api_latency_ms", None)
        api_base = response_body.pop("_api_base", None)
        deployment_model = response_body.pop("_deployment_model", None)
        return response_body, api_base, deployment_model

    def _public_batch_row_id(self, item) -> str:  # noqa: ANN001
        return f"batch_req_{item.item_id}"

    def _public_batch_request_id(self, item) -> str:  # noqa: ANN001
        return f"req_batch_{item.item_id}"

    def _sanitize_public_embedding_body(self, response_body: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(response_body, dict):
            return {}

        sanitized = dict(response_body)
        sanitized.pop("_provider", None)
        sanitized["object"] = str(sanitized.get("object") or "list")

        normalized_rows: list[Any] = []
        for row_number, row in enumerate(sanitized.get("data") or []):
            if not isinstance(row, dict):
                normalized_rows.append(row)
                continue
            normalized_row = dict(row)
            normalized_row["object"] = str(normalized_row.get("object") or "embedding")
            if type(normalized_row.get("index")) is not int:
                normalized_row["index"] = row_number
            normalized_rows.append(normalized_row)
        if isinstance(sanitized.get("data"), list):
            sanitized["data"] = normalized_rows

        return sanitized

    def _serialize_completed_artifact_row(self, item) -> dict[str, Any]:  # noqa: ANN001
        return {
            "id": self._public_batch_row_id(item),
            "custom_id": item.custom_id,
            "response": {
                "status_code": 200,
                "request_id": self._public_batch_request_id(item),
                "body": self._sanitize_public_embedding_body(item.response_body),
            },
            "error": None,
        }

    def _serialize_failed_artifact_row(self, item) -> dict[str, Any]:  # noqa: ANN001
        error_body = dict(item.error_body) if isinstance(item.error_body, dict) else {}
        if not error_body.get("message"):
            error_body["message"] = item.last_error or (
                "Batch request cancelled" if item.status == "cancelled" else "Batch request failed"
            )
        if not error_body.get("type"):
            error_body["type"] = "BatchItemCancelled" if item.status == "cancelled" else "BatchItemError"

        return {
            "id": self._public_batch_row_id(item),
            "custom_id": item.custom_id,
            "response": None,
            "error": error_body,
        }

    def _build_single_item_embedding_response_body(
        self,
        *,
        chunk_response: dict[str, Any],
        item_response: dict[str, Any],
        usage: dict[str, Any],
        api_provider: str,
    ) -> dict[str, Any]:
        response_body: dict[str, Any] = {
            "object": chunk_response.get("object") or "list",
            "data": [{**item_response, "index": 0}],
            "usage": dict(usage),
            "_provider": api_provider,
        }
        if chunk_response.get("model") is not None:
            response_body["model"] = chunk_response.get("model")
        return response_body

    def _validate_embedding_microbatch_response(
        self,
        *,
        response_body: dict[str, Any],
        expected_count: int,
    ) -> list[dict[str, Any]]:
        data_rows = response_body.get("data")
        if not isinstance(data_rows, list):
            raise ValueError("microbatch response data is not a list")
        if len(data_rows) != expected_count:
            raise ValueError(
                f"microbatch response length mismatch expected={expected_count} actual={len(data_rows)}"
            )

        normalized_rows: list[dict[str, Any] | None] = [None] * expected_count
        for row_number, row in enumerate(data_rows):
            if not isinstance(row, dict):
                raise ValueError(f"microbatch response item {row_number} is not an object")
            if "embedding" not in row:
                raise ValueError(f"microbatch response item {row_number} is missing embedding")

            response_index = row.get("index")
            if type(response_index) is not int:
                raise ValueError(f"microbatch response item {row_number} has invalid index")
            if response_index < 0 or response_index >= expected_count:
                raise ValueError(
                    f"microbatch response item {row_number} index out of range index={response_index}"
                )
            if normalized_rows[response_index] is not None:
                raise ValueError(f"microbatch response contains duplicate index={response_index}")
            normalized_rows[response_index] = dict(row)

        if any(row is None for row in normalized_rows):
            raise ValueError("microbatch response is missing one or more expected indexes")

        return [row for row in normalized_rows if row is not None]

    def _build_microbatch_request(self, prepared_items: list[_PreparedEmbeddingItem]) -> EmbeddingRequest:
        if not prepared_items:
            raise ValueError("cannot build microbatch request without items")
        request_data = prepared_items[0].payload.model_dump(exclude_none=True)
        request_data["input"] = [prepared.payload.input for prepared in prepared_items]
        return EmbeddingRequest.model_validate(request_data)

    def _microbatch_group_key(self, prepared: _PreparedEmbeddingItem) -> tuple[_ExecutionSignature, str]:
        return (
            prepared.execution_signature,
            json.dumps(prepared.failover_kwargs, sort_keys=True, default=str),
        )

    async def _mark_item_failed(
        self,
        *,
        job,
        item,
        model_name: str,
        exc: Exception,
        deployment_id: str | None,
        started_at_monotonic: float,
    ) -> None:
        batch_id = job.batch_id
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
            deployment_id=deployment_id,
        )
        self._observe_item_execution_latency(
            status="error",
            latency_seconds=perf_counter() - started_at_monotonic,
            reference=item.item_id,
        )

    def _observe_item_execution_latency(self, *, status: str, latency_seconds: float, reference: str) -> None:
        try:
            observe_batch_item_execution_latency(status=status, latency_seconds=latency_seconds)
        except Exception as exc:
            logger.warning(
                "batch item latency metric publish failed reference=%s status=%s error=%s",
                reference,
                status,
                exc,
            )

    async def _renew_item_lease_once(self, item_id: str) -> bool:
        try:
            return await self.repository.renew_item_lease(
                item_id=item_id,
                worker_id=self.config.worker_id,
                lease_seconds=self.config.item_lease_seconds,
            )
        except Exception as exc:
            logger.warning(
                "batch item lease renewal before persistence failed item_id=%s error=%s",
                item_id,
                exc,
                exc_info=True,
            )
            return False

    def _build_completion_outbox_payload(
        self,
        *,
        job,
        prepared: _PreparedEmbeddingItem,
        usage: dict[str, Any],
        api_provider: str,
        billed_cost: float,
        provider_cost: float,
        api_base: str | None,
        deployment_model: str | None,
    ) -> dict[str, Any]:
        return {
            "request_id": f"batch:{job.batch_id}:{prepared.item.item_id}",
            "batch_id": job.batch_id,
            "item_id": prepared.item.item_id,
            "api_key": job.created_by_api_key,
            "user_id": job.created_by_user_id,
            "team_id": job.created_by_team_id,
            "organization_id": job.created_by_organization_id,
            "model": prepared.payload.model,
            "call_type": "embedding_batch",
            "usage": dict(usage),
            "billed_cost": billed_cost,
            "provider_cost": provider_cost,
            "api_provider": api_provider,
            "api_base": api_base,
            "deployment_model": deployment_model,
            "execution_mode": job.execution_mode,
            "completed_at": datetime.now(tz=UTC).isoformat(),
        }

    async def _persist_completion_rows_with_outbox(
        self,
        *,
        items: list[dict[str, Any]],
        item_ids: list[str],
        context_label: str,
    ) -> bool:
        for attempt in range(2):
            try:
                result = await self.repository.complete_items_with_outbox_bulk(
                    items=items,
                    worker_id=self.config.worker_id,
                )
            except Exception as exc:
                logger.warning(
                    "batch completion persistence attempt failed context=%s item_ids=%s attempt=%s error=%s",
                    context_label,
                    item_ids,
                    attempt + 1,
                    exc,
                    exc_info=True,
                )
                continue

            if result in {"completed", "already_completed"}:
                return True
            if result == "not_owned":
                logger.warning(
                    "batch completion persistence lost ownership context=%s item_ids=%s",
                    context_label,
                    item_ids,
                )
                return False

        try:
            requeued_item_ids = await self.repository.release_items_for_retry(
                item_ids=item_ids,
                worker_id=self.config.worker_id,
            )
        except Exception as exc:
            logger.warning(
                "batch completion persistence requeue failed context=%s item_ids=%s error=%s",
                context_label,
                item_ids,
                exc,
                exc_info=True,
            )
            return False

        expected_ids = set(item_ids)
        if set(requeued_item_ids) != expected_ids:
            logger.warning(
                "batch completion persistence requeue incomplete context=%s item_ids=%s requeued_item_ids=%s",
                context_label,
                item_ids,
                requeued_item_ids,
            )
            return False
        logger.warning(
            "batch completion persistence requeued context=%s item_ids=%s",
            context_label,
            item_ids,
        )
        return False

    async def _execute_prepared_item(self, job, prepared: _PreparedEmbeddingItem) -> None:
        item_heartbeat: asyncio.Task[None] | None = None
        try:
            item_heartbeat = self._start_heartbeat(
                renew=lambda: self.repository.renew_item_lease(
                    item_id=prepared.item.item_id,
                    worker_id=self.config.worker_id,
                    lease_seconds=self.config.item_lease_seconds,
                ),
                label=f"item:{prepared.item.item_id}",
            )
            data, served_deployment = await self.app.state.failover_manager.execute_with_failover(
                primary_deployment=prepared.primary_deployment,
                model_group=prepared.model_group,
                execute=lambda dep: _execute_embedding(prepared.request_shim, prepared.payload, dep),
                return_deployment=True,
                **prepared.failover_kwargs,
            )
            response_body, api_base, deployment_model = self._sanitize_embedding_response(data)
            usage_allocations = allocate_embedding_usage(response_body.get("usage"), item_weights=[1])
            usage = dict(usage_allocations[0] if usage_allocations else {})
            api_provider = resolve_provider(served_deployment.deltallm_params)
            response_body["_provider"] = api_provider
            api_base = api_base or served_deployment.deltallm_params.get("api_base")
            deployment_model = deployment_model or (str(served_deployment.deltallm_params.get("model") or "") or None)
            deployment_pricing = self._deployment_pricing(served_deployment)
            model_info = served_deployment.model_info or {}
            billed_cost = completion_cost(
                model=prepared.payload.model,
                usage=usage,
                cache_hit=False,
                custom_pricing=deployment_pricing,
                pricing_tier="batch",
                model_info=model_info,
            )
            provider_cost = completion_cost(
                model=prepared.payload.model,
                usage=usage,
                cache_hit=False,
                custom_pricing=deployment_pricing,
                pricing_tier="sync",
                model_info=model_info,
            )
            served_deployment_id = str(
                getattr(served_deployment, "deployment_id", None)
                or getattr(prepared.primary_deployment, "deployment_id", None)
                or ""
            )
            await self._record_upstream_success_runtime_hooks(
                batch_id=job.batch_id,
                deployment_id=served_deployment_id,
                usage=usage,
                    reference=prepared.item.item_id,
                )
            await self._renew_item_lease_once(prepared.item.item_id)
            if item_heartbeat is not None:
                await self._stop_heartbeat(item_heartbeat)
                item_heartbeat = None
            persisted = await self._persist_completion_rows_with_outbox(
                items=[
                    {
                        "item_id": prepared.item.item_id,
                        "response_body": response_body,
                        "usage": usage,
                        "provider_cost": provider_cost,
                        "billed_cost": billed_cost,
                        "outbox_payload": self._build_completion_outbox_payload(
                            job=job,
                            prepared=prepared,
                            usage=usage,
                            api_provider=api_provider,
                            billed_cost=billed_cost,
                            provider_cost=provider_cost,
                            api_base=api_base,
                            deployment_model=deployment_model,
                        ),
                        "outbox_max_attempts": _COMPLETION_OUTBOX_MAX_ATTEMPTS,
                    }
                ],
                item_ids=[prepared.item.item_id],
                context_label="single",
            )
            if persisted:
                self._observe_item_execution_latency(
                    status="success",
                    latency_seconds=perf_counter() - prepared.started_at_monotonic,
                    reference=prepared.item.item_id,
                )
        except Exception as exc:
            await self._mark_item_failed(
                job=job,
                item=prepared.item,
                model_name=prepared.model_name,
                exc=exc,
                deployment_id=str(getattr(prepared.primary_deployment, "deployment_id", None) or "") or None,
                started_at_monotonic=prepared.started_at_monotonic,
            )
            return
        finally:
            if item_heartbeat is not None:
                await self._stop_heartbeat(item_heartbeat)

    async def _process_item_with_prepared_override(self, job, prepared: _PreparedEmbeddingItem) -> None:
        self._prepared_item_overrides[prepared.item.item_id] = prepared
        try:
            await self._process_item(job, prepared.item)
        finally:
            self._prepared_item_overrides.pop(prepared.item.item_id, None)

    async def _execute_prepared_microbatch_chunk(
        self,
        job,
        prepared_items: list[_PreparedEmbeddingItem],
    ) -> None:
        if len(prepared_items) <= 1:
            await self._execute_prepared_item(job, prepared_items[0])
            return

        batch_id = job.batch_id
        chunk_size = len(prepared_items)
        first_item = prepared_items[0]
        item_ids = [prepared.item.item_id for prepared in prepared_items]
        item_heartbeats: dict[str, asyncio.Task[None]] = {}
        served_deployment = None

        try:
            for prepared in prepared_items:
                item_heartbeats[prepared.item.item_id] = self._start_heartbeat(
                    renew=lambda item_id=prepared.item.item_id: self.repository.renew_item_lease(
                        item_id=item_id,
                        worker_id=self.config.worker_id,
                        lease_seconds=self.config.item_lease_seconds,
                    ),
                    label=f"item:{prepared.item.item_id}",
                )
            chunk_payload = self._build_microbatch_request(prepared_items)
            logger.info(
                "batch embedding microbatch started batch_id=%s deployment_id=%s size=%s item_ids=%s",
                batch_id,
                getattr(first_item.primary_deployment, "deployment_id", None),
                chunk_size,
                item_ids,
            )
            increment_batch_microbatch_requests()
            increment_batch_microbatch_inputs(count=chunk_size)
            observe_batch_microbatch_size(batch_size=chunk_size)
            data, served_deployment = await self.app.state.failover_manager.execute_with_failover(
                primary_deployment=first_item.primary_deployment,
                model_group=first_item.model_group,
                execute=lambda dep: _execute_embedding(first_item.request_shim, chunk_payload, dep),
                return_deployment=True,
                **first_item.failover_kwargs,
            )
            response_body, api_base, deployment_model = self._sanitize_embedding_response(data)
            item_responses = self._validate_embedding_microbatch_response(
                response_body=response_body,
                expected_count=chunk_size,
            )
            usage_allocations = allocate_embedding_usage(
                response_body.get("usage"),
                item_weights=[prepared.microbatch_weight or 1 for prepared in prepared_items],
            )
        except Exception as exc:
            await self._record_upstream_failure_runtime_hook(
                batch_id=batch_id,
                deployment_id=str(
                    getattr(served_deployment, "deployment_id", None)
                    or getattr(first_item.primary_deployment, "deployment_id", None)
                    or ""
                )
                or None,
                exc=exc,
                reference=",".join(item_ids),
            )
            if isinstance(exc, ValueError):
                logger.warning(
                    "batch embedding microbatch response mismatch batch_id=%s deployment_id=%s size=%s error=%s",
                    batch_id,
                    getattr(served_deployment or first_item.primary_deployment, "deployment_id", None),
                    chunk_size,
                    exc,
                )
            increment_batch_microbatch_isolation_fallback()
            logger.warning(
                "batch embedding microbatch isolated batch_id=%s deployment_id=%s size=%s item_ids=%s error=%s",
                batch_id,
                getattr(served_deployment or first_item.primary_deployment, "deployment_id", None),
                chunk_size,
                item_ids,
                exc,
            )
            for prepared in prepared_items:
                item_heartbeat = item_heartbeats.pop(prepared.item.item_id, None)
                if item_heartbeat is not None:
                    await self._stop_heartbeat(item_heartbeat)
                await self._process_item(job, prepared.item)
            return

        try:
            api_provider = resolve_provider(served_deployment.deltallm_params)
            api_base = api_base or served_deployment.deltallm_params.get("api_base")
            deployment_model = deployment_model or (str(served_deployment.deltallm_params.get("model") or "") or None)
            deployment_pricing = self._deployment_pricing(served_deployment)
            model_info = served_deployment.model_info or {}
            served_deployment_id = str(
                getattr(served_deployment, "deployment_id", None)
                or getattr(first_item.primary_deployment, "deployment_id", None)
                or ""
            )
            await self._record_upstream_success_runtime_hooks(
                batch_id=batch_id,
                deployment_id=served_deployment_id,
                usage=dict(response_body.get("usage") or {}),
                reference=",".join(item_ids),
            )
            completion_rows: list[dict[str, Any]] = []
            for prepared, item_response, usage in zip(prepared_items, item_responses, usage_allocations, strict=False):
                normalized_response_body = self._build_single_item_embedding_response_body(
                    chunk_response=response_body,
                    item_response=item_response,
                    usage=usage,
                    api_provider=api_provider,
                )
                billed_cost = completion_cost(
                    model=prepared.payload.model,
                    usage=usage,
                    cache_hit=False,
                    custom_pricing=deployment_pricing,
                    pricing_tier="batch",
                    model_info=model_info,
                )
                provider_cost = completion_cost(
                    model=prepared.payload.model,
                    usage=usage,
                    cache_hit=False,
                    custom_pricing=deployment_pricing,
                    pricing_tier="sync",
                    model_info=model_info,
                )
                completion_rows.append(
                    {
                        "prepared": prepared,
                        "response_body": normalized_response_body,
                        "usage": usage,
                        "provider_cost": provider_cost,
                        "billed_cost": billed_cost,
                    }
                )

            for item_id in item_ids:
                await self._renew_item_lease_once(item_id)
            await self._stop_heartbeat_tasks(item_heartbeats.values())
            item_heartbeats.clear()

            persisted = await self._persist_completion_rows_with_outbox(
                items=[
                    {
                        "item_id": row["prepared"].item.item_id,
                        "response_body": row["response_body"],
                        "usage": row["usage"],
                        "provider_cost": row["provider_cost"],
                        "billed_cost": row["billed_cost"],
                        "outbox_payload": self._build_completion_outbox_payload(
                            job=job,
                            prepared=row["prepared"],
                            usage=row["usage"],
                            api_provider=api_provider,
                            billed_cost=row["billed_cost"],
                            provider_cost=row["provider_cost"],
                            api_base=api_base,
                            deployment_model=deployment_model,
                        ),
                        "outbox_max_attempts": _COMPLETION_OUTBOX_MAX_ATTEMPTS,
                    }
                    for row in completion_rows
                ],
                item_ids=item_ids,
                context_label=f"microbatch:{served_deployment_id or getattr(first_item.primary_deployment, 'deployment_id', None) or 'unknown'}",
            )
            if not persisted:
                return

            for row in completion_rows:
                self._observe_item_execution_latency(
                    status="success",
                    latency_seconds=perf_counter() - row["prepared"].started_at_monotonic,
                    reference=row["prepared"].item.item_id,
                )
            logger.info(
                "batch embedding microbatch succeeded batch_id=%s primary_deployment_id=%s served_deployment_id=%s size=%s item_ids=%s",
                batch_id,
                getattr(first_item.primary_deployment, "deployment_id", None),
                served_deployment_id,
                chunk_size,
                item_ids,
            )
        finally:
            await self._stop_heartbeat_tasks(item_heartbeats.values())

    async def _process_item(self, job, item) -> None:
        request_body = item.request_body if isinstance(item.request_body, dict) else {}
        model_name = str(request_body.get("model") or job.model or "")
        started_at_monotonic = perf_counter()
        prepared = self._prepared_item_overrides.get(item.item_id)
        try:
            if prepared is None:
                prepared = await self._prepare_item_for_execution(job, item)
        except Exception as exc:
            await self._mark_item_failed(
                job=job,
                item=item,
                model_name=model_name,
                exc=exc,
                deployment_id=None,
                started_at_monotonic=started_at_monotonic,
            )
            return
        await self._execute_prepared_item(job, prepared)

    async def _stop_heartbeat_tasks(self, tasks) -> None:  # noqa: ANN001
        for task in tasks:
            await self._stop_heartbeat(task)

    async def _process_items(self, job, items) -> None:
        if not items:
            set_batch_worker_saturation(worker_id=self.config.worker_id, active=0, capacity=self.config.worker_concurrency)
            return

        raw_items: deque[Any] = deque(items)
        deferred_grouped_raw_items: deque[Any] = deque()
        prepared_backlog: deque[_PreparedEmbeddingItem] = deque()
        planned_work_units: deque[Any] = deque()
        planning_lock = asyncio.Lock()
        grouped_chunk_available = asyncio.Event()
        grouped_chunk_available.set()
        grouped_chunk_in_flight = False
        grouped_chunk_blocked = object()
        active = 0

        logger.info(
            "batch embedding microbatch planner started batch_id=%s claimed_items=%s",
            job.batch_id,
            len(items),
        )

        def _requeue_prepared_candidates(candidates: list[_PreparedEmbeddingItem]) -> None:
            for candidate in reversed(candidates):
                prepared_backlog.appendleft(candidate)

        def _requeue_grouped_raw_items(candidates: list[Any]) -> None:  # noqa: ANN401
            for candidate in reversed(candidates):
                deferred_grouped_raw_items.appendleft(candidate)

        def _requeue_deferred_candidates(candidates: list[_PreparedEmbeddingItem]) -> None:
            prepared_candidates: list[_PreparedEmbeddingItem] = []
            grouped_candidates: list[Any] = []
            for candidate in candidates:
                if candidate.microbatch_eligible and candidate.effective_upstream_max_batch_inputs > 1:
                    grouped_candidates.append(candidate.item)
                else:
                    prepared_candidates.append(candidate)
            _requeue_prepared_candidates(prepared_candidates)
            _requeue_grouped_raw_items(grouped_candidates)

        def _queue_preparation_failure(item, model_name: str, exc: Exception, started_at_monotonic: float) -> None:  # noqa: ANN001
            planned_work_units.append(
                lambda item=item, model_name=model_name, exc=exc, started_at_monotonic=started_at_monotonic: self._mark_item_failed(
                    job=job,
                    item=item,
                    model_name=model_name,
                    exc=exc,
                    deployment_id=None,
                    started_at_monotonic=started_at_monotonic,
                )
            )

        async def _prepare_candidate_item(item) -> _PreparedEmbeddingItem | None:  # noqa: ANN001
            started_at_monotonic = perf_counter()
            request_body = item.request_body if isinstance(item.request_body, dict) else {}
            model_name = str(request_body.get("model") or job.model or "")
            try:
                prepared = await self._prepare_item_for_execution(job, item)
            except Exception as exc:
                _queue_preparation_failure(item, model_name, exc, started_at_monotonic)
                return None
            if not prepared.microbatch_eligible:
                increment_batch_microbatch_ineligible_item(
                    reason=prepared.microbatch_ineligible_reason or "unknown",
                )
            return prepared

        def _raw_item_looks_groupable(item) -> bool:  # noqa: ANN001
            payload = EmbeddingRequest.model_validate(item.request_body)
            _, microbatch_eligible, _ = classify_embedding_microbatch_request(payload)
            return microbatch_eligible

        async def _pop_next_candidate(*, allow_grouped_chunks: bool) -> _PreparedEmbeddingItem | object | None:
            while True:
                if allow_grouped_chunks and deferred_grouped_raw_items:
                    prepared = await _prepare_candidate_item(deferred_grouped_raw_items.popleft())
                    if prepared is None:
                        continue
                    return prepared

                if prepared_backlog:
                    return prepared_backlog.popleft()

                if not raw_items:
                    if deferred_grouped_raw_items and not allow_grouped_chunks:
                        return grouped_chunk_blocked
                    return None

                item = raw_items.popleft()
                if not allow_grouped_chunks:
                    started_at_monotonic = perf_counter()
                    request_body = item.request_body if isinstance(item.request_body, dict) else {}
                    model_name = str(request_body.get("model") or job.model or "")
                    try:
                        if _raw_item_looks_groupable(item):
                            deferred_grouped_raw_items.append(item)
                            continue
                    except Exception as exc:
                        _queue_preparation_failure(item, model_name, exc, started_at_monotonic)
                        continue
                prepared = await _prepare_candidate_item(item)
                if prepared is None:
                    continue
                grouped_chunk_candidate = (
                    prepared.microbatch_eligible and prepared.effective_upstream_max_batch_inputs > 1
                )
                if grouped_chunk_candidate and not allow_grouped_chunks:
                    deferred_grouped_raw_items.append(prepared.item)
                    continue
                return prepared

        async def _run_grouped_chunk(chunk: list[_PreparedEmbeddingItem]) -> None:
            nonlocal grouped_chunk_in_flight
            try:
                await self._execute_prepared_microbatch_chunk(job, chunk)
            finally:
                async with planning_lock:
                    grouped_chunk_in_flight = False
                    grouped_chunk_available.set()

        async def _wait_for_grouped_chunk_slot() -> None:
            await grouped_chunk_available.wait()

        async def _plan_next_work_unit():
            nonlocal grouped_chunk_in_flight
            async with planning_lock:
                if planned_work_units:
                    return planned_work_units.popleft()

                seed = await _pop_next_candidate(allow_grouped_chunks=not grouped_chunk_in_flight)
                while seed is None:
                    if planned_work_units:
                        return planned_work_units.popleft()
                    return None
                if seed is grouped_chunk_blocked:
                    return _wait_for_grouped_chunk_slot

                if not seed.microbatch_eligible or seed.effective_upstream_max_batch_inputs <= 1:
                    return lambda seed=seed: self._process_item_with_prepared_override(job, seed)

                chunk = [seed]
                chunk_key = self._microbatch_group_key(seed)
                deferred_candidates: list[_PreparedEmbeddingItem] = []

                while len(chunk) < seed.effective_upstream_max_batch_inputs:
                    candidate = await _pop_next_candidate(allow_grouped_chunks=True)
                    if candidate is None:
                        break
                    if candidate is grouped_chunk_blocked:
                        break

                    candidate_matches_chunk = (
                        candidate.microbatch_eligible
                        and candidate.effective_upstream_max_batch_inputs > 1
                        and candidate.effective_upstream_max_batch_inputs == seed.effective_upstream_max_batch_inputs
                        and self._microbatch_group_key(candidate) == chunk_key
                    )
                    if candidate_matches_chunk:
                        chunk.append(candidate)
                    else:
                        deferred_candidates.append(candidate)

                _requeue_deferred_candidates(deferred_candidates)
                grouped_chunk_in_flight = True
                grouped_chunk_available.clear()
                return lambda chunk=list(chunk): _run_grouped_chunk(chunk)

        async def _runner() -> None:
            nonlocal active
            while True:
                work_unit = await _plan_next_work_unit()
                if work_unit is None:
                    return

                active += 1
                set_batch_worker_saturation(worker_id=self.config.worker_id, active=active, capacity=self.config.worker_concurrency)
                try:
                    await work_unit()
                finally:
                    active -= 1
                    set_batch_worker_saturation(worker_id=self.config.worker_id, active=active, capacity=self.config.worker_concurrency)

        runner_count = min(max(1, self.config.worker_concurrency), len(items))
        async with asyncio.TaskGroup() as task_group:
            for _ in range(runner_count):
                task_group.create_task(_runner())

    async def _record_upstream_success_runtime_hooks(
        self,
        *,
        batch_id: str,
        deployment_id: str,
        usage: dict[str, Any],
        reference: str,
    ) -> None:
        passive_health_tracker = getattr(self.app.state, "passive_health_tracker", None)
        if passive_health_tracker is not None and deployment_id:
            try:
                await passive_health_tracker.record_request_outcome(deployment_id, success=True)
            except Exception as exc:
                logger.warning(
                    "batch passive health success hook failed batch_id=%s reference=%s deployment_id=%s error=%s",
                    batch_id,
                    reference,
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
                    "batch router usage hook failed batch_id=%s reference=%s deployment_id=%s error=%s",
                    batch_id,
                    reference,
                    deployment_id,
                    exc,
                )

    async def _record_upstream_failure_runtime_hook(
        self,
        *,
        batch_id: str,
        deployment_id: str | None,
        exc: Exception,
        reference: str,
    ) -> None:
        passive_health_tracker = getattr(self.app.state, "passive_health_tracker", None)
        if passive_health_tracker is None or deployment_id is None:
            return
        try:
            await passive_health_tracker.record_request_outcome(deployment_id, success=False, error=str(exc))
        except Exception as hook_exc:
            logger.warning(
                "batch passive health upstream failure hook failed batch_id=%s reference=%s deployment_id=%s error=%s",
                batch_id,
                reference,
                deployment_id,
                hook_exc,
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
                yield json.dumps(self._serialize_completed_artifact_row(item))

    async def _iter_error_lines(self, batch_id: str) -> AsyncIterator[str]:
        async for item in self._iter_batch_items(batch_id):
            if item.status in {"failed", "cancelled"}:
                yield json.dumps(self._serialize_failed_artifact_row(item))

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
