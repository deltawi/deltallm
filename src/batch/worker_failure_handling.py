from __future__ import annotations

import logging
from math import ceil
import random
from time import perf_counter
from typing import Any

from src.batch.backpressure import BatchModelGroupDeferred
from src.batch.models import BatchItemRecord, BatchJobRecord
from src.batch.retry import (
    BatchRetryCategory,
    BatchRetryDecision,
    BatchRetryTerminalReason,
    classify_batch_retry,
)
from src.metrics import (
    increment_batch_item_retry,
    increment_batch_item_terminal_failure,
    observe_batch_item_retry_delay,
)

logger = logging.getLogger(__name__)


class WorkerFailureHandlingMixin:
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
            logger.warning(
                "batch item failure update skipped after lease loss batch_id=%s item_id=%s",
                batch_id,
                item.item_id,
            )
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
        log_failure = (
            logger.warning if decision.category is BatchRetryCategory.UNKNOWN else logger.info
        )
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
