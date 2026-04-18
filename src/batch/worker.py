from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime
from typing import Any

from src.batch.models import BatchJobStatus
from src.batch.repository import BatchRepository
from src.batch.storage import BatchArtifactStorage
from src.batch.worker_artifacts import BatchArtifactFinalizer
from src.batch.worker_execution import BatchExecutionEngine
from src.batch.worker_types import (
    BatchArtifactValidationError,
    BatchWorkerConfig,
    _PreparedEmbeddingItem,
    _RequestShim,
)
from src.metrics import (
    observe_batch_item_execution_latency,
    publish_batch_runtime_summary,
    set_batch_worker_saturation,
)
from src.router.usage import record_router_usage
from src.routers.embeddings import _execute_embedding

logger = logging.getLogger(__name__)

__all__ = [
    "BatchArtifactValidationError",
    "BatchExecutorWorker",
    "BatchWorkerConfig",
    "_PreparedEmbeddingItem",
    "_RequestShim",
]


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
        self._artifact_finalizer = BatchArtifactFinalizer(
            repository=repository,
            storage=storage,
            config=config,
        )
        self._execution_engine = BatchExecutionEngine(
            app=app,
            repository=repository,
            config=config,
            normalize_persisted_embedding_response_body=self._artifact_finalizer.normalize_persisted_embedding_response_body,
            execute_embedding=self._call_execute_embedding,
            record_router_usage=self._call_record_router_usage,
            observe_item_execution_latency=self._call_observe_item_execution_latency,
            start_heartbeat=self._call_start_heartbeat,
            stop_heartbeat=self._call_stop_heartbeat,
        )

    def _sync_dependencies(self) -> None:
        self._artifact_finalizer.repository = self.repository
        self._artifact_finalizer.storage = self.storage
        self._artifact_finalizer.config = self.config
        self._execution_engine.app = self.app
        self._execution_engine.repository = self.repository
        self._execution_engine.config = self.config

    async def _call_execute_embedding(self, request, payload, deployment):  # noqa: ANN001, ANN201
        return await _execute_embedding(request, payload, deployment)

    async def _call_record_router_usage(self, router_state_backend, deployment_id: str, *, mode: str, usage: dict) -> None:
        await record_router_usage(
            router_state_backend,
            deployment_id,
            mode=mode,
            usage=usage,
        )

    def _call_observe_item_execution_latency(self, *, status: str, latency_seconds: float) -> None:
        observe_batch_item_execution_latency(status=status, latency_seconds=latency_seconds)

    def _call_start_heartbeat(self, *, renew, label: str) -> asyncio.Task[None]:  # noqa: ANN001
        return self._start_heartbeat(renew=renew, label=label)

    async def _call_stop_heartbeat(self, task: asyncio.Task[None]) -> None:
        await self._stop_heartbeat(task)

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
            set_batch_worker_saturation(
                worker_id=self.config.worker_id,
                active=0,
                capacity=self.config.worker_concurrency,
            )
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
            set_batch_worker_saturation(
                worker_id=self.config.worker_id,
                active=0,
                capacity=self.config.worker_concurrency,
            )
            await self._stop_heartbeat(job_heartbeat)
            await self.repository.release_job_lease(batch_id=job.batch_id, worker_id=self.config.worker_id)

    async def _prepare_item_for_execution(self, job, item) -> _PreparedEmbeddingItem:  # noqa: ANN001
        self._sync_dependencies()
        return await self._execution_engine.prepare_item_for_execution(job, item)

    async def _process_item(self, job, item) -> None:  # noqa: ANN001
        self._sync_dependencies()
        await self._execution_engine.process_item(job, item, prepare_item=self._prepare_item_for_execution)

    async def _process_items(self, job, items) -> None:  # noqa: ANN001
        self._sync_dependencies()
        await self._execution_engine.process_items(
            job,
            items,
            prepare_item=self._prepare_item_for_execution,
            process_item=self._process_item,
        )

    def _resolve_final_status(self, job) -> str:  # noqa: ANN001
        self._sync_dependencies()
        return self._artifact_finalizer.resolve_final_status(job)

    async def _finalize_with_retry(self, job) -> None:  # noqa: ANN001
        self._sync_dependencies()
        await self._artifact_finalizer.finalize_with_retry(job, finalize_artifacts=self._finalize_artifacts)

    async def _iter_output_lines(self, batch_id: str):  # noqa: ANN201
        self._sync_dependencies()
        async for line in self._artifact_finalizer.iter_output_lines(batch_id):
            yield line

    async def _iter_error_lines(self, batch_id: str):  # noqa: ANN201
        self._sync_dependencies()
        async for line in self._artifact_finalizer.iter_error_lines(batch_id):
            yield line

    async def _finalize_artifacts(self, job) -> None:  # noqa: ANN001
        self._sync_dependencies()
        await self._artifact_finalizer.finalize_artifacts(job)

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
