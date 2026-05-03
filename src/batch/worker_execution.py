from __future__ import annotations

import asyncio
from collections import deque
from datetime import UTC, datetime
import logging
from math import ceil
import random
from time import perf_counter
from typing import Any, Awaitable, Callable

from src.batch.backpressure import BatchModelGroupDeferred
from src.batch.chat_worker_execution import ChatWorkerExecutionMixin
from src.batch.embedding_worker_execution import EmbeddingWorkerExecutionMixin
from src.batch.endpoints import (
    BATCH_ENDPOINT_CHAT_COMPLETIONS,
    BATCH_ENDPOINT_EMBEDDINGS,
    batch_call_type_for_endpoint,
)
from src.batch.models import BatchItemRecord, BatchJobRecord
from src.batch.retry import (
    BatchRetryCategory,
    BatchRetryDecision,
    BatchRetryTerminalReason,
    classify_batch_retry,
)
from src.batch.repository import BatchRepository
from src.billing.cost import ModelPricing
from src.metrics import (
    increment_batch_item_retry,
    increment_batch_item_terminal_failure,
    increment_batch_model_group_deferral,
    increment_batch_model_group_deferred_items,
    observe_batch_item_retry_delay,
    observe_batch_model_group_deferral_seconds,
    set_batch_worker_saturation,
)
from src.models.errors import InvalidRequestError

from src.batch.worker_types import _PreparedChatItem, _PreparedEmbeddingItem, BatchWorkerConfig

logger = logging.getLogger(__name__)


