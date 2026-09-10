from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from datetime import UTC, datetime, timedelta
import logging
from time import perf_counter
from typing import Any, Sequence
from src.batch.chat_item_execution import ChatItemExecutionMixin
from src.batch.chat_dispatch import ChatDispatchMixin
from src.batch.chat_capacity import bind_chat_capacity
from src.batch.chat_lease_lifecycle import ChatItemLeaseWatch, stop_chat_watches
from src.router.attempt_capacity import attempt_capacity, bind_attempt_capacity

from src.batch.chat_batching import (
    ChatBatchingSettings,
    ChatMicrobatchExecutor,
    estimate_chat_input_tokens,
    normalize_chat_microbatch_results,
    resolve_chat_batching_settings,
)
from src.batch.endpoints import batch_call_type_for_endpoint, router_usage_mode_for_batch_endpoint
from src.batch.error_sanitization import persisted_batch_error_message
from src.batch.policy import record_batch_policy_failure
from src.batch.retry import BatchResponseShapeError, BatchRetryDecision, classify_batch_retry
from src.batch.worker_types import (
    BatchItemLeaseLostError,
    _PreparedChatItem,
    routing_generation_batch_key,
)
from src.metrics import (
    increment_batch_chat_item_executed,
    increment_batch_chat_microbatch_fallback,
    increment_batch_chat_microbatch_request,
    observe_batch_chat_microbatch_size,
    observe_batch_chat_provider_latency,
)
from src.models.errors import ProxyError, ServiceUnavailableError
from src.providers.base import map_standard_provider_error, sanitize_provider_proxy_error
from src.providers.resolution import resolve_provider
from src.router import (
    ProviderAttemptResult,
)
from src.router.context_policy import (
    build_combined_request_context,
)
from src.router.execution import (
    RequestDeadline,
    attach_failover_attempt_context,
    get_failover_attempt_context,
)
from src.router.health_policy import affects_deployment_health

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


