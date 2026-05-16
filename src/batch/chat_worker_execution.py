from __future__ import annotations

import asyncio
from collections import deque
from datetime import UTC, datetime, timedelta
import logging
from time import perf_counter
from typing import Any, Awaitable, Callable, Sequence

from src.batch.chat_batching import (
    ChatBatchingSettings,
    ChatMicrobatchExecutor,
    classify_chat_microbatch_request,
    estimate_chat_input_tokens,
    normalize_chat_microbatch_results,
    resolve_chat_batching_settings,
)
from src.batch.endpoints import batch_call_type_for_endpoint, router_usage_mode_for_batch_endpoint
from src.batch.policy import record_batch_policy_failure, run_batch_request_preflight
from src.batch.retry import BatchResponseShapeError, BatchRetryDecision, classify_batch_retry
from src.batch.worker_constants import COMPLETION_OUTBOX_MAX_ATTEMPTS
from src.batch.worker_types import (
    BatchItemLeaseLostError,
    _PreparedChatItem,
    _PreparedEmbeddingItem,
    _RequestShim,
)
from src.billing.cost import completion_cost
from src.metrics import (
    increment_batch_chat_item_executed,
    increment_batch_chat_microbatch_fallback,
    increment_batch_chat_microbatch_request,
    observe_batch_chat_microbatch_size,
    observe_batch_chat_provider_latency,
    set_batch_worker_saturation,
)
from src.models.errors import InvalidRequestError, ServiceUnavailableError
from src.models.requests import ChatCompletionRequest, MCPToolDefinition
from src.providers.resolution import resolve_provider
from src.router.health_policy import affects_deployment_health
from src.routers.routing_decision import route_failover_kwargs

logger = logging.getLogger(__name__)

CHAT_MICROBATCH_UNSUPPORTED_CODE = "chat_microbatch_unsupported"
CHAT_MICROBATCH_UNSUPPORTED_REASON_FALLBACK = "executor_unavailable"
CHAT_MICROBATCH_UNSUPPORTED_REASONS = frozenset(
    {
        "unsupported",
        "mode=disabled",
        "mode=concurrent",
        "upstream_max_batch_size",
        "max_total_input_tokens",
        CHAT_MICROBATCH_UNSUPPORTED_REASON_FALLBACK,
    }
)