class BatchExecutionEngine(EmbeddingWorkerExecutionMixin, ChatWorkerExecutionMixin):
    def __init__(
        self,
        *,
        app: Any,
        repository: BatchRepository,
        config: BatchWorkerConfig,
        normalize_persisted_embedding_response_body: Callable[..., dict[str, Any]],
        execute_embedding: Callable[..., Awaitable[dict[str, Any]]],
        execute_chat: Callable[..., Awaitable[tuple[dict[str, Any], float]]],
        record_router_usage: Callable[..., Awaitable[None]],
        observe_item_execution_latency: Callable[..., None],
        start_heartbeat: Callable[..., asyncio.Task[None]],
        stop_heartbeat: Callable[[asyncio.Task[None]], Awaitable[None]],
    ) -> None:
        self.app = app
        self.repository = repository
        self.config = config
        self._normalize_persisted_embedding_response_body = normalize_persisted_embedding_response_body
        self._execute_embedding = execute_embedding
        self._execute_chat = execute_chat
        self._record_router_usage = record_router_usage
        self._observe_batch_item_execution_latency = observe_item_execution_latency
        self._start_heartbeat_fn = start_heartbeat
        self._stop_heartbeat_fn = stop_heartbeat
        self._prepared_item_overrides: dict[str, _PreparedEmbeddingItem | _PreparedChatItem] = {}

    async def prepare_item_for_execution(self, job, item) -> _PreparedEmbeddingItem | _PreparedChatItem:  # noqa: ANN001
        if job.endpoint == BATCH_ENDPOINT_CHAT_COMPLETIONS:
            return await self.prepare_chat_item_for_execution(job, item)
        if job.endpoint == BATCH_ENDPOINT_EMBEDDINGS:
            return await self.prepare_embedding_item_for_execution(job, item)
        raise InvalidRequestError(message=f"Unsupported batch endpoint '{job.endpoint}'")

    def _deployment_pricing(self, deployment) -> ModelPricing | None:  # noqa: ANN001
        if deployment.input_cost_per_token or deployment.output_cost_per_token:
            return ModelPricing(
                input_cost_per_token=deployment.input_cost_per_token,
                output_cost_per_token=deployment.output_cost_per_token,
            )
        return None

    def _batch_backpressure_coordinator(self):  # noqa: ANN201
        coordinator = getattr(self.app.state, "batch_backpressure", None)
        if coordinator is None or not getattr(coordinator, "enabled", True):
            return None
        return coordinator

    def _resolve_item_model_group(self, *, item: BatchItemRecord | None, model_name: str | None) -> str | None:
        app_router = getattr(self.app.state, "router", None)
        if app_router is None:
            return None
        model = str(model_name or "").strip()
        if not model and item is not None:
            request_body = item.request_body if isinstance(item.request_body, dict) else {}
            model = str(request_body.get("model") or "").strip()
        if not model:
            return None
        try:
            return str(app_router.resolve_model_group(model))
        except Exception as exc:
            logger.debug("batch model-group backpressure model resolution skipped model=%s error=%s", model, exc)
            return None

    async def _get_model_group_deferral(self, model_group: str):  # noqa: ANN201
        coordinator = self._batch_backpressure_coordinator()
        if coordinator is None:
            return None
        try:
            return await coordinator.get_model_group_deferral(model_group)
        except Exception as exc:
            logger.warning(
                "batch model-group backpressure read failed model_group=%s error=%s",
                model_group,
                exc,
                exc_info=True,
            )
            return None

    async def _raise_if_model_group_deferred(self, model_group: str) -> None:
        deferral = await self._get_model_group_deferral(model_group)
        if deferral is None:
            return
        retry_after = max(1, int(deferral.remaining_seconds()))
        logger.info(
            "batch item deferred by model group backpressure model_group=%s reason=%s delay_seconds=%s",
            model_group,
            deferral.reason,
            retry_after,
        )
        raise BatchModelGroupDeferred(
            model_group=model_group,
            reason=deferral.reason,
            retry_after_seconds=retry_after,
        )

    def _record_model_group_deferral(self, *, reason: str, delay_seconds: int) -> None:
        try:
            increment_batch_model_group_deferral(reason=reason)
            observe_batch_model_group_deferral_seconds(reason=reason, delay_seconds=delay_seconds)
        except Exception as exc:
            logger.warning(
                "batch model-group backpressure metric publish failed reason=%s error=%s",
                reason,
                exc,
            )

    def _record_model_group_deferred_item(self, *, reason: str) -> None:
        try:
            increment_batch_model_group_deferred_items(reason=reason)
        except Exception as exc:
            logger.warning(
                "batch model-group deferred item metric publish failed reason=%s error=%s",
                reason,
                exc,
            )

    async def _maybe_defer_model_group_for_retry(
        self,
        *,
        item: BatchItemRecord | None,
        model_name: str | None,
        model_group: str | None,
        exc: Exception,
        decision: BatchRetryDecision,
        retry_delay_seconds: int,
    ) -> None:
        if decision.category is not BatchRetryCategory.NO_HEALTHY_DEPLOYMENTS:
            return
        if isinstance(exc, BatchModelGroupDeferred):
            return

        resolved_model_group = model_group or self._resolve_item_model_group(item=item, model_name=model_name)
        if not resolved_model_group:
            return

        coordinator = self._batch_backpressure_coordinator()
        if coordinator is None:
            return

        reason = decision.category.value
        try:
            deferral = await coordinator.defer_model_group(
                resolved_model_group,
                delay_seconds=retry_delay_seconds,
                reason=reason,
            )
        except Exception as defer_exc:
            logger.warning(
                "batch model-group backpressure deferral failed model_group=%s reason=%s error=%s",
                resolved_model_group,
                reason,
                defer_exc,
                exc_info=True,
            )
            return
        if deferral is None:
            return

        effective_delay_seconds = max(0, int(deferral.remaining_seconds()))
        effective_reason = str(deferral.reason or reason)
        self._record_model_group_deferral(reason=effective_reason, delay_seconds=effective_delay_seconds)
        logger.info(
            "batch model group deferred model_group=%s reason=%s delay_seconds=%s",
            deferral.model_group,
            effective_reason,
            effective_delay_seconds,
        )

    async def _mark_item_failed(
        self,
        *,
        job: BatchJobRecord,
        item: BatchItemRecord,
        model_name: str,
        exc: Exception,
        deployment_id: str | None,
        started_at_monotonic: float,
    ) -> None:
        batch_id = job.batch_id
        retry_decision = classify_batch_retry(exc)
        candidate_retry_delay = (
            self._retry_delay_seconds(item_attempts=item.attempts, decision=retry_decision)
            if retry_decision.retryable
            else 0
        )
        await self._maybe_defer_model_group_for_retry(
            item=item,
            model_name=model_name,
            model_group=None,
            exc=exc,
            decision=retry_decision,
            retry_delay_seconds=candidate_retry_delay,
        )
        retryable = self._can_retry_item(
            job=job,
            item=item,
            decision=retry_decision,
            retry_delay_seconds=candidate_retry_delay,
        )
        terminal_reason = (
            None
            if retryable
            else self._retry_terminal_reason(
                job=job,
                item=item,
                decision=retry_decision,
                retry_delay_seconds=candidate_retry_delay,
            )
        )
        retry_delay = candidate_retry_delay if retryable else 0
        error_payload = self._build_failure_error_payload(
            item=item,
            exc=exc,
            decision=retry_decision,
            retryable=retryable,
            retry_delay_seconds=retry_delay,
            terminal_reason=terminal_reason,
        )
        updated = await self.repository.mark_item_failed(
            item_id=item.item_id,
            worker_id=self.config.worker_id,
            error_body=error_payload,
            last_error=str(exc),
            retryable=retryable,
            retry_delay_seconds=retry_delay,
        )
        if not updated and retryable:
            retryable = False
            retry_delay = 0
            terminal_reason = BatchRetryTerminalReason.BATCH_EXPIRED
            error_payload = self._build_failure_error_payload(
                item=item,
                exc=exc,
                decision=retry_decision,
                retryable=retryable,
                retry_delay_seconds=retry_delay,
                terminal_reason=terminal_reason,
            )
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
        if isinstance(exc, BatchModelGroupDeferred):
            self._record_model_group_deferred_item(reason=exc.reason)
        self._record_item_failure_decision(
            batch_id=batch_id,
            item=item,
            decision=retry_decision,
            retryable=retryable,
            retry_delay_seconds=retry_delay,
            terminal_reason=terminal_reason,
        )
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
            self._observe_batch_item_execution_latency(status=status, latency_seconds=latency_seconds)
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
        prepared: _PreparedEmbeddingItem | _PreparedChatItem,
        usage: dict[str, Any],
        api_provider: str,
        billed_cost: float,
        provider_cost: float,
        api_base: str | None,
        deployment_model: str | None,
        batch_execution_mode: str | None = None,
        microbatch_size: int | None = None,
        microbatch_id: str | None = None,
    ) -> dict[str, Any]:
        payload = {
            "request_id": f"batch:{job.batch_id}:{prepared.item.item_id}",
            "batch_id": job.batch_id,
            "item_id": prepared.item.item_id,
            "api_key": job.created_by_api_key,
            "user_id": job.created_by_user_id,
            "team_id": job.created_by_team_id,
            "organization_id": job.created_by_organization_id,
            "model": prepared.payload.model,
            "call_type": batch_call_type_for_endpoint(job.endpoint),
            "usage": dict(usage),
            "billed_cost": billed_cost,
            "provider_cost": provider_cost,
            "api_provider": api_provider,
            "api_base": api_base,
            "deployment_model": deployment_model,
            "execution_mode": job.execution_mode,
            "completed_at": datetime.now(tz=UTC).isoformat(),
        }
        if batch_execution_mode is not None:
            payload["batch_execution_mode"] = batch_execution_mode
        if microbatch_size is not None:
            payload["microbatch_size"] = int(microbatch_size)
        if microbatch_id is not None:
            payload["microbatch_id"] = microbatch_id
        return payload

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

    async def _process_item_with_prepared_override(
        self,
        job,
        prepared: _PreparedEmbeddingItem | _PreparedChatItem,
        *,
        process_item: Callable[[Any, Any], Awaitable[None]],
    ) -> None:  # noqa: ANN001
        self._prepared_item_overrides[prepared.item.item_id] = prepared
        try:
            await process_item(job, prepared.item)
        finally:
            self._prepared_item_overrides.pop(prepared.item.item_id, None)

    async def process_item(
        self,
        job,
        item,
        *,
        prepare_item: Callable[[Any, Any], Awaitable[_PreparedEmbeddingItem | _PreparedChatItem]] | None = None,
    ) -> None:  # noqa: ANN001
        request_body = item.request_body if isinstance(item.request_body, dict) else {}
        model_name = str(request_body.get("model") or job.model or "")
        started_at_monotonic = perf_counter()
        prepared = self._prepared_item_overrides.get(item.item_id)
        prepare_item_fn = prepare_item or self.prepare_item_for_execution
        try:
            if prepared is None:
                prepared = await prepare_item_fn(job, item)
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
        if job.endpoint == BATCH_ENDPOINT_CHAT_COMPLETIONS:
            if not isinstance(prepared, _PreparedChatItem):
                await self._mark_item_failed(
                    job=job,
                    item=item,
                    model_name=model_name,
                    exc=InvalidRequestError(message="Prepared batch chat item has an invalid execution shape"),
                    deployment_id=None,
                    started_at_monotonic=started_at_monotonic,
                )
                return
            await self._execute_prepared_chat_item(job, prepared)
            return

        if job.endpoint != BATCH_ENDPOINT_EMBEDDINGS:
            await self._mark_item_failed(
                job=job,
                item=item,
                model_name=model_name,
                exc=InvalidRequestError(message=f"Unsupported batch endpoint '{job.endpoint}'"),
                deployment_id=None,
                started_at_monotonic=started_at_monotonic,
            )
            return

        if not isinstance(prepared, _PreparedEmbeddingItem):
            await self._mark_item_failed(
                job=job,
                item=item,
                model_name=model_name,
                exc=InvalidRequestError(message="Prepared embedding batch item has an invalid execution shape"),
                deployment_id=None,
                started_at_monotonic=started_at_monotonic,
            )
            return
        await self._execute_prepared_item(job, prepared)

    async def _stop_heartbeat_tasks(self, tasks) -> None:  # noqa: ANN001
        for task in tasks:
            await self._stop_heartbeat_fn(task)

    async def process_items(
        self,
        job,
        items,
        *,
        prepare_item: Callable[[Any, Any], Awaitable[_PreparedEmbeddingItem | _PreparedChatItem]] | None = None,
        process_item: Callable[[Any, Any], Awaitable[None]] | None = None,
    ) -> None:  # noqa: ANN001
        if not items:
            set_batch_worker_saturation(worker_id=self.config.worker_id, active=0, capacity=self.config.worker_concurrency)
            return

        prepare_item_fn = prepare_item or self.prepare_item_for_execution
        if process_item is None:
            async def process_item_fn(job, item) -> None:  # noqa: ANN001
                await self.process_item(job, item, prepare_item=prepare_item_fn)
        else:
            process_item_fn = process_item

        if job.endpoint == BATCH_ENDPOINT_CHAT_COMPLETIONS:
            await self._process_chat_items(job, items, prepare_item=prepare_item_fn)
            return

        if job.endpoint != BATCH_ENDPOINT_EMBEDDINGS:
            raw_items: deque[Any] = deque(items)
            queue_lock = asyncio.Lock()
            active = 0
            logger.info(
                "batch non-microbatch item processing started batch_id=%s endpoint=%s claimed_items=%s",
                job.batch_id,
                job.endpoint,
                len(items),
            )

            async def _runner() -> None:
                nonlocal active
                while True:
                    async with queue_lock:
                        if not raw_items:
                            return
                        item = raw_items.popleft()

                    active += 1
                    set_batch_worker_saturation(
                        worker_id=self.config.worker_id,
                        active=active,
                        capacity=self.config.worker_concurrency,
                    )
                    try:
                        await process_item_fn(job, item)
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
            return

        await self._process_embedding_items(
            job,
            items,
            prepare_item=prepare_item_fn,
            process_item=process_item_fn,
        )

    async def _record_upstream_success_runtime_hooks(
        self,
        *,
        batch_id: str,
        deployment_id: str,
        mode: str,
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
                await self._record_router_usage(
                    router_state_backend,
                    deployment_id,
                    mode=mode,
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
            await passive_health_tracker.record_request_outcome(
                deployment_id,
                success=False,
                error=str(exc),
                exc=exc,
            )
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
                await passive_health_tracker.record_request_outcome(
                    deployment_id,
                    success=False,
                    error=str(exc),
                    exc=exc,
                )
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
                    call_type=batch_call_type_for_endpoint(job.endpoint),
                    metadata={
                        "batch_id": batch_id,
                        "batch_item_id": item.item_id,
                        "custom_id": item.custom_id,
                        "endpoint": job.endpoint,
                    },
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

    def _build_failure_error_payload(
        self,
        *,
        item: BatchItemRecord,
        exc: Exception,
        decision: BatchRetryDecision,
        retryable: bool,
        retry_delay_seconds: int,
        terminal_reason: BatchRetryTerminalReason | None,
    ) -> dict[str, Any]:
        error_payload: dict[str, Any] = {
            "message": str(exc),
            "type": exc.__class__.__name__,
            "retryable": retryable,
            "retry_category": decision.category.value,
            "attempt": int(item.attempts),
            "max_attempts": int(self.config.max_attempts),
        }
        if retryable:
            error_payload["retry_delay_seconds"] = int(retry_delay_seconds)
        else:
            error_payload["terminal_reason"] = (
                terminal_reason or BatchRetryTerminalReason.NOT_RETRYABLE
            ).value
        return error_payload

    def _retry_terminal_reason(
        self,
        *,
        job: BatchJobRecord,
        item: BatchItemRecord,
        decision: BatchRetryDecision,
        retry_delay_seconds: int,
    ) -> BatchRetryTerminalReason | None:
        if not decision.retryable:
            return decision.terminal_reason or BatchRetryTerminalReason.NOT_RETRYABLE
        if item.attempts >= self.config.max_attempts:
            return BatchRetryTerminalReason.ATTEMPTS_EXHAUSTED
        return None

    def _can_retry_item(
        self,
        *,
        job: BatchJobRecord,
        item: BatchItemRecord,
        decision: BatchRetryDecision,
        retry_delay_seconds: int,
    ) -> bool:
        return (
            self._retry_terminal_reason(
                job=job,
                item=item,
                decision=decision,
                retry_delay_seconds=retry_delay_seconds,
            )
            is None
        )

    def _record_item_failure_decision(
        self,
        *,
        batch_id: str,
        item: BatchItemRecord,
        decision: BatchRetryDecision,
        retryable: bool,
        retry_delay_seconds: int,
        terminal_reason: BatchRetryTerminalReason | None,
    ) -> None:
        category = decision.category.value
        if retryable:
            try:
                increment_batch_item_retry(category=category)
                observe_batch_item_retry_delay(category=category, delay_seconds=retry_delay_seconds)
            except Exception as exc:
                logger.warning(
                    "batch item retry metric publish failed batch_id=%s item_id=%s category=%s error=%s",
                    batch_id,
                    item.item_id,
                    category,
                    exc,
                )
            logger.info(
                "batch item retry scheduled batch_id=%s item_id=%s category=%s attempt=%s max_attempts=%s delay_seconds=%s",
                batch_id,
                item.item_id,
                category,
                item.attempts,
                self.config.max_attempts,
                retry_delay_seconds,
            )
            return

        reason = (terminal_reason or BatchRetryTerminalReason.NOT_RETRYABLE).value
        try:
            increment_batch_item_terminal_failure(category=category, reason=reason)
        except Exception as exc:
            logger.warning(
                "batch item terminal failure metric publish failed batch_id=%s item_id=%s category=%s reason=%s error=%s",
                batch_id,
                item.item_id,
                category,
                reason,
                exc,
            )
        log_failure = logger.warning if decision.category is BatchRetryCategory.UNKNOWN else logger.info
        log_failure(
            "batch item terminal failure batch_id=%s item_id=%s category=%s reason=%s attempt=%s max_attempts=%s",
            batch_id,
            item.item_id,
            category,
            reason,
            item.attempts,
            self.config.max_attempts,
        )

    def _retry_delay_seconds(self, *, item_attempts: int, decision: BatchRetryDecision) -> int:
        initial = max(1, int(self.config.retry_initial_seconds))
        max_delay = max(initial, int(self.config.retry_max_seconds))
        multiplier = max(1.0, float(self.config.retry_multiplier))
        retry_after = decision.retry_after_seconds
        provider_delay = min(max_delay, retry_after) if retry_after is not None else None

        exponent = max(0, item_attempts - 1)
        backoff_delay = min(max_delay, ceil(initial * (multiplier**exponent)))
        target_delay = max(backoff_delay, provider_delay or 0)
        if not self.config.retry_jitter or target_delay <= 1:
            return target_delay

        lower_bound = max(1, provider_delay or 0, ceil(target_delay / 2))
        return random.randint(lower_bound, target_delay)
