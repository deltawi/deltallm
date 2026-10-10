"""Own cleanup across every phase of a bounded embedding group."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from src.batch.models import BatchItemRecord, BatchJobRecord
from src.batch.worker_types import _PreparedEmbeddingItem


class EmbeddingGroupLifecycleMixin:
    async def _execute_prepared_microbatch_chunk(
        self,
        job: BatchJobRecord,
        prepared_items: list[_PreparedEmbeddingItem],
        *,
        process_item: Callable[[BatchJobRecord, BatchItemRecord], Awaitable[None]],
    ) -> None:
        prepared_items = await self._acquire_embedding_policy_leases_for_chunk(
            job=job, prepared_items=prepared_items
        )
        if not prepared_items:
            return
        if len(prepared_items) == 1:
            await self._execute_prepared_item(job, prepared_items[0])
            return
        item_heartbeats: dict[str, asyncio.Task[None]] = {}
        try:
            await self._execute_owned_embedding_chunk(
                job, prepared_items, process_item=process_item, item_heartbeats=item_heartbeats
            )
        finally:
            await self._close_native_batch_items(prepared_items)
            await self._stop_heartbeat_tasks(item_heartbeats.values())
            await self._release_prepared_policy_leases(prepared_items)