class ChatWorkerExecutionMixin:
    async def prepare_chat_item_for_execution(self, job, item) -> _PreparedChatItem:  # noqa: ANN001
        started_at_monotonic = perf_counter()
        chat_request = ChatCompletionRequest.model_validate(item.request_body)
        self._validate_batch_chat_request(chat_request)
        preflight = await run_batch_request_preflight(
            app=self.app,
            job=job,
            payload=chat_request,
            request_data=chat_request.model_dump(exclude_none=True),
            call_type="completion",
        )
        chat_request = preflight.payload
        self._validate_batch_chat_request(chat_request)

        request_context: dict[str, Any] = {
            "metadata": chat_request.metadata or {},
            "user_id": preflight.auth.user_id or preflight.auth.api_key or "batch-worker",
        }
        app_router = self.app.state.router
        model_group = app_router.resolve_model_group(chat_request.model)
        await self._raise_if_model_group_deferred(model_group)

        primary_deployment = app_router.require_deployment(
            model_group=model_group,
            deployment=await app_router.select_deployment(model_group, request_context),
        )
        return _PreparedChatItem(
            item=item,
            started_at_monotonic=started_at_monotonic,
            payload=chat_request,
            model_name=chat_request.model,
            model_group=model_group,
            primary_deployment=primary_deployment,
            request_context=request_context,
            failover_kwargs=route_failover_kwargs(request_context),
            request_shim=_RequestShim(app=self.app),
            policy_auth=preflight.auth,
        )

    def _validate_batch_chat_request(self, payload: ChatCompletionRequest) -> None:
        if payload.stream is True:
            raise InvalidRequestError(message="Chat batch requests support non-streaming requests only; stream must be false")
        if any(isinstance(tool, MCPToolDefinition) for tool in payload.tools or []):
            raise InvalidRequestError(message="MCP tools are not supported in batch chat yet")

    def _build_chat_completion_persistence_row(
        self,
        *,
        job,
        prepared: _PreparedChatItem,
        response_body: dict[str, Any],
        usage: dict[str, Any],
        served_deployment: Any,
        batch_execution_mode: str,
        microbatch_size: int | None = None,
        microbatch_id: str | None = None,
    ) -> dict[str, Any]:
        response_body = dict(response_body)
        usage = dict(usage)
        response_body["usage"] = usage
        api_provider = resolve_provider(served_deployment.deltallm_params)
        api_base = served_deployment.deltallm_params.get("api_base")
        deployment_model = str(served_deployment.deltallm_params.get("model") or "") or None
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
        return {
            "item_id": prepared.item.item_id,
            "claim_epoch": prepared.item.claim_epoch,
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
                batch_execution_mode=batch_execution_mode,
                microbatch_size=microbatch_size,
                microbatch_id=microbatch_id,
            ),
            "outbox_max_attempts": COMPLETION_OUTBOX_MAX_ATTEMPTS,
        }

    async def _execute_prepared_chat_item(
        self,
        job,
        prepared: _PreparedChatItem,
        *,
        batch_execution_mode: str = "concurrent",
    ) -> None:  # noqa: ANN001
        item_heartbeat: asyncio.Task[None] | None = None
        item_lease_lost = asyncio.Event()
        try:
            await self._acquire_prepared_policy_lease(prepared=prepared)
        except Exception as exc:
            record_batch_policy_failure(endpoint=batch_call_type_for_endpoint(job.endpoint), exc=exc)
            await self._mark_item_failed(
                job=job,
                item=prepared.item,
                model_name=prepared.model_name,
                exc=exc,
                deployment_id=None,
                started_at_monotonic=prepared.started_at_monotonic,
            )
            increment_batch_chat_item_executed(mode=batch_execution_mode, status="error")
            return

        try:
            item_heartbeat = self._start_heartbeat_fn(
                renew=lambda: self.repository.renew_item_lease(
                    item_id=prepared.item.item_id,
                    worker_id=self.config.worker_id,
                    lease_seconds=self.config.item_lease_seconds,
                    claim_epoch=prepared.item.claim_epoch,
                ),
                label=f"item:{prepared.item.item_id}",
                lease_lost_event=item_lease_lost,
            )
            (
                response_body,
                api_latency_ms,
            ), served_deployment = await self._await_with_lease_loss_cancellation(
                self.app.state.failover_manager.execute_with_failover(
                    primary_deployment=prepared.primary_deployment,
                    model_group=prepared.model_group,
                    execute=lambda dep: self._execute_chat(
                        prepared.request_shim,
                        prepared.payload,
                        dep,
                        record_usage=False,
                    ),
                    return_deployment=True,
                    **prepared.failover_kwargs,
                ),
                lease_lost_event=item_lease_lost,
                label=f"item:{prepared.item.item_id}",
            )
            response_body = dict(response_body)
            usage = dict(response_body.get("usage") or {})
            served_deployment_id = str(
                getattr(served_deployment, "deployment_id", None)
                or getattr(prepared.primary_deployment, "deployment_id", None)
                or ""
            )
            observe_batch_chat_provider_latency(
                mode=batch_execution_mode,
                status="success",
                latency_seconds=max(0.0, float(api_latency_ms or 0.0) / 1000.0),
            )
            await self._record_upstream_success_runtime_hooks(
                batch_id=job.batch_id,
                deployment_id=served_deployment_id,
                mode=router_usage_mode_for_batch_endpoint(job.endpoint),
                usage=usage,
                reference=prepared.item.item_id,
            )
            if item_lease_lost.is_set() or not await self._renew_item_lease_once(
                prepared.item.item_id,
                claim_epoch=prepared.item.claim_epoch,
            ):
                item_lease_lost.set()
                logger.warning(
                    "batch chat completion skipped after lease loss batch_id=%s item_id=%s",
                    job.batch_id,
                    prepared.item.item_id,
                )
                return
            if item_heartbeat is not None:
                await self._stop_heartbeat_fn(item_heartbeat)
                item_heartbeat = None
            persisted = await self._persist_completion_rows_with_outbox(
                items=[
                    self._build_chat_completion_persistence_row(
                        job=job,
                        prepared=prepared,
                        response_body=response_body,
                        usage=usage,
                        served_deployment=served_deployment,
                        batch_execution_mode=batch_execution_mode,
                    )
                ],
                item_ids=[prepared.item.item_id],
                context_label="chat",
            )
            if persisted:
                self._observe_item_execution_latency(
                    status="success",
                    latency_seconds=perf_counter() - prepared.started_at_monotonic,
                    reference=prepared.item.item_id,
                )
                increment_batch_chat_item_executed(mode=batch_execution_mode, status="success")
        except BatchItemLeaseLostError as exc:
            logger.warning(
                "batch chat provider call cancelled after lease loss batch_id=%s item_id=%s error=%s",
                job.batch_id,
                prepared.item.item_id,
                exc,
            )
            return
        except Exception as exc:
            observe_batch_chat_provider_latency(
                mode=batch_execution_mode,
                status="error",
                latency_seconds=perf_counter() - prepared.started_at_monotonic,
            )
            await self._mark_item_failed(
                job=job,
                item=prepared.item,
                model_name=prepared.model_name,
                exc=exc,
                deployment_id=str(getattr(prepared.primary_deployment, "deployment_id", None) or "") or None,
                started_at_monotonic=prepared.started_at_monotonic,
            )
            increment_batch_chat_item_executed(mode=batch_execution_mode, status="error")
            return
        finally:
            if item_heartbeat is not None:
                await self._stop_heartbeat_fn(item_heartbeat)
            await self._release_prepared_policy_lease(prepared)

    @staticmethod
    def _chat_deployment_key(prepared: _PreparedChatItem) -> str:
        return str(getattr(prepared.primary_deployment, "deployment_id", None) or id(prepared.primary_deployment))

    def _resolve_chat_microbatch_executor(
        self,
        prepared: _PreparedChatItem,
        *,
        deployment: Any | None = None,
    ) -> ChatMicrobatchExecutor | None:
        explicit_executor = getattr(self.app.state, "chat_microbatch_executor", None)
        if callable(getattr(explicit_executor, "execute_chat_microbatch", None)):
            return explicit_executor

        target_deployment = deployment or prepared.primary_deployment
        try:
            from src.providers.registry import resolve_chat_upstream

            upstream = resolve_chat_upstream(prepared.request_shim, target_deployment.deltallm_params)
        except Exception:
            return None
        adapter = upstream.adapter
        if callable(getattr(adapter, "execute_chat_microbatch", None)):
            return adapter
        return None

    @staticmethod
    def _chat_microbatch_unsupported_error(deployment: Any, *, reason: str = "unsupported") -> ServiceUnavailableError:
        deployment_id = str(getattr(deployment, "deployment_id", None) or "unknown")
        return ServiceUnavailableError(
            message=f"Deployment '{deployment_id}' does not support sync chat microbatch: {reason}",
            param=reason,
            code=CHAT_MICROBATCH_UNSUPPORTED_CODE,
            affects_deployment_health=False,
        )

    @staticmethod
    def _is_chat_microbatch_unsupported_error(exc: Exception) -> bool:
        return getattr(exc, "code", None) == CHAT_MICROBATCH_UNSUPPORTED_CODE

    @staticmethod
    def _chat_microbatch_unsupported_reason(exc: Exception) -> str:
        reason = str(getattr(exc, "param", None) or CHAT_MICROBATCH_UNSUPPORTED_REASON_FALLBACK)
        if reason in CHAT_MICROBATCH_UNSUPPORTED_REASONS:
            return reason
        return CHAT_MICROBATCH_UNSUPPORTED_REASON_FALLBACK

    def _resolve_chat_microbatch_capable_executor(
        self,
        prepared: _PreparedChatItem,
        *,
        deployment: Any,
        chunk_size: int,
        input_tokens: int,
    ) -> ChatMicrobatchExecutor:
        settings = resolve_chat_batching_settings(getattr(deployment, "deltallm_params", None))
        if settings.mode != "sync_microbatch":
            raise self._chat_microbatch_unsupported_error(deployment, reason=f"mode={settings.mode}")
        if settings.upstream_max_batch_size < chunk_size:
            raise self._chat_microbatch_unsupported_error(deployment, reason="upstream_max_batch_size")
        if settings.max_total_input_tokens is not None and input_tokens > settings.max_total_input_tokens:
            raise self._chat_microbatch_unsupported_error(deployment, reason="max_total_input_tokens")

        deployment_executor = self._resolve_chat_microbatch_executor(prepared, deployment=deployment)
        if deployment_executor is None:
            raise self._chat_microbatch_unsupported_error(deployment, reason="executor_unavailable")
        return deployment_executor

    @staticmethod
    def _build_chat_microbatch_request_context(
        *,
        job,
        prepared_items: list[_PreparedChatItem],
    ) -> dict[str, Any]:
        return {
            "batch_id": job.batch_id,
            "endpoint": job.endpoint,
            "items": [
                {
                    "item_id": prepared.item.item_id,
                    "custom_id": prepared.item.custom_id,
                    "line_number": prepared.item.line_number,
                }
                for prepared in prepared_items
            ],
        }

    @staticmethod
    def _split_chat_microbatch_candidates(
        candidates: list[tuple[_PreparedChatItem, int]],
        settings: ChatBatchingSettings,
    ) -> tuple[list[list[_PreparedChatItem]], list[tuple[_PreparedChatItem, str]]]:
        chunks: list[list[_PreparedChatItem]] = []
        fallbacks: list[tuple[_PreparedChatItem, str]] = []
        current_chunk: list[_PreparedChatItem] = []
        current_tokens = 0
        max_batch_size = max(2, int(settings.upstream_max_batch_size))
        token_cap = settings.max_total_input_tokens

        def flush_current() -> None:
            nonlocal current_chunk, current_tokens
            if len(current_chunk) > 1:
                chunks.append(current_chunk)
            elif current_chunk:
                fallbacks.append((current_chunk[0], "single_candidate"))
            current_chunk = []
            current_tokens = 0

        for prepared, input_tokens in candidates:
            if token_cap is not None and input_tokens > token_cap:
                flush_current()
                fallbacks.append((prepared, "token_limit"))
                continue

            exceeds_count = len(current_chunk) >= max_batch_size
            exceeds_tokens = token_cap is not None and current_chunk and current_tokens + input_tokens > token_cap
            if exceeds_count or exceeds_tokens:
                flush_current()

            current_chunk.append(prepared)
            current_tokens += input_tokens

        flush_current()
        return chunks, fallbacks

    @staticmethod
    def _chat_microbatch_metadata(error_body: Any) -> dict[str, Any]:
        if not isinstance(error_body, dict):
            return {}
        metadata = error_body.get("microbatch")
        return dict(metadata) if isinstance(metadata, dict) else {}

    @classmethod
    def _chat_microbatch_retry_count(cls, error_body: Any) -> int:
        metadata = cls._chat_microbatch_metadata(error_body)
        try:
            return max(0, int(metadata.get("retry_count") or 0))
        except (TypeError, ValueError):
            return 0

    @classmethod
    def _chat_microbatch_original_size(cls, *, error_body: Any, fallback: int) -> int:
        metadata = cls._chat_microbatch_metadata(error_body)
        try:
            return max(1, int(metadata.get("original_size") or fallback))
        except (TypeError, ValueError):
            return max(1, int(fallback))

    @staticmethod
    def _chat_microbatch_retry_fits_job_deadline(*, job, retry_delay_seconds: int) -> bool:  # noqa: ANN001
        if job.expires_at is None:
            return True
        expires_at = job.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        return datetime.now(tz=UTC) + timedelta(seconds=retry_delay_seconds) < expires_at

    @staticmethod
    def _build_chat_microbatch_retry_error_payload(
        *,
        exc: Exception,
        decision: BatchRetryDecision,
        retry_delay_seconds: int,
        retry_count: int,
        original_size: int,
        failed_size: int,
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
            },
        }

    async def _release_failed_chat_microbatch_for_retry(
        self,
        *,
        job,
        prepared_items: list[_PreparedChatItem],
        exc: Exception,
        decision: BatchRetryDecision,
    ) -> bool:
        if not decision.retryable:
            return False
        if not self.config.microbatch_retry_enabled:
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
        if not self._chat_microbatch_retry_fits_job_deadline(job=job, retry_delay_seconds=retry_delay):
            return False

        for prepared in prepared_items:
            if not self._can_retry_item(
                job=job,
                item=prepared.item,
                decision=decision,
                retry_delay_seconds=retry_delay,
            ):
                return False

        chunk_size = len(prepared_items)
        retry_count = max(
            self._chat_microbatch_retry_count(prepared.item.error_body) for prepared in prepared_items
        ) + 1
        original_size = max(
            self._chat_microbatch_original_size(error_body=prepared.item.error_body, fallback=chunk_size)
            for prepared in prepared_items
        )
        item_ids = [prepared.item.item_id for prepared in prepared_items]
        error_body = self._build_chat_microbatch_retry_error_payload(
            exc=exc,
            decision=decision,
            retry_delay_seconds=retry_delay,
            retry_count=retry_count,
            original_size=original_size,
            failed_size=chunk_size,
        )
        try:
            released_item_ids = await self.repository.release_items_for_retry(
                item_ids=item_ids,
                worker_id=self.config.worker_id,
                retry_delay_seconds=retry_delay,
                error_body=error_body,
                last_error=str(exc),
                item_claim_epochs={
                    prepared.item.item_id: prepared.item.claim_epoch
                    for prepared in prepared_items
                },
            )
        except Exception as release_exc:
            logger.warning(
                "batch chat microbatch retry release failed batch_id=%s item_ids=%s error=%s",
                job.batch_id,
                item_ids,
                release_exc,
                exc_info=True,
            )
            return False

        expected_item_ids = set(item_ids)
        if set(released_item_ids) != expected_item_ids:
            logger.warning(
                "batch chat microbatch retry release incomplete batch_id=%s item_ids=%s released_item_ids=%s",
                job.batch_id,
                item_ids,
                released_item_ids,
            )
            if not released_item_ids:
                return False
            await self.repository.refresh_job_progress(job.batch_id)
            return True

        for prepared in prepared_items:
            self._record_item_failure_decision(
                batch_id=job.batch_id,
                item=prepared.item,
                decision=decision,
                retryable=True,
                retry_delay_seconds=retry_delay,
                terminal_reason=None,
            )
        await self.repository.refresh_job_progress(job.batch_id)
        logger.info(
            "batch chat microbatch retry scheduled batch_id=%s category=%s size=%s delay_seconds=%s item_ids=%s",
            job.batch_id,
            decision.category.value,
            chunk_size,
            retry_delay,
            item_ids,
        )
        return True

    async def _execute_prepared_chat_microbatch_chunk(
        self,
        job,
        prepared_items: list[_PreparedChatItem],
        *,
        settings: ChatBatchingSettings | None = None,
    ) -> None:
        prepared_items = await self._acquire_chat_policy_leases_for_chunk(
            job=job,
            prepared_items=prepared_items,
            mode="sync_microbatch",
        )
        if not prepared_items:
            return
        if len(prepared_items) <= 1:
            await self._execute_prepared_chat_item(job, prepared_items[0], batch_execution_mode="sync_microbatch")
            return

        batch_id = job.batch_id
        chunk_size = len(prepared_items)
        first_item = prepared_items[0]
        chat_settings = settings or resolve_chat_batching_settings(first_item.primary_deployment.deltallm_params)
        item_ids = [prepared.item.item_id for prepared in prepared_items]
        item_heartbeats: dict[str, asyncio.Task[None]] = {}
        item_lease_lost = asyncio.Event()
        microbatch_id = f"{batch_id}:{item_ids[0]}:{chunk_size}"
        chunk_started_at = perf_counter()
        chunk_input_tokens = sum(estimate_chat_input_tokens(prepared.payload) for prepared in prepared_items)
        served_deployment: Any | None = None
        last_retryable_microbatch_exc: Exception | None = None
        last_retryable_microbatch_deployment_id: str | None = None
        request_context = self._build_chat_microbatch_request_context(
            job=job,
            prepared_items=prepared_items,
        )

        async def _execute_for_deployment(deployment: Any) -> Sequence[Any]:
            nonlocal last_retryable_microbatch_deployment_id, last_retryable_microbatch_exc
            deployment_executor = self._resolve_chat_microbatch_capable_executor(
                first_item,
                deployment=deployment,
                chunk_size=chunk_size,
                input_tokens=chunk_input_tokens,
            )
            try:
                return await deployment_executor.execute_chat_microbatch(
                    requests=[prepared.payload for prepared in prepared_items],
                    deployment=deployment,
                    request_context=request_context,
                )
            except Exception as exc:
                retry_decision = classify_batch_retry(exc)
                if (
                    not self._is_chat_microbatch_unsupported_error(exc)
                    and retry_decision.retryable
                    and affects_deployment_health(exc)
                ):
                    deployment_id = str(getattr(deployment, "deployment_id", None) or "")
                    last_retryable_microbatch_exc = exc
                    last_retryable_microbatch_deployment_id = deployment_id or None
                raise

        try:
            for prepared in prepared_items:
                item_heartbeats[prepared.item.item_id] = self._start_heartbeat_fn(
                    renew=lambda item_id=prepared.item.item_id,
                    claim_epoch=prepared.item.claim_epoch: self.repository.renew_item_lease(
                        item_id=item_id,
                        worker_id=self.config.worker_id,
                        lease_seconds=self.config.item_lease_seconds,
                        claim_epoch=claim_epoch,
                    ),
                    label=f"item:{prepared.item.item_id}",
                    lease_lost_event=item_lease_lost,
                )

            observe_batch_chat_microbatch_size(batch_size=chunk_size)
            raw_results, served_deployment = await self._await_with_lease_loss_cancellation(
                self.app.state.failover_manager.execute_with_failover(
                    primary_deployment=first_item.primary_deployment,
                    model_group=first_item.model_group,
                    execute=_execute_for_deployment,
                    return_deployment=True,
                    **first_item.failover_kwargs,
                ),
                lease_lost_event=item_lease_lost,
                label=f"chat_microbatch:{microbatch_id}",
            )
            normalized_results = normalize_chat_microbatch_results(
                raw_results,
                expected_count=chunk_size,
                custom_ids=[prepared.item.custom_id for prepared in prepared_items],
            )
        except BatchItemLeaseLostError as exc:
            logger.warning(
                "batch chat microbatch provider call cancelled after lease loss batch_id=%s size=%s item_ids=%s error=%s",
                batch_id,
                chunk_size,
                item_ids,
                exc,
            )
            await self._stop_heartbeat_tasks(item_heartbeats.values())
            item_heartbeats.clear()
            return
        except Exception as exc:
            await self._stop_heartbeat_tasks(item_heartbeats.values())
            item_heartbeats.clear()
            if self._is_chat_microbatch_unsupported_error(exc):
                if last_retryable_microbatch_exc is None:
                    observe_batch_chat_provider_latency(
                        mode="sync_microbatch",
                        status="fallback",
                        latency_seconds=perf_counter() - chunk_started_at,
                    )
                    increment_batch_chat_microbatch_request(status="fallback")
                    increment_batch_chat_microbatch_fallback(
                        reason=self._chat_microbatch_unsupported_reason(exc),
                        count=chunk_size,
                    )
                    await self._execute_chat_microbatch_fallback_items(
                        job,
                        prepared_items,
                        max_in_flight=chat_settings.max_in_flight,
                    )
                    return
                exc = last_retryable_microbatch_exc
                failure_deployment_id = last_retryable_microbatch_deployment_id
            else:
                failure_deployment = served_deployment or first_item.primary_deployment
                failure_deployment_id = str(getattr(failure_deployment, "deployment_id", None) or "") or None
            await self._record_upstream_failure_runtime_hook(
                batch_id=batch_id,
                deployment_id=failure_deployment_id,
                exc=exc,
                reference=microbatch_id,
            )
            retry_decision = classify_batch_retry(exc)
            requeued = await self._release_failed_chat_microbatch_for_retry(
                job=job,
                prepared_items=prepared_items,
                exc=exc,
                decision=retry_decision,
            )
            if requeued:
                observe_batch_chat_provider_latency(
                    mode="sync_microbatch",
                    status="retry",
                    latency_seconds=perf_counter() - chunk_started_at,
                )
                increment_batch_chat_microbatch_request(status="retry")
                await self._release_prepared_policy_leases(prepared_items)
                return

            observe_batch_chat_provider_latency(
                mode="sync_microbatch",
                status="error",
                latency_seconds=perf_counter() - chunk_started_at,
            )
            increment_batch_chat_microbatch_request(status="error")
            for prepared in prepared_items:
                await self._mark_item_failed(
                    job=job,
                    item=prepared.item,
                    model_name=prepared.model_name,
                    exc=exc,
                    deployment_id=None,
                    started_at_monotonic=prepared.started_at_monotonic,
                )
                increment_batch_chat_item_executed(mode="sync_microbatch", status="error")
            await self._release_prepared_policy_leases(prepared_items)
            return

        success_rows: list[dict[str, Any]] = []
        success_prepared: list[_PreparedChatItem] = []
        failure_count = 0
        served_deployment_id = str(getattr(served_deployment, "deployment_id", None) or "")

        for result in normalized_results:
            if item_lease_lost.is_set():
                logger.warning(
                    "batch chat microbatch result handling skipped after lease loss batch_id=%s size=%s item_ids=%s",
                    batch_id,
                    chunk_size,
                    item_ids,
                )
                await self._stop_heartbeat_tasks(item_heartbeats.values())
                item_heartbeats.clear()
                return
            prepared = prepared_items[result.index]
            if result.error is not None:
                failure_count += 1
                item_heartbeat = item_heartbeats.pop(prepared.item.item_id, None)
                if item_heartbeat is not None:
                    await self._stop_heartbeat_fn(item_heartbeat)
                await self._mark_item_failed(
                    job=job,
                    item=prepared.item,
                    model_name=prepared.model_name,
                    exc=result.error,
                    deployment_id=served_deployment_id or None,
                    started_at_monotonic=prepared.started_at_monotonic,
                )
                increment_batch_chat_item_executed(mode="sync_microbatch", status="error")
                continue

            if result.response_body is None or result.usage is None:
                failure_count += 1
                exc = BatchResponseShapeError("chat microbatch result is missing response body or per-item usage")
                item_heartbeat = item_heartbeats.pop(prepared.item.item_id, None)
                if item_heartbeat is not None:
                    await self._stop_heartbeat_fn(item_heartbeat)
                await self._mark_item_failed(
                    job=job,
                    item=prepared.item,
                    model_name=prepared.model_name,
                    exc=exc,
                    deployment_id=served_deployment_id or None,
                    started_at_monotonic=prepared.started_at_monotonic,
                )
                increment_batch_chat_item_executed(mode="sync_microbatch", status="error")
                continue
            success_rows.append(
                self._build_chat_completion_persistence_row(
                    job=job,
                    prepared=prepared,
                    response_body=result.response_body,
                    usage=result.usage,
                    served_deployment=served_deployment,
                    batch_execution_mode="sync_microbatch",
                    microbatch_size=chunk_size,
                    microbatch_id=microbatch_id,
                )
            )
            success_prepared.append(prepared)

        for prepared, row in zip(success_prepared, success_rows, strict=False):
            await self._record_upstream_success_runtime_hooks(
                batch_id=batch_id,
                deployment_id=served_deployment_id,
                mode=router_usage_mode_for_batch_endpoint(job.endpoint),
                usage=dict(row["usage"]),
                reference=prepared.item.item_id,
            )
            if item_lease_lost.is_set() or not await self._renew_item_lease_once(
                prepared.item.item_id,
                claim_epoch=prepared.item.claim_epoch,
            ):
                item_lease_lost.set()
                logger.warning(
                    "batch chat microbatch completion skipped after lease loss batch_id=%s item_id=%s",
                    batch_id,
                    prepared.item.item_id,
                )
                await self._stop_heartbeat_tasks(item_heartbeats.values())
                item_heartbeats.clear()
                return
            item_heartbeat = item_heartbeats.pop(prepared.item.item_id, None)
            if item_heartbeat is not None:
                await self._stop_heartbeat_fn(item_heartbeat)

        status = "success" if failure_count == 0 else "mixed" if success_rows else "error"
        observe_batch_chat_provider_latency(
            mode="sync_microbatch",
            status=status,
            latency_seconds=perf_counter() - chunk_started_at,
        )
        increment_batch_chat_microbatch_request(status=status)

        try:
            if success_rows:
                persisted = await self._persist_completion_rows_with_outbox(
                    items=success_rows,
                    item_ids=[prepared.item.item_id for prepared in success_prepared],
                    context_label=f"chat_microbatch:{served_deployment_id or 'unknown'}",
                )
                if persisted:
                    for prepared in success_prepared:
                        self._observe_item_execution_latency(
                            status="success",
                            latency_seconds=perf_counter() - prepared.started_at_monotonic,
                            reference=prepared.item.item_id,
                        )
                    increment_batch_chat_item_executed(
                        mode="sync_microbatch",
                        status="success",
                        count=len(success_rows),
                    )
        finally:
            await self._stop_heartbeat_tasks(item_heartbeats.values())
            await self._release_prepared_policy_leases(prepared_items)

    async def _acquire_chat_policy_leases_for_chunk(
        self,
        *,
        job,
        prepared_items: list[_PreparedChatItem],
        mode: str,
    ) -> list[_PreparedChatItem]:
        allowed: list[_PreparedChatItem] = []
        for prepared in prepared_items:
            try:
                await self._acquire_prepared_policy_lease(prepared=prepared)
            except Exception as exc:
                record_batch_policy_failure(endpoint=batch_call_type_for_endpoint(job.endpoint), exc=exc)
                await self._mark_item_failed(
                    job=job,
                    item=prepared.item,
                    model_name=prepared.model_name,
                    exc=exc,
                    deployment_id=None,
                    started_at_monotonic=prepared.started_at_monotonic,
                )
                increment_batch_chat_item_executed(mode=mode, status="error")
                continue
            allowed.append(prepared)
        return allowed

    async def _execute_chat_microbatch_fallback_items(
        self,
        job,
        prepared_items: list[_PreparedChatItem],
        *,
        max_in_flight: int | None = None,
    ) -> None:
        configured_limit = max_in_flight or self.config.worker_concurrency
        fallback_limit = max(1, min(int(configured_limit), self.config.worker_concurrency, len(prepared_items)))
        fallback_semaphore = asyncio.Semaphore(fallback_limit)

        async def _execute_single(prepared: _PreparedChatItem) -> None:
            async with fallback_semaphore:
                await self._execute_prepared_chat_item(
                    job,
                    prepared,
                    batch_execution_mode="sync_microbatch_fallback",
                )

        async with asyncio.TaskGroup() as task_group:
            for prepared in prepared_items:
                task_group.create_task(_execute_single(prepared))

    async def _process_chat_items(
        self,
        job,
        items,
        *,
        prepare_item: Callable[[Any, Any], Awaitable[_PreparedEmbeddingItem | _PreparedChatItem]],
    ) -> None:  # noqa: ANN001
        raw_items: deque[Any] = deque(items)
        prepared_items: list[_PreparedChatItem] = []
        prepared_lock = asyncio.Lock()
        queue_lock = asyncio.Lock()
        active = 0

        logger.info(
            "batch chat item planning started batch_id=%s claimed_items=%s",
            job.batch_id,
            len(items),
        )

        async def _prepare_runner() -> None:
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
                started_at_monotonic = perf_counter()
                request_body = item.request_body if isinstance(item.request_body, dict) else {}
                model_name = str(request_body.get("model") or job.model or "")
                try:
                    prepared = await prepare_item(job, item)
                    if not isinstance(prepared, _PreparedChatItem):
                        raise InvalidRequestError(message="Prepared batch chat item has an invalid execution shape")
                    async with prepared_lock:
                        prepared_items.append(prepared)
                except Exception as exc:
                    await self._mark_item_failed(
                        job=job,
                        item=item,
                        model_name=model_name,
                        exc=exc,
                        deployment_id=None,
                        started_at_monotonic=started_at_monotonic,
                    )
                finally:
                    active -= 1
                    set_batch_worker_saturation(
                        worker_id=self.config.worker_id,
                        active=active,
                        capacity=self.config.worker_concurrency,
                    )

        prepare_runner_count = min(max(1, self.config.worker_concurrency), len(items))
        async with asyncio.TaskGroup() as task_group:
            for _ in range(prepare_runner_count):
                task_group.create_task(_prepare_runner())

        work_units: deque[tuple[str, ChatBatchingSettings, Callable[[], Awaitable[None]]]] = deque()
        by_deployment: dict[str, list[_PreparedChatItem]] = {}
        for prepared in prepared_items:
            by_deployment.setdefault(self._chat_deployment_key(prepared), []).append(prepared)

        def _queue_single(prepared: _PreparedChatItem, settings: ChatBatchingSettings, *, mode: str) -> None:
            work_units.append(
                (
                    self._chat_deployment_key(prepared),
                    settings,
                    lambda prepared=prepared, mode=mode: self._execute_prepared_chat_item(
                        job,
                        prepared,
                        batch_execution_mode=mode,
                    ),
                )
            )

        for deployment_key, deployment_items in by_deployment.items():
            settings = resolve_chat_batching_settings(deployment_items[0].primary_deployment.deltallm_params)
            if settings.mode in {"disabled", "concurrent"}:
                for prepared in deployment_items:
                    _queue_single(prepared, settings, mode=settings.mode)
                continue

            executor = self._resolve_chat_microbatch_executor(deployment_items[0])
            if executor is None:
                increment_batch_chat_microbatch_fallback(
                    reason="executor_unavailable",
                    count=len(deployment_items),
                )
                for prepared in deployment_items:
                    _queue_single(prepared, settings, mode="sync_microbatch_fallback")
                continue

            grouped_candidates: dict[tuple[Any, ...], list[tuple[_PreparedChatItem, int]]] = {}
            for prepared in deployment_items:
                eligibility = classify_chat_microbatch_request(
                    payload=prepared.payload,
                    deployment=prepared.primary_deployment,
                    model_group=prepared.model_group,
                    failover_kwargs=prepared.failover_kwargs,
                )
                if not eligibility.eligible or eligibility.group_key is None:
                    increment_batch_chat_microbatch_fallback(reason=eligibility.reason or "ineligible")
                    _queue_single(prepared, settings, mode="sync_microbatch_fallback")
                    continue
                grouped_candidates.setdefault(eligibility.group_key, []).append((prepared, eligibility.input_tokens))

            for candidates in grouped_candidates.values():
                chunks, fallbacks = self._split_chat_microbatch_candidates(candidates, settings)
                for prepared, reason in fallbacks:
                    increment_batch_chat_microbatch_fallback(reason=reason)
                    _queue_single(prepared, settings, mode="sync_microbatch_fallback")
                for chunk in chunks:
                    work_units.append(
                        (
                            deployment_key,
                            settings,
                            lambda chunk=list(chunk): self._execute_prepared_chat_microbatch_chunk(
                                job,
                                chunk,
                                settings=settings,
                            ),
                        )
                    )

        if not work_units:
            return

        semaphores: dict[str, asyncio.Semaphore] = {}
        for deployment_key, settings, _ in work_units:
            limit = settings.max_in_flight or self.config.worker_concurrency
            semaphores.setdefault(
                deployment_key,
                asyncio.Semaphore(max(1, min(int(limit), max(1, self.config.worker_concurrency)))),
            )

        work_lock = asyncio.Lock()
        active = 0

        async def _execution_runner() -> None:
            nonlocal active
            while True:
                async with work_lock:
                    if not work_units:
                        return
                    deployment_key, _, work_unit = work_units.popleft()

                async with semaphores[deployment_key]:
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

        runner_count = min(max(1, self.config.worker_concurrency), len(work_units))
        async with asyncio.TaskGroup() as task_group:
            for _ in range(runner_count):
                task_group.create_task(_execution_runner())