class ChatWorkerExecutionMixin(ChatItemExecutionMixin, ChatDispatchMixin):
    def _sanitize_chat_microbatch_executor_error(
        self, deployment: Any, exc: Exception
    ) -> ProxyError:
        if self._is_chat_microbatch_unsupported_error(exc):
            return self._chat_microbatch_unsupported_error(
                deployment,
                reason=self._chat_microbatch_unsupported_reason(exc),
            )
        if isinstance(exc, ProxyError):
            return sanitize_provider_proxy_error(exc)
        registry = getattr(self.app.state, "provider_error_mapper_registry", None)
        if registry is not None:
            mapped_error = registry.map_error(resolve_provider(deployment.deltallm_params), exc)
        else:
            mapped_error = map_standard_provider_error(exc)
        return sanitize_provider_proxy_error(mapped_error)

    @staticmethod
    def _chat_deployment_key(prepared: _PreparedChatItem) -> tuple[str, str]:
        return (
            routing_generation_batch_key(prepared.routing_generation),
            str(
                getattr(prepared.primary_deployment, "deployment_id", None)
                or id(prepared.primary_deployment)
            ),
        )

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

            upstream = resolve_chat_upstream(
                prepared.request_shim, target_deployment.deltallm_params
            )
        except Exception:
            return None
        adapter = upstream.adapter
        if callable(getattr(adapter, "execute_chat_microbatch", None)):
            return adapter
        return None

    @staticmethod
    def _chat_microbatch_unsupported_error(
        deployment: Any, *, reason: str = "unsupported"
    ) -> ServiceUnavailableError:
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
            raise self._chat_microbatch_unsupported_error(
                deployment, reason=f"mode={settings.mode}"
            )
        if settings.upstream_max_batch_size < chunk_size:
            raise self._chat_microbatch_unsupported_error(
                deployment, reason="upstream_max_batch_size"
            )
        if (
            settings.max_total_input_tokens is not None
            and input_tokens > settings.max_total_input_tokens
        ):
            raise self._chat_microbatch_unsupported_error(
                deployment, reason="max_total_input_tokens"
            )

        deployment_executor = self._resolve_chat_microbatch_executor(
            prepared, deployment=deployment
        )
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
            exceeds_tokens = (
                token_cap is not None
                and current_chunk
                and current_tokens + input_tokens > token_cap
            )
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
            "message": persisted_batch_error_message(exc, decision),
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
        if not self._chat_microbatch_retry_fits_job_deadline(
            job=job, retry_delay_seconds=retry_delay
        ):
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
        retry_count = (
            max(
                self._chat_microbatch_retry_count(prepared.item.error_body)
                for prepared in prepared_items
            )
            + 1
        )
        original_size = max(
            self._chat_microbatch_original_size(
                error_body=prepared.item.error_body, fallback=chunk_size
            )
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
        safe_error_message = persisted_batch_error_message(exc, decision)
        try:
            released_item_ids = await self.repository.release_items_for_retry(
                item_ids=item_ids,
                worker_id=self.config.worker_id,
                retry_delay_seconds=retry_delay,
                error_body=error_body,
                last_error=safe_error_message,
                item_claim_epochs={
                    prepared.item.item_id: prepared.item.claim_epoch for prepared in prepared_items
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
    ) -> None:
        async with AsyncExitStack() as cleanup:
            for prepared in prepared_items:
                cleanup.push_async_callback(self._release_owned_chat_policy_lease, prepared)
            await self._execute_owned_chat_microbatch_chunk(job, prepared_items, cleanup)

    async def _execute_owned_chat_microbatch_chunk(
        self, job, prepared_items: list[_PreparedChatItem], cleanup: AsyncExitStack
    ) -> None:
        capacity = attempt_capacity(prepared_items[0].request_context) if prepared_items else None
        if capacity is None:
            capacity = bind_chat_capacity(
                prepared_items, worker_concurrency=self.config.worker_concurrency
            )
        prepared_items = await self._acquire_chat_policy_leases_for_chunk(
            job=job,
            prepared_items=prepared_items,
            mode="sync_microbatch",
        )
        if not prepared_items:
            return
        if len(prepared_items) <= 1:
            await self._execute_prepared_chat_item(
                job, prepared_items[0], batch_execution_mode="sync_microbatch"
            )
            return

        batch_id = job.batch_id
        chunk_size = len(prepared_items)
        first_item = prepared_items[0]
        deadline = first_item.routing_generation.failover_manager.create_request_deadline(
            first_item.failover_kwargs.get("timeout_seconds")
        )
        if job.expires_at is not None:
            expires = (
                job.expires_at.replace(tzinfo=UTC)
                if job.expires_at.tzinfo is None
                else job.expires_at
            )
            deadline = RequestDeadline.after(
                min(deadline.remaining(), max(0.0, (expires - datetime.now(UTC)).total_seconds()))
            )
        failover_routing_context = build_combined_request_context(
            [prepared.request_context for prepared in prepared_items]
        )
        bind_attempt_capacity(failover_routing_context, capacity)
        item_ids = [prepared.item.item_id for prepared in prepared_items]
        item_heartbeats: dict[str, ChatItemLeaseWatch] = {}
        item_lease_lost = asyncio.Event()
        microbatch_id = f"{batch_id}:{item_ids[0]}:{chunk_size}"
        chunk_started_at = perf_counter()
        chunk_input_tokens = sum(
            estimate_chat_input_tokens(prepared.payload) for prepared in prepared_items
        )
        served_deployment: Any | None = None
        last_retryable_microbatch_exc: Exception | None = None
        request_context = self._build_chat_microbatch_request_context(
            job=job,
            prepared_items=prepared_items,
        )

        async def _execute_for_deployment(
            deployment: Any,
        ) -> Sequence[Any] | ProviderAttemptResult[Sequence[Any]]:
            nonlocal last_retryable_microbatch_exc
            deployment_executor = self._resolve_chat_microbatch_capable_executor(
                first_item,
                deployment=deployment,
                chunk_size=chunk_size,
                input_tokens=chunk_input_tokens,
            )
            try:
                raw_results = await deployment_executor.execute_chat_microbatch(
                    requests=[prepared.payload for prepared in prepared_items],
                    deployment=deployment,
                    request_context=request_context,
                )
            except Exception as exc:
                mapped_error = self._sanitize_chat_microbatch_executor_error(deployment, exc)
                retry_decision = classify_batch_retry(mapped_error)
                if (
                    not self._is_chat_microbatch_unsupported_error(mapped_error)
                    and retry_decision.retryable
                    and affects_deployment_health(mapped_error)
                ):
                    last_retryable_microbatch_exc = mapped_error
                if mapped_error is exc:
                    raise
                raise mapped_error from exc

            normalized_results = normalize_chat_microbatch_results(
                raw_results,
                expected_count=chunk_size,
                custom_ids=[prepared.item.custom_id for prepared in prepared_items],
            )
            health_errors = [
                result.error
                for result in normalized_results
                if result.error is not None and affects_deployment_health(result.error)
            ]
            if len(health_errors) == len(normalized_results):
                last_retryable_microbatch_exc = health_errors[0]
                raise health_errors[0]
            if health_errors:
                return ProviderAttemptResult(
                    value=normalized_results,
                    health_error=health_errors[0],
                )
            return normalized_results

        try:
            for prepared in prepared_items:
                task = self._start_heartbeat_fn(
                    renew=lambda item_id=prepared.item.item_id, claim_epoch=prepared.item.claim_epoch: (
                        self.repository.renew_item_lease(
                            item_id=item_id,
                            worker_id=self.config.worker_id,
                            lease_seconds=self.config.item_lease_seconds,
                            claim_epoch=claim_epoch,
                        )
                    ),
                    label=f"item:{prepared.item.item_id}",
                    lease_lost_event=item_lease_lost,
                )

                watch = ChatItemLeaseWatch(task, item_lease_lost, self._stop_heartbeat_fn)
                item_heartbeats[prepared.item.item_id] = watch
                cleanup.push_async_callback(watch.stop)

            observe_batch_chat_microbatch_size(batch_size=chunk_size)
            normalized_results, served_deployment = await self._await_with_lease_loss_cancellation(
                first_item.routing_generation.failover_manager.execute_with_failover(
                    primary_deployment=first_item.primary_deployment,
                    model_group=first_item.model_group,
                    execute=_execute_for_deployment,
                    return_deployment=True,
                    routing_context=failover_routing_context,
                    **{**first_item.failover_kwargs, "request_deadline": deadline},
                ),
                lease_lost_event=item_lease_lost,
                label=f"chat_microbatch:{microbatch_id}",
            )
        except asyncio.CancelledError:
            await stop_chat_watches(item_heartbeats.values())
            raise
        except BatchItemLeaseLostError as exc:
            logger.warning(
                "batch chat microbatch provider call cancelled after lease loss batch_id=%s size=%s item_ids=%s error=%s",
                batch_id,
                chunk_size,
                item_ids,
                exc,
            )
            self._observe_prepared_items_lease_lost(prepared_items)
            await stop_chat_watches(item_heartbeats.values())
            item_heartbeats.clear()
            return
        except Exception as exc:
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
                        watches=item_heartbeats,
                        deadline=deadline,
                    )
                    return
                terminal_context = get_failover_attempt_context(exc)
                exc = last_retryable_microbatch_exc
                if terminal_context is not None:
                    attach_failover_attempt_context(
                        exc,
                        model_group=terminal_context.model_group,
                        attempted_deployment_ids=list(terminal_context.attempted_deployment_ids),
                    )
            await stop_chat_watches(item_heartbeats.values())
            item_heartbeats.clear()
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
                await stop_chat_watches(item_heartbeats.values())
                item_heartbeats.clear()
                return
            prepared = prepared_items[result.index]
            if result.error is not None:
                failure_count += 1
                item_heartbeat = item_heartbeats.pop(prepared.item.item_id, None)
                if item_heartbeat is not None:
                    await item_heartbeat.stop()
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
                exc = BatchResponseShapeError(
                    "chat microbatch result is missing response body or per-item usage"
                )
                item_heartbeat = item_heartbeats.pop(prepared.item.item_id, None)
                if item_heartbeat is not None:
                    await item_heartbeat.stop()
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
                self._observe_prepared_items_lease_lost(success_prepared)
                logger.warning(
                    "batch chat microbatch completion skipped after lease loss batch_id=%s item_id=%s",
                    batch_id,
                    prepared.item.item_id,
                )
                await stop_chat_watches(item_heartbeats.values())
                item_heartbeats.clear()
                return
            item_heartbeat = item_heartbeats.pop(prepared.item.item_id, None)
            if item_heartbeat is not None:
                await item_heartbeat.stop()

        status = "success" if failure_count == 0 else "mixed" if success_rows else "error"
        observe_batch_chat_provider_latency(
            mode="sync_microbatch",
            status=status,
            latency_seconds=perf_counter() - chunk_started_at,
        )
        increment_batch_chat_microbatch_request(status=status)

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
            except asyncio.CancelledError:
                await self._release_prepared_policy_leases([*allowed, prepared])
                raise
            except Exception as exc:
                await self._release_prepared_policy_lease(prepared)
                record_batch_policy_failure(
                    endpoint=batch_call_type_for_endpoint(job.endpoint), exc=exc
                )
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
