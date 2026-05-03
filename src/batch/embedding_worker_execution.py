from __future__ import annotations

import asyncio
from collections import deque
from datetime import UTC, datetime, timedelta
import json
import logging
from math import ceil
from time import perf_counter
from typing import Any, Awaitable, Callable

from src.batch.embedding_microbatch import (
    allocate_embedding_usage,
    build_embedding_execution_signature,
    classify_embedding_microbatch_request,
    estimate_embedding_microbatch_weight,
    resolve_effective_upstream_max_batch_inputs,
)
from src.batch.endpoints import router_usage_mode_for_batch_endpoint
from src.batch.retry import BatchResponseShapeError, BatchRetryCategory, BatchRetryDecision, classify_batch_retry
from src.batch.worker_constants import COMPLETION_OUTBOX_MAX_ATTEMPTS
from src.batch.worker_types import _PreparedEmbeddingItem, _RequestShim
from src.billing.cost import completion_cost
from src.metrics import (
    increment_batch_microbatch_ineligible_item,
    increment_batch_microbatch_inputs,
    increment_batch_microbatch_isolation_fallback,
    increment_batch_microbatch_requeue,
    increment_batch_microbatch_requests,
    observe_batch_microbatch_retry_delay,
    observe_batch_microbatch_size,
    set_batch_worker_saturation,
)
from src.models.requests import EmbeddingRequest
from src.providers.resolution import resolve_provider
from src.routers.routing_decision import route_failover_kwargs

logger = logging.getLogger(__name__)


