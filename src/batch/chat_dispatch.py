from __future__ import annotations

import asyncio
from collections import deque
import logging
from time import perf_counter
from typing import Any, Awaitable, Callable

from src.batch.chat_batching import (
    classify_chat_microbatch_request,
    resolve_chat_batching_settings,
)
from src.batch.chat_capacity import bind_chat_capacity
from src.batch.chat_lease_lifecycle import ChatItemLeaseWatch, ClaimedChatAttemptCapacity
from src.router.attempt_capacity import attempt_capacity, bind_attempt_capacity
from src.router.execution import RequestDeadline
from src.batch.worker_types import (
    _PreparedChatItem,
    _PreparedEmbeddingItem,
)
from src.metrics import (
    increment_batch_chat_microbatch_fallback,
    set_batch_worker_saturation,
)
from src.models.errors import InvalidRequestError

logger = logging.getLogger(__name__)


class ChatDispatchMixin:
    async def _execute_chat_microbatch_fallback_items(
        self,
        job,
        prepared_items: list[_PreparedChatItem],
        *,
        watches: dict[str, ChatItemLeaseWatch],
        deadline: RequestDeadline,
    ) -> None:
        # The chunk retains queued watches and caller leases; one pipeline at a time.
        for prepared in prepared_items:
            watch = watches[prepared.item.item_id]
            if watch.lost.is_set():
                return
            capacity = attempt_capacity(prepared.request_context)
            if capacity is None:
                raise RuntimeError("Missing Batch split capacity")
            bind_attempt_capacity(
                prepared.request_context,
                ClaimedChatAttemptCapacity(
                    capacity,
                    watch,
                    lambda expires_at, item=prepared.item: self.repository.renew_item_lease(
                        item_id=item.item_id,
                        worker_id=self.config.worker_id,
                        claim_epoch=item.claim_epoch,
                        lease_seconds=self.config.item_lease_seconds,
                        expires_at=expires_at,
                    ),
                ),
            )
            await self._execute_prepared_chat_item(
                job,
                prepared,
                batch_execution_mode="sync_microbatch_fallback",
                lease_watch=watch,
                request_deadline=deadline,
            )

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
                        raise InvalidRequestError(
                            message="Prepared batch chat item has an invalid execution shape"
                        )
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

        bind_chat_capacity(prepared_items, worker_concurrency=self.config.worker_concurrency)
        work_units: deque[Callable[[], Awaitable[None]]] = deque()
        by_deployment: dict[tuple[str, str], list[_PreparedChatItem]] = {}

        def _queue_single(prepared: _PreparedChatItem, *, mode: str) -> None:
            work_units.append(
                lambda prepared=prepared, mode=mode: self._execute_prepared_chat_item(
                    job, prepared, batch_execution_mode=mode
                )
            )

        for prepared in prepared_items:
            if prepared.selector is not None:
                # Includes normal/context/content-policy fallback reachability:
                # grouped failover must never share one item's selector context.
                increment_batch_chat_microbatch_fallback(reason="selector_per_item")
                _queue_single(prepared, mode="concurrent")
            else:
                by_deployment.setdefault(self._chat_deployment_key(prepared), []).append(prepared)

        for deployment_items in by_deployment.values():
            settings = resolve_chat_batching_settings(
                deployment_items[0].primary_deployment.deltallm_params
            )
            if settings.mode in {"disabled", "concurrent"}:
                for prepared in deployment_items:
                    _queue_single(prepared, mode=settings.mode)
                continue

            executor = self._resolve_chat_microbatch_executor(deployment_items[0])
            if executor is None:
                increment_batch_chat_microbatch_fallback(
                    reason="executor_unavailable",
                    count=len(deployment_items),
                )
                for prepared in deployment_items:
                    _queue_single(prepared, mode="sync_microbatch_fallback")
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
                    increment_batch_chat_microbatch_fallback(
                        reason=eligibility.reason or "ineligible"
                    )
                    _queue_single(prepared, mode="sync_microbatch_fallback")
                    continue
                grouped_candidates.setdefault(eligibility.group_key, []).append(
                    (prepared, eligibility.input_tokens)
                )

            for candidates in grouped_candidates.values():
                chunks, fallbacks = self._split_chat_microbatch_candidates(candidates, settings)
                for prepared, reason in fallbacks:
                    increment_batch_chat_microbatch_fallback(reason=reason)
                    _queue_single(prepared, mode="sync_microbatch_fallback")
                for chunk in chunks:
                    work_units.append(
                        lambda chunk=list(chunk): self._execute_prepared_chat_microbatch_chunk(
                            job, chunk
                        )
                    )

        if not work_units:
            return

        work_lock = asyncio.Lock()
        active = 0

        async def _execution_runner() -> None:
            nonlocal active
            while True:
                async with work_lock:
                    if not work_units:
                        return
                    work_unit = work_units.popleft()

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
