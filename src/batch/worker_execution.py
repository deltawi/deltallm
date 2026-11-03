from __future__ import annotations

import asyncio
from collections import deque
from functools import partial
import logging
from time import perf_counter
from typing import Any, Awaitable, Callable

from src.batch.chat_capacity import ChatAttemptCounts
from src.batch.chat_worker_execution import ChatWorkerExecutionMixin
from src.batch.accounting_provider_execution import AccountingProviderExecutionMixin
from src.batch.accounting_native import NativeBatchBilling
from src.batch.embedding_worker_execution import EmbeddingWorkerExecutionMixin
from src.batch.embedding_group_lifecycle import EmbeddingGroupLifecycleMixin
from src.batch.execution_slots import BatchExecutionSlots
from src.batch.endpoints import BATCH_ENDPOINT_CHAT_COMPLETIONS, BATCH_ENDPOINT_EMBEDDINGS
from src.batch.repository import BatchRepository
from src.batch.worker_backpressure import WorkerBackpressureMixin
from src.batch.worker_failure_handling import WorkerFailureHandlingMixin
from src.batch.worker_persistence import WorkerPersistenceMixin
from src.batch.worker_runtime_hooks import WorkerRuntimeHooksMixin
from src.batch.worker_types import _PreparedChatItem, _PreparedEmbeddingItem, BatchWorkerConfig
from src.batch.work_unit_isolation import run_isolated_work_unit
from src.metrics import set_batch_worker_saturation
from src.models.errors import InvalidRequestError

logger = logging.getLogger(__name__)


class BatchExecutionEngine(
    AccountingProviderExecutionMixin,
    EmbeddingGroupLifecycleMixin,
    EmbeddingWorkerExecutionMixin,
    ChatWorkerExecutionMixin,
    WorkerBackpressureMixin,
    WorkerFailureHandlingMixin,
    WorkerPersistenceMixin,
    WorkerRuntimeHooksMixin,
):
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
        native_billing: NativeBatchBilling | None = None,
    ) -> None:
        self.app = app
        self.repository = repository
        self.config = config
        self.native_billing = native_billing
        self._normalize_persisted_embedding_response_body = (
            normalize_persisted_embedding_response_body
        )
        self._execute_embedding = execute_embedding
        self._execute_chat = execute_chat
        self._record_router_usage = record_router_usage
        self._observe_batch_item_execution_latency = observe_item_execution_latency
        self._start_heartbeat_fn = start_heartbeat
        self._stop_heartbeat_fn = stop_heartbeat
        self._prepared_item_overrides: dict[str, _PreparedEmbeddingItem | _PreparedChatItem] = {}
        # Shared by every slice this worker runs at once: the pod never has more
        # than worker_concurrency units, or one deployment's max_in_flight answers.
        self.execution_slots = BatchExecutionSlots(
            capacity=lambda: self.config.worker_concurrency,
            on_change=self._publish_worker_saturation,
        )
        self.chat_attempt_counts = ChatAttemptCounts()

    def _publish_worker_saturation(self, active: int, capacity: int) -> None:
        set_batch_worker_saturation(
            worker_id=self.config.worker_id, active=active, capacity=capacity
        )

    async def prepare_item_for_execution(
        self, job, item
    ) -> _PreparedEmbeddingItem | _PreparedChatItem:  # noqa: ANN001
        if job.endpoint == BATCH_ENDPOINT_CHAT_COMPLETIONS:
            return await self.prepare_chat_item_for_execution(job, item)
        if job.endpoint == BATCH_ENDPOINT_EMBEDDINGS:
            return await self.prepare_embedding_item_for_execution(job, item)
        raise InvalidRequestError(message=f"Unsupported batch endpoint '{job.endpoint}'")

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
        prepare_item: Callable[[Any, Any], Awaitable[_PreparedEmbeddingItem | _PreparedChatItem]]
        | None = None,
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
                    exc=InvalidRequestError(
                        message="Prepared batch chat item has an invalid execution shape"
                    ),
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
                exc=InvalidRequestError(
                    message="Prepared embedding batch item has an invalid execution shape"
                ),
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
        prepare_item: Callable[[Any, Any], Awaitable[_PreparedEmbeddingItem | _PreparedChatItem]]
        | None = None,
        process_item: Callable[[Any, Any], Awaitable[None]] | None = None,
    ) -> None:  # noqa: ANN001
        if not items:
            self.execution_slots.publish()
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
            logger.info(
                "batch non-microbatch item processing started batch_id=%s endpoint=%s claimed_items=%s",
                job.batch_id,
                job.endpoint,
                len(items),
            )

            async def _runner() -> None:
                while True:
                    async with queue_lock:
                        if not raw_items:
                            return
                        item = raw_items.popleft()

                    async with self.execution_slots.unit(items=1):
                        await run_isolated_work_unit(
                            partial(process_item_fn, job, item),
                            batch_id=job.batch_id,
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