class EmbeddingWorkerExecutionMixin:
    async def prepare_embedding_item_for_execution(self, job, item) -> _PreparedEmbeddingItem:  # noqa: ANN001
        started_at_monotonic = perf_counter()
        embedding_request = EmbeddingRequest.model_validate(item.request_body)
        budget_service = getattr(self.app.state, "budget_service", None)
        if budget_service is not None:
            await budget_service.check_budgets(
                api_key=job.created_by_api_key,
                user_id=job.created_by_user_id,
                team_id=job.created_by_team_id,
                organization_id=job.created_by_organization_id,
                model=embedding_request.model,
            )

        request_context: dict[str, Any] = {"metadata": {}, "user_id": "batch-worker"}
        app_router = self.app.state.router
        model_group = app_router.resolve_model_group(embedding_request.model)
        await self._raise_if_model_group_deferred(model_group)

        primary_deployment = app_router.require_deployment(
            model_group=model_group,
            deployment=await app_router.select_deployment(model_group, request_context),
        )
        input_kind, microbatch_eligible, microbatch_ineligible_reason = classify_embedding_microbatch_request(
            embedding_request
        )
        primary_deployment_id = str(getattr(primary_deployment, "deployment_id", None) or "")
        effective_upstream_max_batch_inputs = resolve_effective_upstream_max_batch_inputs(
            getattr(primary_deployment, "model_info", None)
        )
        metadata_max_inputs = self._microbatch_next_max_inputs(item.error_body)
        if metadata_max_inputs is not None:
            effective_upstream_max_batch_inputs = min(effective_upstream_max_batch_inputs, metadata_max_inputs)
        return _PreparedEmbeddingItem(
            item=item,
            started_at_monotonic=started_at_monotonic,
            payload=embedding_request,
            model_name=embedding_request.model,
            model_group=model_group,
            primary_deployment=primary_deployment,
            request_context=request_context,
            failover_kwargs=route_failover_kwargs(request_context),
            request_shim=_RequestShim(app=self.app),
            effective_upstream_max_batch_inputs=effective_upstream_max_batch_inputs,
            microbatch_eligible=microbatch_eligible,
            microbatch_ineligible_reason=microbatch_ineligible_reason,
            microbatch_weight=estimate_embedding_microbatch_weight(embedding_request) if microbatch_eligible else None,
            execution_signature=build_embedding_execution_signature(
                payload=embedding_request,
                model_group=model_group,
                primary_deployment_id=primary_deployment_id,
                input_kind=input_kind,
            ),
        )

    def _sanitize_embedding_response(self, data: dict[str, Any]) -> tuple[dict[str, Any], str | None, str | None]:
        response_body = dict(data)
        response_body.pop("_api_latency_ms", None)
        api_base = response_body.pop("_api_base", None)
        deployment_model = response_body.pop("_deployment_model", None)
        return response_body, api_base, deployment_model

    def _build_single_item_embedding_response_body(
        self,
        *,
        chunk_response: dict[str, Any],
        item_response: dict[str, Any],
        usage: dict[str, Any],
        api_provider: str,
        model_fallback: str | None,
    ) -> dict[str, Any]:
        response_body: dict[str, Any] = {
            "object": chunk_response.get("object") or "list",
            "data": [dict(item_response)],
        }
        return self._normalize_persisted_embedding_response_body(
            response_body=response_body,
            usage=usage,
            api_provider=api_provider,
            model_fallback=model_fallback,
        )

    def _validate_embedding_microbatch_response(
        self,
        *,
        response_body: dict[str, Any],
        expected_count: int,
    ) -> list[dict[str, Any]]:
        data_rows = response_body.get("data")
        if not isinstance(data_rows, list):
            raise BatchResponseShapeError("microbatch response data is not a list")
        if len(data_rows) != expected_count:
            raise BatchResponseShapeError(
                f"microbatch response length mismatch expected={expected_count} actual={len(data_rows)}"
            )

        normalized_rows: list[dict[str, Any] | None] = [None] * expected_count
        for row_number, row in enumerate(data_rows):
            if not isinstance(row, dict):
                raise BatchResponseShapeError(f"microbatch response item {row_number} is not an object")
            if "embedding" not in row:
                raise BatchResponseShapeError(f"microbatch response item {row_number} is missing embedding")

            response_index = row.get("index")
            if type(response_index) is not int:
                raise BatchResponseShapeError(f"microbatch response item {row_number} has invalid index")
            if response_index < 0 or response_index >= expected_count:
                raise BatchResponseShapeError(
                    f"microbatch response item {row_number} index out of range index={response_index}"
                )
            if normalized_rows[response_index] is not None:
                raise BatchResponseShapeError(f"microbatch response contains duplicate index={response_index}")
            normalized_rows[response_index] = dict(row)

        if any(row is None for row in normalized_rows):
            raise BatchResponseShapeError("microbatch response is missing one or more expected indexes")

        return [row for row in normalized_rows if row is not None]

    def _build_microbatch_request(self, prepared_items: list[_PreparedEmbeddingItem]):  # noqa: ANN001
        if not prepared_items:
            raise ValueError("cannot build microbatch request without items")
        request_data = prepared_items[0].payload.model_dump(exclude_none=True)
        request_data["input"] = [prepared.payload.input for prepared in prepared_items]
        return EmbeddingRequest.model_validate(request_data)

    def _microbatch_group_key(self, prepared: _PreparedEmbeddingItem) -> tuple[Any, str]:
        return (
            prepared.execution_signature,
            json.dumps(prepared.failover_kwargs, sort_keys=True, default=str),
        )

    @staticmethod
    def _microbatch_metadata(error_body: Any) -> dict[str, Any]:
        if not isinstance(error_body, dict):
            return {}
        metadata = error_body.get("microbatch")
        return dict(metadata) if isinstance(metadata, dict) else {}

    @classmethod
    def _microbatch_next_max_inputs(cls, error_body: Any) -> int | None:
        metadata = cls._microbatch_metadata(error_body)
        try:
            value = int(metadata.get("next_max_inputs"))
        except (TypeError, ValueError):
            return None
        return value if value > 0 else None

    @classmethod
    def _microbatch_retry_count(cls, error_body: Any) -> int:
        metadata = cls._microbatch_metadata(error_body)
        try:
            return max(0, int(metadata.get("retry_count") or 0))
        except (TypeError, ValueError):
            return 0

    @classmethod
    def _microbatch_original_size(cls, *, error_body: Any, fallback: int) -> int:
        metadata = cls._microbatch_metadata(error_body)
        try:
            return max(1, int(metadata.get("original_size") or fallback))
        except (TypeError, ValueError):
            return max(1, int(fallback))

    def _next_reduced_microbatch_size(self, chunk_size: int) -> int | None:
        min_size = max(1, int(self.config.microbatch_min_reduced_size))
        factor = min(1.0, max(0.0, float(self.config.microbatch_reduce_factor)))
        reduced_size = max(min_size, ceil(chunk_size * factor))
        if reduced_size >= chunk_size:
            return None
        return reduced_size

    def _build_microbatch_retry_error_payload(
        self,
        *,
        exc: Exception,
        decision: BatchRetryDecision,
        retry_delay_seconds: int,
        retry_count: int,
        original_size: int,
        failed_size: int,
        next_max_inputs: int,
        result: str,
    ) -> dict[str, Any]:
        return {
            "message": str(exc),
            "type": exc.__class__.__name__,
            "retryable": True,
            "retry_category": decision.category.value,
            "retry_delay_seconds": int(retry_delay_seconds),
            "microbatch": {
                "retry_count": int(retry_count),
                "original_size": int(original_size),
                "failed_size": int(failed_size),
                "next_max_inputs": int(next_max_inputs),
                "last_result": result,
            },
        }

    def _record_microbatch_requeue(
        self,
        *,
        category: BatchRetryCategory,
        result: str,
        retry_delay_seconds: int | None = None,
    ) -> None:
        category_value = category.value
        try:
            increment_batch_microbatch_requeue(category=category_value, result=result)
            if retry_delay_seconds is not None and result in {"scheduled", "reduced"}:
                observe_batch_microbatch_retry_delay(category=category_value, delay_seconds=retry_delay_seconds)
        except Exception as exc:
            logger.warning(
                "batch embedding microbatch retry metric publish failed category=%s result=%s error=%s",
                category_value,
                result,
                exc,
            )

    def _microbatch_retry_fits_job_deadline(self, *, job, retry_delay_seconds: int) -> bool:  # noqa: ANN001
        if job.expires_at is None:
            return True
        expires_at = job.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        return datetime.now(tz=UTC) + timedelta(seconds=retry_delay_seconds) < expires_at

    async def _release_failed_microbatch_for_retry(
        self,
        *,
        job,
        prepared_items: list[_PreparedEmbeddingItem],
        item_heartbeats: dict[str, asyncio.Task[None]],
        exc: Exception,
        decision: BatchRetryDecision,
    ) -> bool:
        if not decision.retryable:
            return False

        if not self.config.microbatch_retry_enabled:
            self._record_microbatch_requeue(category=decision.category, result="disabled")
            return False

        retry_delay = self._retry_delay_seconds(
            item_attempts=max((prepared.item.attempts for prepared in prepared_items), default=0),
            decision=decision,
        )
        await self._maybe_defer_model_group_for_retry(
            item=None,
            model_name=None,
            model_group=prepared_items[0].model_group if prepared_items else None,
            exc=exc,
            decision=decision,
            retry_delay_seconds=retry_delay,
        )
        if not self._microbatch_retry_fits_job_deadline(job=job, retry_delay_seconds=retry_delay):
            self._record_microbatch_requeue(category=decision.category, result="exhausted")
            return False

        for prepared in prepared_items:
            if not self._can_retry_item(
                job=job,
                item=prepared.item,
                decision=decision,
                retry_delay_seconds=retry_delay,
            ):
                self._record_microbatch_requeue(category=decision.category, result="exhausted")
                return False

        chunk_size = len(prepared_items)
        retry_count = max(
            self._microbatch_retry_count(prepared.item.error_body) for prepared in prepared_items
        ) + 1
        original_size = max(
            self._microbatch_original_size(error_body=prepared.item.error_body, fallback=chunk_size)
            for prepared in prepared_items
        )
        next_max_inputs = chunk_size
        result = "scheduled"

        if retry_count > max(0, int(self.config.microbatch_max_group_retries)):
            reduced_size = self._next_reduced_microbatch_size(chunk_size)
            if reduced_size is None:
                self._record_microbatch_requeue(category=decision.category, result="exhausted")
                return False
            next_max_inputs = reduced_size
            result = "reduced"

        item_ids = [prepared.item.item_id for prepared in prepared_items]
        error_body = self._build_microbatch_retry_error_payload(
            exc=exc,
            decision=decision,
            retry_delay_seconds=retry_delay,
            retry_count=retry_count,
            original_size=original_size,
            failed_size=chunk_size,
            next_max_inputs=next_max_inputs,
            result=result,
        )
        try:
            released_item_ids = await self.repository.release_items_for_retry(
                item_ids=item_ids,
                worker_id=self.config.worker_id,
                retry_delay_seconds=retry_delay,
                error_body=error_body,
                last_error=str(exc),
            )
        except Exception as release_exc:
            self._record_microbatch_requeue(category=decision.category, result="partial_release")
            logger.warning(
                "batch embedding microbatch retry release failed batch_id=%s item_ids=%s error=%s",
                job.batch_id,
                item_ids,
                release_exc,
                exc_info=True,
            )
            return False

        expected_item_ids = set(item_ids)
        if set(released_item_ids) != expected_item_ids:
            self._record_microbatch_requeue(category=decision.category, result="partial_release")
            logger.warning(
                "batch embedding microbatch retry release incomplete batch_id=%s item_ids=%s released_item_ids=%s",
                job.batch_id,
                item_ids,
                released_item_ids,
            )
            if released_item_ids:
                await self._stop_heartbeat_tasks(item_heartbeats.values())
                item_heartbeats.clear()
                await self.repository.refresh_job_progress(job.batch_id)
                return True
            return False

        await self._stop_heartbeat_tasks(item_heartbeats.values())
        item_heartbeats.clear()
        self._record_microbatch_requeue(
            category=decision.category,
            result=result,
            retry_delay_seconds=retry_delay,
        )
        await self.repository.refresh_job_progress(job.batch_id)
        logger.info(
            "batch embedding microbatch retry scheduled batch_id=%s category=%s result=%s size=%s next_max_inputs=%s delay_seconds=%s item_ids=%s",
            job.batch_id,
            decision.category.value,
            result,
            chunk_size,
            next_max_inputs,
            retry_delay,
            item_ids,
        )
        return True

    async def _execute_prepared_item(self, job, prepared: _PreparedEmbeddingItem) -> None:  # noqa: ANN001
        item_heartbeat: asyncio.Task[None] | None = None
        try:
            item_heartbeat = self._start_heartbeat_fn(
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
                execute=lambda dep: self._execute_embedding(prepared.request_shim, prepared.payload, dep),
                return_deployment=True,
                **prepared.failover_kwargs,
            )
            response_body, api_base, deployment_model = self._sanitize_embedding_response(data)
            usage_allocations = allocate_embedding_usage(response_body.get("usage"), item_weights=[1])
            usage = dict(usage_allocations[0] if usage_allocations else {})
            api_provider = resolve_provider(served_deployment.deltallm_params)
            api_base = api_base or served_deployment.deltallm_params.get("api_base")
            deployment_model = deployment_model or (str(served_deployment.deltallm_params.get("model") or "") or None)
            response_body = self._normalize_persisted_embedding_response_body(
                response_body=response_body,
                usage=usage,
                api_provider=api_provider,
                model_fallback=deployment_model,
            )
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
                mode=router_usage_mode_for_batch_endpoint(job.endpoint),
                usage=usage,
                reference=prepared.item.item_id,
            )
            await self._renew_item_lease_once(prepared.item.item_id)
            if item_heartbeat is not None:
                await self._stop_heartbeat_fn(item_heartbeat)
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
                        "outbox_max_attempts": COMPLETION_OUTBOX_MAX_ATTEMPTS,
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
                await self._stop_heartbeat_fn(item_heartbeat)

    async def _execute_prepared_microbatch_chunk(
        self,
        job,
        prepared_items: list[_PreparedEmbeddingItem],
        *,
        process_item: Callable[[Any, Any], Awaitable[None]],
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
                item_heartbeats[prepared.item.item_id] = self._start_heartbeat_fn(
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
                execute=lambda dep: self._execute_embedding(first_item.request_shim, chunk_payload, dep),
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
            if isinstance(exc, BatchResponseShapeError):
                logger.warning(
                    "batch embedding microbatch response mismatch batch_id=%s deployment_id=%s size=%s error=%s",
                    batch_id,
                    getattr(served_deployment or first_item.primary_deployment, "deployment_id", None),
                    chunk_size,
                    exc,
                )
            else:
                retry_decision = classify_batch_retry(exc)
                requeued = await self._release_failed_microbatch_for_retry(
                    job=job,
                    prepared_items=prepared_items,
                    item_heartbeats=item_heartbeats,
                    exc=exc,
                    decision=retry_decision,
                )
                if requeued:
                    return
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
                    await self._stop_heartbeat_fn(item_heartbeat)
                await process_item(job, prepared.item)
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
                mode=router_usage_mode_for_batch_endpoint(job.endpoint),
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
                    model_fallback=deployment_model,
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
                        "outbox_max_attempts": COMPLETION_OUTBOX_MAX_ATTEMPTS,
                    }
                    for row in completion_rows
                ],
                item_ids=item_ids,
                context_label=(
                    f"microbatch:{served_deployment_id or getattr(first_item.primary_deployment, 'deployment_id', None) or 'unknown'}"
                ),
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

    async def _process_embedding_items(
        self,
        job,
        items,
        *,
        prepare_item: Callable[[Any, Any], Awaitable[Any]],
        process_item: Callable[[Any, Any], Awaitable[None]],
    ) -> None:  # noqa: ANN001
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

        def _requeue_grouped_raw_items(candidates: list[Any]) -> None:
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

        def _queue_preparation_failure(item, model_name: str, exc: Exception, started_at_monotonic: float) -> None:
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
                prepared = await prepare_item(job, item)
            except Exception as exc:
                _queue_preparation_failure(item, model_name, exc, started_at_monotonic)
                return None
            if not prepared.microbatch_eligible:
                increment_batch_microbatch_ineligible_item(reason=prepared.microbatch_ineligible_reason or "unknown")
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
                grouped_chunk_candidate = prepared.microbatch_eligible and prepared.effective_upstream_max_batch_inputs > 1
                if grouped_chunk_candidate and not allow_grouped_chunks:
                    deferred_grouped_raw_items.append(prepared.item)
                    continue
                return prepared

        async def _run_grouped_chunk(chunk: list[_PreparedEmbeddingItem]) -> None:
            nonlocal grouped_chunk_in_flight
            try:
                await self._execute_prepared_microbatch_chunk(job, chunk, process_item=process_item)
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
                    return lambda seed=seed: self._process_item_with_prepared_override(
                        job,
                        seed,
                        process_item=process_item,
                    )

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
                set_batch_worker_saturation(
                    worker_id=self.config.worker_id,
                    active=active,
                    capacity=self.config.worker_concurrency,
                )
                try:
                    await work_unit()
                finally:
                    active -= 1
                    set_batch_worker_saturation(
                        worker_id=self.config.worker_id,
                        active=active,
                        capacity=self.config.worker_concurrency,
                    )

        runner_count = min(max(1, self.config.worker_concurrency), len(items))
        async with asyncio.TaskGroup() as task_group:
            for _ in range(runner_count):
                task_group.create_task(_runner())
