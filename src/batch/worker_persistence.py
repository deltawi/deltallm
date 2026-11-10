from __future__ import annotations

import asyncio
from collections import deque
import contextlib
import inspect
from dataclasses import dataclass
from datetime import UTC, datetime
import logging
import random
from time import perf_counter
from typing import Any, Awaitable, Iterator, Sequence

from src.batch.endpoints import batch_call_type_for_endpoint
from src.batch.models import BatchItemRecord
from src.batch.result_save_waits import BusyWait, ResultSaveWaits, known_lease_seconds
from src.batch.retry import is_gateway_busy
from src.batch.policy import (
    BatchPolicyLease,
    acquire_batch_policy_lease,
    release_batch_policy_lease,
)
from src.batch.worker_types import (
    BatchItemLeaseLostError,
    _PreparedChatItem,
    _PreparedEmbeddingItem,
)
from src.batch.write_groups import WriteGroups
from src.billing.pricing.tier_pricing import (
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
# After the provider has answered, a busy database must not throw the answer
# away: wait and try the lease renewal or the requeue again before giving up.
# The result save waits longer (src/batch/result_save_waits.py).
_GATEWAY_BUSY_SAVE_DELAYS_SECONDS = (0.5, 1.0, 2.0)
# Rows one grouped result save may write, across all its members.
RESULT_SAVE_GROUP_MAX_ROWS = 50
_SAVED_RESULTS = frozenset({"completed", "already_completed"})


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


def _shares_an_item(group: Sequence[list[dict[str, Any]]], rows: list[dict[str, Any]]) -> bool:
    ids = {str(row["item_id"]) for row in rows}
    return any(str(row["item_id"]) in ids for member in group for row in member)


def _policy_release_retry_delay_seconds(attempt_count: int) -> float:
    exponential_delay = _POLICY_RELEASE_RETRY_INITIAL_SECONDS * (2 ** max(0, int(attempt_count)))
    capped_delay = min(_POLICY_RELEASE_RETRY_MAX_SECONDS, exponential_delay)
    jitter = random.uniform(0.0, capped_delay * 0.2)
    return capped_delay + jitter


class WorkerPersistenceMixin:
    # Set once the worker starts its shutdown drain (start_draining).
    _draining: asyncio.Event | None = None

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
        if self.native_billing is not None:
            pricing = self._require_native_execution(prepared).pricing
            if pricing is None:
                from src.batch.accounting_checkpoint import BatchAccountingUnavailable

                raise BatchAccountingUnavailable()
            return pricing
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

    async def _acquire_prepared_policy_lease(
        self, *, prepared: _PreparedEmbeddingItem | _PreparedChatItem
    ) -> None:
        if prepared.policy_lease is not None:
            return
        if prepared.policy_auth is None:
            return
        prepared.policy_lease = await acquire_batch_policy_lease(
            app=self.app,
            payload=prepared.payload,
            auth=prepared.policy_auth,
        )
        if isinstance(prepared, _PreparedChatItem) and prepared.policy_lease is not None:
            prepared.request_shim.state.output_token_context = (
                prepared.policy_lease.rate_limit_lease.output_context
            )
        self._start_prepared_policy_lease_refresher(prepared)

    async def _release_prepared_policy_lease(
        self, prepared: _PreparedEmbeddingItem | _PreparedChatItem
    ) -> None:
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

    def _start_prepared_policy_lease_refresher(
        self, prepared: _PreparedEmbeddingItem | _PreparedChatItem
    ) -> None:
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

    async def _stop_prepared_policy_lease_refresher(
        self, prepared: _PreparedEmbeddingItem | _PreparedChatItem
    ) -> None:
        refresher = getattr(prepared, "policy_lease_refresher", None)
        if refresher is None:
            return
        prepared.policy_lease_refresher = None
        await refresher.stop()

    async def _release_prepared_policy_leases(
        self, prepared_items: list[_PreparedEmbeddingItem] | list[_PreparedChatItem]
    ) -> None:
        for prepared in prepared_items:
            await self._release_prepared_policy_lease(prepared)

    async def _still_owns_item_for_save(
        self,
        prepared: _PreparedEmbeddingItem | _PreparedChatItem,
        *,
        lease_lost: asyncio.Event,
    ) -> bool:
        """False when the item is known lost; with grouping the save fences the rest."""
        if lease_lost.is_set():
            return False
        if getattr(self.config, "write_grouping_enabled", False):
            # The result save checks the same owner, claim epoch and status in
            # its own transaction, so a separate check first is one more query.
            return True
        return await self._renew_item_lease_once(
            prepared.item.item_id, claim_epoch=prepared.item.claim_epoch
        )

    def _result_save_groups(self) -> WriteGroups[list[dict[str, Any]], str] | None:
        limit = int(getattr(self.config, "result_save_concurrency", 0) or 0)
        if limit <= 0 or not getattr(self.config, "write_grouping_enabled", False):
            return None
        groups = getattr(self, "_result_saves", None)
        if groups is None:
            groups = WriteGroups(
                kind="result_save",
                limit=limit,
                write=self._save_result_group,
                accepted=lambda result: result in _SAVED_RESULTS,
                max_rows=RESULT_SAVE_GROUP_MAX_ROWS,
                rows=len,
                conflicts=_shares_an_item,
            )
            self._result_saves = groups
        return groups

    async def _save_result_rows(
        self, items: list[dict[str, Any]], *, skip_queue: bool = False
    ) -> str:
        groups = None if skip_queue else self._result_save_groups()
        if groups is None:
            return await self.repository.complete_items_with_outbox_bulk(
                items=items,
                worker_id=self.config.worker_id,
            )
        return await groups.run(items)

    async def _save_result_group(self, members: Sequence[list[dict[str, Any]]]) -> str:
        if len(members) == 1:
            return await self.repository.complete_items_with_outbox_bulk(
                items=members[0],
                worker_id=self.config.worker_id,
            )
        # A group that is not accepted is split and saved again alone, and only
        # those answers count as duplicate or lost completions.
        return await self.repository.complete_items_with_outbox_bulk(
            items=[row for member in members for row in member],
            worker_id=self.config.worker_id,
            count_rejections=False,
        )

    async def _renew_item_lease_once(self, item_id: str, *, claim_epoch: int | None = None) -> bool:
        busy_delays = iter(_GATEWAY_BUSY_SAVE_DELAYS_SECONDS)
        while True:
            try:
                return await self.repository.renew_item_lease(
                    item_id=item_id,
                    worker_id=self.config.worker_id,
                    lease_seconds=self.config.item_lease_seconds,
                    claim_epoch=claim_epoch,
                )
            except Exception as exc:
                delay = next(busy_delays, None) if is_gateway_busy(exc) else None
                logger.warning(
                    "batch item lease renewal before persistence failed item_id=%s retry_in_seconds=%s error=%s",
                    item_id,
                    delay,
                    exc,
                    exc_info=delay is None,
                )
                if delay is None:
                    return False
                await asyncio.sleep(delay)

    async def _await_with_lease_loss_cancellation(
        self,
        awaitable: Awaitable[Any],
        *,
        lease_lost_event: asyncio.Event,
        label: str,
    ) -> Any:
        if lease_lost_event.is_set():
            if inspect.iscoroutine(awaitable):
                awaitable.close()
            raise BatchItemLeaseLostError(
                f"batch item lease lost before provider call target={label}"
            )

        provider_task = asyncio.ensure_future(awaitable)
        lease_task = asyncio.create_task(lease_lost_event.wait())
        try:
            await asyncio.wait(
                {provider_task, lease_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if lease_lost_event.is_set():
                provider_task.cancel()
                await asyncio.gather(provider_task, return_exceptions=True)
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
            await asyncio.gather(provider_task, return_exceptions=True)

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
            "owner_account_id": job.created_by_owner_account_id,
            "owner_snapshot_complete": job.created_by_owner_snapshot_complete,
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
        if isinstance(prepared, _PreparedChatItem) and prepared.selector is not None:
            payload["billing_event_id"] = str(prepared.selector.operation_id)
        if self.native_billing is not None:
            return self._require_native_execution(prepared).completion_payload(payload, usage)
        return payload

    def start_draining(self) -> None:
        """Stop long result-save waits: the shutdown drain must cover the save.

        Short waits and the requeue keep their bounded waits, so an answer that
        is still not saved is given back to the queue before the drain ends.
        """
        self._drain_signal().set()

    def _drain_signal(self) -> asyncio.Event:
        signal = self._draining
        if signal is None:
            signal = self._draining = asyncio.Event()
        return signal

    async def _wait_before_result_save_retry(
        self, waits: ResultSaveWaits, items: list[dict[str, Any]]
    ) -> bool:
        """Wait before the next result save; False when the save stops waiting."""
        wait = waits.next_wait()
        if wait is None:
            return False
        if not wait.long:
            await asyncio.sleep(wait.seconds)
            return True
        if self._drain_signal().is_set():
            return False
        if not waits.covers(wait):
            # No long wait without a lease known to cover it: when the renewal
            # fails or the lease is too short for one, the save is retried at
            # once, as before the long waits. When it succeeds, the database
            # answered: try the save again now.
            return waits.can_renew() and await self._renew_for_long_wait(waits, items)
        await self._sleep_unless_draining(wait)
        waits.long_wait_done()
        if (
            self._result_save_groups() is not None
            and waits.shorter_than_main()
            and waits.can_renew()
            and not self._drain_signal().is_set()
        ):
            # The save can wait in the save queue before it reaches the
            # database. Renew first, so that it goes in with as much lease as
            # a save on main. If the renewal is refused, the save skips the
            # queue: the reserve left after the renewal covers one save call,
            # not a wait for a save slot.
            await self._renew_for_long_wait(waits, items)
        return True

    async def _renew_for_long_wait(
        self, waits: ResultSaveWaits, items: list[dict[str, Any]]
    ) -> bool:
        started_at = waits.now()
        if not await self._renew_unsaved_item_leases(items):
            return False
        waits.lease_renewed(started_at)
        return True

    async def _items_still_claimed(self, items: list[dict[str, Any]]) -> bool:
        """True when this worker still holds every item at its claim epoch.

        A failed read raises: only a read that the database answered can show
        that another worker or claim holds an item.
        """
        epochs = {str(item["item_id"]): item.get("claim_epoch") for item in items}
        records = await self.repository.list_items_by_ids(list(epochs))
        return len(records) == len(epochs) and all(
            record.status == "in_progress"
            and record.locked_by == self.config.worker_id
            and epochs.get(str(record.item_id)) is not None
            and int(record.claim_epoch) == int(epochs[str(record.item_id)])
            for record in records
        )

    async def _renew_unsaved_item_leases(self, items: list[dict[str, Any]]) -> bool:
        """True when this worker still owns every item and renewed its lease."""
        claims = {
            str(item["item_id"]): int(item["claim_epoch"])
            for item in items
            if item.get("claim_epoch") is not None
        }
        if len(claims) != len(items):
            return False
        try:
            owned = await self.repository.renew_item_leases(
                item_claim_epochs=claims,
                worker_id=self.config.worker_id,
                lease_seconds=self.config.item_lease_seconds,
            )
        except Exception as exc:
            logger.warning(
                "batch completion persistence lease renewal failed item_ids=%s error=%s",
                list(claims),
                exc,
                exc_info=not is_gateway_busy(exc),
            )
            return False
        return set(owned) == set(claims)

    async def _sleep_unless_draining(self, wait: BusyWait) -> None:
        sleeper = asyncio.ensure_future(asyncio.sleep(wait.seconds))
        drained = asyncio.ensure_future(self._drain_signal().wait())
        try:
            await asyncio.wait({sleeper, drained}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            sleeper.cancel()
            drained.cancel()
            await asyncio.gather(sleeper, drained, return_exceptions=True)

    async def _requeue_unsaved_items(
        self,
        *,
        items: list[dict[str, Any]],
        item_ids: list[str],
        context_label: str,
    ) -> list[str] | None:
        """Give unsaved items back to the queue; None when the database refused.

        An item that is not given back stays claimed until its lease expires.
        So a busy database is waited out here too. The release checks the owner
        and claim epoch, so a repeat after a lost reply changes nothing.
        """
        busy_delays = iter(_GATEWAY_BUSY_SAVE_DELAYS_SECONDS)
        while True:
            try:
                return await self.repository.release_items_for_retry(
                    item_ids=item_ids,
                    worker_id=self.config.worker_id,
                    item_claim_epochs={
                        str(item["item_id"]): int(item["claim_epoch"])
                        for item in items
                        if item.get("claim_epoch") is not None
                    },
                )
            except Exception as exc:
                delay = next(busy_delays, None) if is_gateway_busy(exc) else None
                logger.warning(
                    "batch completion persistence requeue failed context=%s item_ids=%s retry_in_seconds=%s error=%s",
                    context_label,
                    item_ids,
                    delay,
                    exc,
                    exc_info=delay is None,
                )
                if delay is None:
                    return None
                await asyncio.sleep(delay)

    async def _persist_claimed_completions(
        self,
        *,
        items: list[dict[str, Any]],
        claimed: Sequence[BatchItemRecord],
        context_label: str,
    ) -> bool:
        """Save the answers of claimed items, with the lease ends of their claims."""
        return await self._persist_completion_rows_with_outbox(
            items=items,
            item_ids=[item.item_id for item in claimed],
            context_label=context_label,
            lease_ends=[item.lease_expires_at for item in claimed],
        )

    async def _persist_completion_rows_with_outbox(
        self,
        *,
        items: list[dict[str, Any]],
        item_ids: list[str],
        context_label: str,
        lease_ends: Sequence[datetime | None] = (),
    ) -> bool:
        """Save provider answers; requeue them when the save keeps failing.

        ``lease_ends`` are the lease ends that the items' claims returned.
        They tell how long the leases are known to last without a renewal.
        """
        waits = ResultSaveWaits(
            self.config.item_lease_seconds,
            heartbeat_seconds=self.config.heartbeat_interval_seconds,
            call_seconds=getattr(self.config, "database_call_seconds", 0.0),
            known_lease_seconds=known_lease_seconds(lease_ends),
        )
        failures = 0
        attempt = 0
        while failures < 2:
            attempt += 1
            try:
                result = await self._save_result_rows(items, skip_queue=waits.skips_save_queue())
                if result == "not_owned" and await self._items_still_claimed(items):
                    # The save skipped an item row that another statement held
                    # (FOR UPDATE SKIP LOCKED), such as a lease renewal that
                    # ran on after its caller stopped waiting.
                    result = "skipped_held_row"
            except Exception as exc:
                logger.warning(
                    "batch completion persistence attempt failed context=%s item_ids=%s attempt=%s error=%s",
                    context_label,
                    item_ids,
                    attempt,
                    exc,
                    exc_info=True,
                )
                # A busy database is waited out before it counts as a
                # failure, also when it refuses the ownership read after a
                # skipped row. The save is idempotent, so a retry after an
                # uncertain commit returns already_completed.
                if not (
                    is_gateway_busy(exc) and await self._wait_before_result_save_retry(waits, items)
                ):
                    failures += 1
                continue

            if result in {"completed", "already_completed"}:
                return True
            if result == "skipped_held_row":
                # The items are still ours: wait and save again.
                if not await self._wait_before_result_save_retry(waits, items):
                    failures += 1
                continue
            if result == "not_owned":
                logger.warning(
                    "batch completion persistence lost ownership context=%s item_ids=%s",
                    context_label,
                    item_ids,
                )
                return False
            failures += 1

        requeued_item_ids = await self._requeue_unsaved_items(
            items=items, item_ids=item_ids, context_label=context_label
        )
        if requeued_item_ids is None:
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
