from __future__ import annotations

import asyncio
from collections import deque
import contextlib
from dataclasses import dataclass
from datetime import UTC, datetime
import logging
import random
from time import perf_counter
from typing import Any, Awaitable, Iterator

from src.batch.endpoints import batch_call_type_for_endpoint
from src.batch.policy import BatchPolicyLease, acquire_batch_policy_lease, release_batch_policy_lease
from src.batch.worker_types import BatchItemLeaseLostError, _PreparedChatItem, _PreparedEmbeddingItem
from src.billing.tier_pricing import (
    PricingResolution,
    TokenBillingResolution,
    resolve_deployment_tier_pricing,
    resolve_token_billing_result,
)
from src.rate_limit_lease_refresh import RateLimitLeaseRefresher

logger = logging.getLogger(__name__)
_POLICY_RELEASE_RETRY_DRAIN_LIMIT = 16
_POLICY_RELEASE_RETRY_QUEUE_LIMIT = 1024
_POLICY_RELEASE_RETRY_INITIAL_SECONDS = 0.5
_POLICY_RELEASE_RETRY_MAX_SECONDS = 30.0


@dataclass(frozen=True, slots=True)
class _PolicyReleaseRetry:
    lease: BatchPolicyLease
    attempt_count: int
    next_attempt_at: float


@dataclass(frozen=True, slots=True)
class _BatchItemCosts:
    billed_cost: float
    provider_cost: float
    pricing: PricingResolution
    customer_billing: TokenBillingResolution
    provider_billing: TokenBillingResolution

    def __iter__(self) -> Iterator[float | PricingResolution]:
        """Preserve the historical three-value internal unpacking contract."""
        yield self.billed_cost
        yield self.provider_cost
        yield self.pricing


def _policy_release_retry_delay_seconds(attempt_count: int) -> float:
    exponential_delay = _POLICY_RELEASE_RETRY_INITIAL_SECONDS * (2 ** max(0, int(attempt_count)))
    capped_delay = min(_POLICY_RELEASE_RETRY_MAX_SECONDS, exponential_delay)
    jitter = random.uniform(0.0, capped_delay * 0.2)
    return capped_delay + jitter


class WorkerPersistenceMixin:
    def _policy_release_retry_queue(self) -> deque[_PolicyReleaseRetry]:
        queue = getattr(self, "_pending_policy_release_retries", None)
        if queue is None:
            queue = deque()
            self._pending_policy_release_retries = queue
        return queue

    def _queue_policy_lease_release_retry(
        self,
        lease: BatchPolicyLease,
        *,
        attempt_count: int = 0,
    ) -> None:
        if not lease.rate_limit_lease.pending_parallel_acquisitions:
            return
        delay_seconds = _policy_release_retry_delay_seconds(attempt_count)
        entry = _PolicyReleaseRetry(
            lease=lease,
            attempt_count=attempt_count,
            next_attempt_at=perf_counter() + delay_seconds,
        )
        self._insert_policy_release_retry(entry)

    def _insert_policy_release_retry(self, entry: _PolicyReleaseRetry) -> None:
        queue = self._policy_release_retry_queue()
        if len(queue) >= _POLICY_RELEASE_RETRY_QUEUE_LIMIT:
            logger.error(
                "batch policy release retry queue full pending=%s",
                len(queue),
            )
            return
        for index, queued in enumerate(queue):
            if entry.next_attempt_at < queued.next_attempt_at:
                queue.insert(index, entry)
                return
        queue.append(entry)

    async def _drain_policy_lease_release_retries(
        self,
        *,
        max_releases: int = _POLICY_RELEASE_RETRY_DRAIN_LIMIT,
    ) -> None:
        queue = self._policy_release_retry_queue()
        if not queue:
            return
        attempts = 0
        now = perf_counter()
        max_attempts = max(0, int(max_releases))
        while queue and attempts < max_attempts and queue[0].next_attempt_at <= now:
            retry = queue.popleft()
            released = await release_batch_policy_lease(app=self.app, lease=retry.lease)
            attempts += 1
            if not released:
                self._queue_policy_lease_release_retry(
                    retry.lease,
                    attempt_count=retry.attempt_count + 1,
                )

    def _resolve_batch_item_pricing(
        self,
        *,
        prepared: _PreparedEmbeddingItem | _PreparedChatItem,
        served_deployment: Any,
    ) -> PricingResolution:
        return resolve_deployment_tier_pricing(
            auth=prepared.policy_auth,
            model=prepared.payload.model,
            deployment=served_deployment,
            tier_policy_service=getattr(self.app.state, "tier_policy_service", None),
            mode="batch",
        )

    def _batch_item_costs(
        self,
        *,
        prepared: _PreparedEmbeddingItem | _PreparedChatItem,
        usage: dict[str, Any],
        served_deployment: Any,
    ) -> _BatchItemCosts:
        pricing = self._resolve_batch_item_pricing(
            prepared=prepared,
            served_deployment=served_deployment,
        )
        customer_billing = resolve_token_billing_result(
            pricing,
            model=prepared.payload.model,
            usage=usage,
            mode="batch",
        )
        provider_billing = resolve_token_billing_result(
            pricing,
            model=prepared.payload.model,
            usage=usage,
            mode="sync",
            pricing_view="provider",
        )
        return _BatchItemCosts(
            billed_cost=customer_billing.billing.cost,
            provider_cost=provider_billing.billing.cost,
            pricing=pricing,
            customer_billing=customer_billing,
            provider_billing=provider_billing,
        )

    def _observe_item_execution_latency(
        self, *, status: str, latency_seconds: float, reference: str
    ) -> None:
        try:
            self._observe_batch_item_execution_latency(
                status=status, latency_seconds=latency_seconds
            )
        except Exception as exc:
            logger.warning(
                "batch item latency metric publish failed reference=%s status=%s error=%s",
                reference,
                status,
                exc,
            )

    def _observe_prepared_item_lease_lost(
        self,
        prepared: _PreparedEmbeddingItem | _PreparedChatItem,
    ) -> None:
        self._observe_item_execution_latency(
            status="lease_lost",
            latency_seconds=perf_counter() - prepared.started_at_monotonic,
            reference=prepared.item.item_id,
        )

    def _observe_prepared_items_lease_lost(
        self,
        prepared_items: list[_PreparedEmbeddingItem] | list[_PreparedChatItem],
    ) -> None:
        for prepared in prepared_items:
            self._observe_prepared_item_lease_lost(prepared)

    async def _acquire_prepared_policy_lease(self, *, prepared: _PreparedEmbeddingItem | _PreparedChatItem) -> None:
        if prepared.policy_lease is not None:
            return
        if prepared.policy_auth is None:
            return
        prepared.policy_lease = await acquire_batch_policy_lease(
            app=self.app,
            payload=prepared.payload,
            auth=prepared.policy_auth,
        )
        self._start_prepared_policy_lease_refresher(prepared)

    async def _release_prepared_policy_lease(self, prepared: _PreparedEmbeddingItem | _PreparedChatItem) -> None:
        await self._stop_prepared_policy_lease_refresher(prepared)
        lease = prepared.policy_lease
        if lease is None:
            return
        released = await release_batch_policy_lease(app=self.app, lease=lease)
        if released:
            prepared.policy_lease = None
            return
        self._queue_policy_lease_release_retry(lease)
        prepared.policy_lease = None

    def _start_prepared_policy_lease_refresher(self, prepared: _PreparedEmbeddingItem | _PreparedChatItem) -> None:
        lease = prepared.policy_lease
        limiter = getattr(getattr(self.app, "state", None), "limit_counter", None)
        if lease is None or limiter is None:
            return
        refresher = RateLimitLeaseRefresher(
            limiter=limiter,
            lease=lease.rate_limit_lease,
        )
        if refresher.start():
            prepared.policy_lease_refresher = refresher

    async def _stop_prepared_policy_lease_refresher(self, prepared: _PreparedEmbeddingItem | _PreparedChatItem) -> None:
        refresher = getattr(prepared, "policy_lease_refresher", None)
        if refresher is None:
            return
        prepared.policy_lease_refresher = None
        await refresher.stop()

    async def _release_prepared_policy_leases(self, prepared_items: list[_PreparedEmbeddingItem] | list[_PreparedChatItem]) -> None:
        for prepared in prepared_items:
            await self._release_prepared_policy_lease(prepared)

    async def _renew_item_lease_once(self, item_id: str, *, claim_epoch: int | None = None) -> bool:
        try:
            return await self.repository.renew_item_lease(
                item_id=item_id,
                worker_id=self.config.worker_id,
                lease_seconds=self.config.item_lease_seconds,
                claim_epoch=claim_epoch,
            )
        except Exception as exc:
            logger.warning(
                "batch item lease renewal before persistence failed item_id=%s error=%s",
                item_id,
                exc,
                exc_info=True,
            )
            return False

    async def _await_with_lease_loss_cancellation(
        self,
        awaitable: Awaitable[Any],
        *,
        lease_lost_event: asyncio.Event,
        label: str,
    ) -> Any:
        if lease_lost_event.is_set():
            raise BatchItemLeaseLostError(f"batch item lease lost before provider call target={label}")

        provider_task = asyncio.ensure_future(awaitable)
        lease_task = asyncio.create_task(lease_lost_event.wait())
        try:
            await asyncio.wait(
                {provider_task, lease_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if lease_lost_event.is_set():
                provider_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await provider_task
                raise BatchItemLeaseLostError(
                    f"batch item lease lost during provider call target={label}"
                )
            return await provider_task
        finally:
            lease_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await lease_task
            if not provider_task.done():
                provider_task.cancel()

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
        pricing_metadata: dict[str, Any] | None = None,
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
        if pricing_metadata:
            payload["pricing_metadata"] = dict(pricing_metadata)
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
                item_claim_epochs={
                    str(item["item_id"]): int(item["claim_epoch"])
                    for item in items
                    if item.get("claim_epoch") is not None
                },
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
